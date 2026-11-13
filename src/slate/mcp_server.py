"""Slate's MCP server: one tool per resource, each with an `action` and a `params` object.

Two transports, one tool table (`slate.mcp_tools.TOOLS`):

- **Streamable HTTP at `/mcp`**, inside the FastAPI app (`install()`). Auth is the REST bearer
  token from the `Authorization` header: `BearerGate` answers 401 before MCP sees the request,
  and each call resolves the token again into an `Actor` with `source=mcp`. Tools call the
  service layer directly (`LocalBackend`), exactly as the REST routers do.
- **stdio**, the `slate-mcp` console script (`main()`). A thin client: every call becomes a REST
  request to `SLATE_URL` with `SLATE_TOKEN` (`RestBackend`). It never opens the SQLite file, so a
  laptop can run it against a remote server.

Each `Action` therefore carries two bindings: `local` (service functions, shaped with the REST
response models so both transports return the same JSON) and `rest` (the equivalent REST call).
Results are compact JSON text with null fields dropped; failures are `isError` results carrying
the service layer's plain-English message. See docs/mcp-server.md to add a tool.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import anyio
import httpx
from fastapi import FastAPI
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ValidationError
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from slate import __version__, db, monitor
from slate.mcp_tools import (
    RULE,
    TOOLS,
    ItemRef,
    Local,
    ToolError,
    ToolSpec,
    single_action,
    tool_list,
)
from slate.models import Source
from slate.presence import presence
from slate.ratelimit import limiter
from slate.service import access, accounts
from slate.service.core import Actor
from slate.service.errors import ServiceError
from slate.settings import settings

# The longest session ref an event records, as REST's `X-Slate-Session-Ref` (api/auth.py).
SESSION_REF_MAX = 200

# --- backends -------------------------------------------------------------------------------


class Backend(Protocol):
    async def run(
        self, spec: ToolSpec, name: str, params: BaseModel, session_ref: str | None
    ) -> Any: ...


class LocalBackend:
    """In-process: resolve the bearer token and call the service layer (source `mcp`)."""

    def __init__(self, token: str, session_ref: str | None = None) -> None:
        self.token = token
        self.session_ref = session_ref

    async def run(
        self, spec: ToolSpec, name: str, params: BaseModel, session_ref: str | None
    ) -> Any:
        action = spec.actions[name]
        if action.local is None:
            raise ToolError(spec.missing or "not available")
        return await anyio.to_thread.run_sync(self._run, spec, name, params, session_ref)

    def _run(self, spec: ToolSpec, name: str, params: BaseModel, session_ref: str | None) -> Any:
        action = spec.actions[name]
        with db.SessionLocal() as session:
            principal = accounts.authenticate(session, self.token)
            if principal is None:
                raise ToolError("invalid or revoked token")
            if not principal.has(action.scope):
                raise ToolError(
                    f"this token lacks the '{action.scope}' scope for {spec.name} {name}"
                )
            # Before the access computation, as for REST (api.auth.require).
            verdict = limiter.take(
                principal.token_id,
                principal.account.id,
                "read" if action.scope == "read" else "write",
            )
            route = f"{spec.name} {name}"
            if not verdict.allowed:
                msg = (
                    f"rate limit: {verdict.limit} requests per minute for this token; "
                    f"retry in {verdict.retry_after} s"
                )
                monitor.calls.record(
                    principal.token_id, principal.account.id, route, 0, status=429, door="mcp"
                )
                raise ToolError(msg)
            access.bind(session, principal.account, principal.token_id)
            presence.seen(principal.account.id, "mcp")
            actor = Actor(
                account_id=principal.account.id,
                source=Source.mcp,
                session_ref=session_ref or self.session_ref,
            )
            start = time.perf_counter()
            status, error = 200, None
            try:
                return action.local(Local(session, principal, actor), params)
            except (ToolError, ServiceError) as exc:
                status, error = 400, str(exc)
                raise
            except Exception as exc:
                status, error = 500, f"{type(exc).__name__}: {exc}"
                raise
            finally:
                # The Health page: per-token calls, errors and slow calls (slate.monitor).
                monitor.calls.record(
                    principal.token_id,
                    principal.account.id,
                    route,
                    (time.perf_counter() - start) * 1000,
                    status=status,
                    door="mcp",
                    error=error,
                )


def _error_detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc', []) if x not in ('body', 'query'))}: "
            f"{e.get('msg')}"
            for e in detail
        )
    return str(detail) if detail else f"Slate answered HTTP {resp.status_code}"


class RestBackend:
    """stdio mode: every action is one REST call to SLATE_URL, as the token's account."""

    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.client.headers["Authorization"] = f"Bearer {token}"

    async def run(
        self, spec: ToolSpec, name: str, params: BaseModel, session_ref: str | None
    ) -> Any:
        call = spec.actions[name].rest(params)
        headers = {"X-Slate-Session-Ref": session_ref} if session_ref else None
        try:
            resp = await self.client.request(
                call.method,
                "/api/v1" + call.path,
                params=call.query,
                json=call.body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"could not reach Slate at {self.client.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ToolError(_error_detail(resp))
        if resp.status_code == 204 or not resp.content:
            return call.empty
        return resp.json()


# --- the server ------------------------------------------------------------------------------

GetBackend = Callable[[Any], Backend]


def compact(value: Any) -> Any:
    """Drop null fields everywhere: agents read fewer tokens; absent means null."""
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def _text(value: Any) -> str:
    return json.dumps(compact(value), separators=(",", ":"), ensure_ascii=False)


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(x) for x in e['loc']) or 'params'}: {e['msg']}" for e in exc.errors()
    )


