"""Copyable invitations and cookie-backed browser sign-in; no outbound mail."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from slate.api.auth import AdminAuth, DbSession, ReadAuth, check_browser_write
from slate.models import Invitation, Token
from slate.service import accounts, collaboration, permissions
from slate.service.core import snapshot

router = APIRouter(tags=["collaboration"])
COOKIE = "slate_session"


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str = Field(min_length=12, max_length=12)
    handle: str = Field(pattern=accounts.HANDLE_RE.pattern)
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["guest"] = "guest"
    level: Literal["edit"] = "edit"


class AcceptIn(BaseModel):
    secret: str = Field(max_length=100)


class Attempts:
    """Single-process bounded limiter, independent of optional authenticated API limits."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = OrderedDict()
        self.all = deque()

    def take(self, key: str):
        now = time.monotonic()
        with self.lock:
            for queue in (self.all, self.clients.setdefault(key, deque())):
                while queue and queue[0] <= now - 60:
                    queue.popleft()
            own = self.clients[key]
            self.clients.move_to_end(key)
            while len(self.clients) > 1024:
                self.clients.popitem(last=False)
            if len(own) >= 10 or len(self.all) >= 100:
                raise HTTPException(
                    429, "Too many attempts. Try again in a minute.", headers={"Retry-After": "60"}
                )
            own.append(now)
            self.all.append(now)


attempts = Attempts()


def set_session_cookie(response: Response, request: Request, plaintext: str) -> None:
    """The browser-session cookie: HttpOnly, SameSite=Strict, Secure on HTTPS, scoped to the
    API. Its `max_age` is only the browser's ceiling; the token's `expires_at` is what signs a
    guest out. `GET /me` re-sets it on every board load, so an extended session
    (`collaboration.extend`) is not lost to a cookie that aged out on day 31."""
    response.set_cookie(
        COOKIE,
        plaintext,
        max_age=30 * 86400,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
    )


@router.post("/invitations", status_code=201)
def issue(body: InviteIn, auth: AdminAuth, session: DbSession, response: Response) -> dict:
    """Invite a guest person to edit one active private section; secret returned only once."""
    row, secret = collaboration.issue(
        session,
        auth.actor,
        by=auth.principal,
        section_id=body.section_id,
        handle=body.handle,
        display_name=body.display_name,
    )
    response.headers["Cache-Control"] = "no-store"
    return {**snapshot(row), "secret": secret, "role": "guest", "level": "edit"}


def _row(session, row: Invitation) -> dict:
    """The invitation plus when its browser session ends or was revoked (no credential)."""
    token = session.get(Token, row.token_id) if row.token_id else None
    return {
        **snapshot(row),
        "session_expires_at": token.expires_at if token else None,
        "session_revoked_at": token.revoked_at if token else None,
    }


@router.get("/invitations")
def invitations(auth: AdminAuth, session: DbSession, section_id: str) -> list[dict]:
    """Invitation lifecycle for a section (admin only), never credentials or hashes. Each row
    carries `session_expires_at` / `session_revoked_at` of the browser session it issued."""
    permissions._require_manager(auth.principal)
    return [
        _row(session, row)
        for row in session.scalars(
            select(Invitation)
            .where(Invitation.section_id == section_id)
            .order_by(Invitation.created_at.desc())
        )
    ]


@router.post("/invitations/{invitation_id}/extend")
def extend(invitation_id: str, auth: AdminAuth, session: DbSession) -> dict:
    """Push the accepted invitation's browser session out to thirty days from now
    (`session.extended`). 422 before acceptance; 410 once cancelled or signed out."""
    row, _ = collaboration.extend(
        session, auth.actor, by=auth.principal, invitation_id=invitation_id
    )
    return _row(session, row)


@router.post("/invitations/{invitation_id}/reissue", status_code=201)
def reissue(invitation_id: str, auth: AdminAuth, session: DbSession, response: Response) -> dict:
    """A fresh single-use link for the same guest and section, for another device or a link
    opened in the wrong app; the original stays valid. Secret returned only once."""
    row, secret = collaboration.reissue(
        session, auth.actor, by=auth.principal, invitation_id=invitation_id
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        **snapshot(row),
        "secret": secret,
        "role": "guest",
        "level": "edit",
        "reissued_from": invitation_id,
    }


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke(invitation_id: str, auth: AdminAuth, session: DbSession) -> Response:
    """Cancel an invitation and its issued session; remove shares separately to end all access."""
    collaboration.revoke(session, auth.actor, by=auth.principal, invitation_id=invitation_id)
    return Response(status_code=204)


@router.post("/invitations/accept")
def accept(body: AcceptIn, request: Request, response: Response, session: DbSession) -> dict:
    """Consume a single-use link; sign in this browser for thirty days. Not email verification."""
    check_browser_write(request)
    attempts.take(request.client.host if request.client else "unknown")
    row, plaintext = collaboration.accept(session, body.secret)
    set_session_cookie(response, request, plaintext)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return {"section_id": row.section_id, "account_id": row.account_id}


@router.post("/session/logout", status_code=204)
def logout(request: Request, auth: ReadAuth, session: DbSession) -> Response:
    """Revoke the current credential and remove this browser's session cookie."""
    check_browser_write(request)
    accounts.revoke_token(session, auth.actor, token_id=auth.principal.token_id, by=auth.principal)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/api/v1", httponly=True, samesite="strict")
    return response


@router.get("/sections/{section_id}/project")
def project(section_id: str, _: ReadAuth, session: DbSession) -> dict:
    """Section project home metadata and scoped participant identities (no workspace presence)."""
    return collaboration.project(session, section_id)
