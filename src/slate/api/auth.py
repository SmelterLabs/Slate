"""Bearer-token auth for the REST API.

    @router.get("/thing")
    def thing(auth: ReadAuth): ...   # or WriteAuth, AdminAuth, or Depends(require(...))

`require(scope)` reads `Authorization: Bearer slate_<id>_<secret>`, resolves it through the
service layer, and returns an `Auth`: the acting account, the token's scopes, and the `Actor`
to pass to service writes (`session_ref` from the optional `X-Slate-Session-Ref` header). The
source is `ui` when the request carries both `X-Slate-Source: ui` and `X-Slate-Client: browser`
(the board sends both); anything else is `api`. A caller can never claim `mcp` or `system`.
401 when the token is missing or invalid; 403 when its scopes rank below `scope`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from slate.db import get_session, release
from slate.models import Account, Source
from slate.presence import presence
from slate.ratelimit import limiter
from slate.service import access, accounts
from slate.service.accounts import Principal
from slate.service.core import Actor

DbSession = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True)
class Auth:
    principal: Principal
    actor: Actor

    @property
    def account(self) -> Account:
        return self.principal.account


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def check_browser_write(request: Request) -> None:
    """Custom header plus exact same-origin check; never trust forwarded host headers here."""
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if request.headers.get("x-slate-client") != "browser" or (origin and origin != expected):
        raise HTTPException(403, "Same-origin browser request required")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Same-origin browser request required")


def request_source(request: Request, claimed: str | None) -> Source:
    """`ui` only for the board (both headers); every other REST caller is `api`."""
    if claimed == "ui" and request.headers.get("x-slate-client") == "browser":
        return Source.ui
    return Source.api


def require(scope: str) -> Callable[..., Auth]:
    if scope not in accounts.SCOPE_RANK:
        raise ValueError(f"unknown scope {scope!r}")

    async def dependency(
        request: Request,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_slate_session_ref: Annotated[str | None, Header(max_length=200)] = None,
        x_slate_source: Annotated[str | None, Header(max_length=20)] = None,
    ) -> Auth:
        # Async on purpose: a sync dependency costs a worker-thread round trip per request.
        # The lookup is two primary-key reads (no lock in WAL); the once-a-minute
        # `last_used_at` write goes to the threadpool so a busy writer never stalls the loop.
        scheme, _, token = (authorization or "").partition(" ")
        cookie_auth = not authorization
        if cookie_auth:
            token = request.cookies.get("slate_session", "")
            if token and request.method not in ("GET", "HEAD", "OPTIONS"):
                check_browser_write(request)
        elif scheme.lower() != "bearer":
            raise _unauthorized("missing bearer token")
        principal = accounts.authenticate(session, token, cookie=cookie_auth, touch=False)
        if principal is not None and accounts.needs_touch(session, principal.token_id):
            await run_in_threadpool(accounts.touch_token, session, principal.token_id)
        if principal is None:
            raise _unauthorized("invalid or revoked token")
        # slate.fastlane.Timing counts the call for this token on the Health page.
        request.state.caller = (principal.token_id, principal.account.id)
        if not principal.has(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"token lacks '{scope}' scope")
        # The limiter answers before the access computation below: a runaway loop that is
        # being refused must stay cheap.
        verdict = limiter.take(
            principal.token_id, principal.account.id, "read" if scope == "read" else "write"
        )
        # slate.fastlane.Timing copies these onto the response as X-RateLimit-* headers.
        request.state.ratelimit = verdict
        if not verdict.allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit: {verdict.limit} {'reads' if scope == 'read' else 'writes'} "
                f"per minute for this token; retry in {verdict.retry_after} s",
                headers={
                    "Retry-After": str(verdict.retry_after),
                    "X-RateLimit-Limit": str(verdict.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        access.bind(session, principal.account, principal.token_id)
        presence.seen(principal.account.id, "api")
        actor = Actor(
            account_id=principal.account.id,
            source=request_source(request, x_slate_source),
            session_ref=x_slate_session_ref,
        )
        # Hand the connection back before the route runs: a route that then waits (for its
        # upload body, a long-poll, a threadpool slot) must not hold one (slate.db.POOL_SIZE).
        release(session)
        return Auth(principal=principal, actor=actor)

    return dependency


ReadAuth = Annotated[Auth, Depends(require("read"))]
WriteAuth = Annotated[Auth, Depends(require("write"))]
AdminAuth = Annotated[Auth, Depends(require("admin"))]