def _parse_call(spec: ToolSpec, args: Mapping[str, Any]) -> tuple[str, BaseModel, str | None]:
    extra = set(args) - {"action", "params", "session_ref"}
    if extra:
        raise ToolError(
            f"unknown argument(s) {sorted(extra)}: put the action's fields inside `params`"
        )
    if single_action(spec):
        name = next(iter(spec.actions))
    else:
        name = args.get("action")
        if not name:
            raise ToolError(f"action is required: one of {', '.join(spec.actions)}")
        if name not in spec.actions:
            raise ToolError(
                f"unknown action '{name}' for {spec.name}; use one of {', '.join(spec.actions)}"
            )
    raw = args.get("params") or {}
    if not isinstance(raw, dict):
        raise ToolError("params must be an object")
    try:
        params = spec.actions[name].params.model_validate(raw)
    except ValidationError as exc:
        raise ToolError(_validation_message(exc)) from None
    session_ref = args.get("session_ref")
    if session_ref is not None and (
        not isinstance(session_ref, str) or len(session_ref) > SESSION_REF_MAX
    ):
        raise ToolError(f"session_ref must be a string of at most {SESSION_REF_MAX} characters")
    return name, params, session_ref


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


def build_server(get_backend: GetBackend) -> Server:
    """The MCP server object. `get_backend(ctx)` picks local (HTTP) or REST (stdio) per call."""

    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_list())

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        spec = TOOLS.get(params.name)
        if spec is None:
            return _error(f"unknown tool '{params.name}'; tools: {', '.join(TOOLS)}")
        try:
            name, model, session_ref = _parse_call(spec, params.arguments or {})
            result = await get_backend(ctx).run(spec, name, model, session_ref)
        except (ToolError, ServiceError) as exc:
            return _error(str(exc))
        return types.CallToolResult(content=[types.TextContent(type="text", text=_text(result))])

    async def list_resources(_ctx, _params) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    name="today",
                    title="Slate: today",
                    uri="slate://today",
                    description="Overdue, due today and waiting on you, as markdown",
                    mime_type="text/markdown",
                )
            ]
        )

    async def list_templates(_ctx, _params) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(
                    name="item",
                    title="Slate item",
                    uri_template="slate://item/{id}",
                    description="One item with subtasks, comments and recent history, as markdown",
                    mime_type="text/markdown",
                )
            ]
        )

    async def read_resource(
        ctx, params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        uri = str(params.uri)
        backend = get_backend(ctx)
        try:
            if uri == "slate://today":
                spec, name = TOOLS["slate_today"], "get"
                model = spec.actions[name].params()
                render: Callable[[Any], str] = render_today
            elif uri.startswith("slate://item/") and uri.count("/") == 3:
                spec, name, model = TOOLS["slate_item"], "get", ItemRef(id=uri.rsplit("/", 1)[1])
                render = render_item
            else:
                raise ValueError(f"unknown resource {uri}")
            text = render(await backend.run(spec, name, model, None))
        except (ToolError, ServiceError) as exc:
            raise ValueError(str(exc)) from None
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, mime_type="text/markdown", text=text)]
        )

    return Server(
        "slate",
        version=__version__,
        title="Slate",
        instructions=(
            "Slate is the workspace's task board. Sections hold lists, lists hold items; a list's "
            f"statuses are its columns. {RULE} Find ids with slate_section/slate_list list "
            "before filing; assignees are account handles. Start each run with slate_checkin "
            "(your open work and what changed for you); while you work an item, slate_item "
            "report shows people a live badge; many changes go in one slate_batch."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_templates,
        on_read_resource=read_resource,
    )


# --- markdown resources ---------------------------------------------------------------------


def _when(value: str | None) -> str:
    if not value:
        return ""
    at = datetime.fromisoformat(value).astimezone(ZoneInfo(settings.timezone))
    return at.strftime("%a %b %d %Y %H:%M %Z")


def _item_line(item: dict[str, Any]) -> str:
    bits = [f"**{item['title']}**"]
    if item.get("priority"):
        bits.append(item["priority"])
    if item.get("due_at"):
        bits.append(f"due {_when(item['due_at'])}")
    if item.get("status"):
        bits.append(item["status"]["name"])
    bits.append(f"`{item['id']}`")
    return "- " + " · ".join(bits)


def render_today(view: dict[str, Any]) -> str:
    out = [f"# Today\n\n_As of {_when(view['now'])}; the day ends {_when(view['day_ends_at'])}._"]
    for key, heading in (
        ("overdue", "Overdue"),
        ("today", "Due today"),
        ("waiting", "Waiting on you"),
    ):
        rows = view.get(key) or []
        out.append(f"## {heading}\n\n" + ("\n".join(_item_line(i) for i in rows) or "_Nothing._"))
    return "\n\n".join(out) + "\n"


def render_item(item: dict[str, Any]) -> str:
    facts = []
    if item.get("status"):
        facts.append(f"**Status:** {item['status']['name']}")
    facts.append(f"**Priority:** {item.get('priority') or 'none'}")
    if item.get("due_at"):
        facts.append(f"**Due:** {_when(item['due_at'])}")
    if item.get("start_at"):
        facts.append(f"**Starts:** {_when(item['start_at'])}")
    people = ", ".join(f"@{a['handle']}" for a in item.get("assignees", [])) or "nobody"
    meta = [" · ".join(facts), f"**Assigned:** {people}"]
    if item.get("tags"):
        meta.append("**Tags:** " + ", ".join(item["tags"]))
    if item.get("waiting_on_account_id"):
        meta.append(f"**Waiting on:** `{item['waiting_on_account_id']}`")
    if item.get("blocked"):
        waits = ", ".join(f"`{i}`" for i in item.get("blocked_by") or [])
        meta.append(f"**Blocked:** waits on an open task (blocked by {waits}); do not start it yet")
    ids = f"**Id:** `{item['id']}` · list `{item['list_id']}`"
    if item.get("parent_id"):
        ids += f" · parent `{item['parent_id']}`"
    if item.get("archived_at"):
        ids += f" · archived {_when(item['archived_at'])}"
    meta.append(ids)

    out = [f"# {item['title']}", "\n".join(f"- {m}" for m in meta)]
    if item.get("body_md"):
        out.append(item["body_md"])
    if item.get("acceptance_md"):
        out.append(f"## Done means\n\n{item['acceptance_md']}")
    if item.get("subtasks"):
        lines = [
            f"- [{'x' if s.get('completed_at') else ' '}] {s['title']} (`{s['id']}`)"
            for s in item["subtasks"]
        ]
        out.append("## Subtasks\n\n" + "\n".join(lines))
    if item.get("comments"):
        lines = [
            f"- `{c['author_id']}` · {_when(c['created_at'])}: {c['body_md']}"
            for c in item["comments"]
        ]
        out.append("## Comments\n\n" + "\n".join(lines))
    if item.get("events"):
        lines = [
            f"- {_when(e['at'])} · {e['type']}"
            + (f" ({e['field']})" if e.get("field") else "")
            + f" · by `{e['actor_id']}` via {e['source']}"
            for e in item["events"]
        ]
        out.append("## Recent history\n\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n"


# --- streamable HTTP inside the FastAPI app -------------------------------------------------


def _bearer(headers: Mapping[str, str]) -> str | None:
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _http_backend(ctx: Any) -> Backend:
    request = getattr(ctx, "request", None)
    token = _bearer(request.headers) if request is not None else None
    if token is None:  # BearerGate already refused these; this is belt and braces
        raise ToolError("missing bearer token")
    ref = request.headers.get("x-slate-session-ref")
    if ref is not None and len(ref) > SESSION_REF_MAX:
        # The same limit as REST's header and the `session_ref` argument: the ref rides on
        # every event the call writes (History, audit, the live stream, webhooks).
        raise ToolError(f"X-Slate-Session-Ref must be at most {SESSION_REF_MAX} characters")
    return LocalBackend(token, session_ref=ref)


def _token_ok(token: str) -> bool:
    """The gate only reads: the tool call resolves the token again, and stamps its use."""
    with db.SessionLocal() as session:
        return accounts.authenticate(session, token, touch=False) is not None


class BearerGate:
    """ASGI wrapper: 401 unless `Authorization: Bearer <valid Slate token>`, then MCP."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            token = _bearer(headers)
            if token is None or not await anyio.to_thread.run_sync(_token_ok, token):
                detail = "missing bearer token" if token is None else "invalid or revoked token"
                response = JSONResponse(
                    {"detail": detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
        await self.manager.handle_request(scope, receive, send)


def install(app: FastAPI) -> None:
    """Serve MCP at `/mcp`. The app's lifespan must enter `app.state.mcp_manager.run()`.

    Stateless + JSON responses: every POST is self-contained (no server-held MCP sessions), and
    Slate never sends server-initiated requests. DNS-rebinding protection is off because every
    request must carry a bearer token, which a rebinding page cannot supply, and Slate is served
    behind a reverse proxy or VPN under a hostname the SDK's localhost allowlist would reject
    with 421.
    """
    manager = StreamableHTTPSessionManager(
        app=build_server(_http_backend),
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.state.mcp_manager = manager
    app.router.routes.append(Route("/mcp", endpoint=BearerGate(manager)))


# --- stdio: the `slate-mcp` console script ---------------------------------------------------


def main() -> None:
    """`slate-mcp`: MCP over stdio, calling Slate's REST API at SLATE_URL with SLATE_TOKEN."""
    url = os.environ.get("SLATE_URL", "").strip()
    token = os.environ.get("SLATE_TOKEN", "").strip()
    missing = [n for n, v in (("SLATE_URL", url), ("SLATE_TOKEN", token)) if not v]
    if missing:
        raise SystemExit(
            f"slate-mcp: set {' and '.join(missing)} (e.g. "
            "SLATE_URL=https://slate.example.com and a token from an admin)"
        )

    async def serve() -> None:
        from mcp.server.stdio import stdio_server

        async with httpx.AsyncClient(base_url=url, timeout=30) as client:
            backend = RestBackend(client, token)
            server = build_server(lambda _ctx: backend)
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

    try:
        anyio.run(serve)
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)
