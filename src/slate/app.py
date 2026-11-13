"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from slate import (
    __version__,
    api,
    automator,
    emailer,
    fastlane,
    mcp_server,
    monitor,
    notify,
    pusher,
    runner_compat,
    scheduler,
)
from slate.api import events as events_api
from slate.api import recurrences as recurrences_api
from slate.api import uploads
from slate.build import build_info
from slate.db import SessionLocal, engine
from slate.models import utcnow
from slate.service import health
from slate.settings import settings

log = logging.getLogger(__name__)


# The board shell's policy: only its own bundles run, nothing frames it, images and media may
# come from anywhere (markdown and attachments), styles may be inline (Svelte's `style=`).
# Frames may show only blob: URLs the board made itself (the PDF viewers of attachments and
# file docs hand the browser's own PDF renderer a fetched blob); no outside page is framed.
BOARD_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https: http:",
        "media-src 'self' blob: https: http:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "object-src 'none'",
        "frame-src blob:",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
BOARD_HEADERS = {
    "Content-Security-Policy": BOARD_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


class BoardFiles(StaticFiles):
    """The built board. Everything except the hashed bundles under /assets/ is revalidated on every
    load (`no-cache`), so no browser or installed app can keep showing a build older than the
    one deployed; the hashed bundles never change under the same name and are cached for good."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
            response.headers.update(BOARD_HEADERS)
        return response


_BOARD_ENTRY = re.compile(r"/assets/index-[^\"'\s]+\.js")
_board_seen: tuple[float, str | None] | None = None


def board_bundle() -> str | None:
    """The entry bundle the served index.html loads (`/assets/index-<hash>.js`), None when no
    board is served. Open boards compare it with their own to reload after a deploy
    (web/src/lib/freshness.ts). Re-read only when the file changes."""
    global _board_seen
    if not settings.static_dir:
        return None
    index = settings.static_dir / "index.html"
    try:
        mtime = index.stat().st_mtime
    except OSError:
        return None
    if _board_seen is None or _board_seen[0] != mtime:
        m = _BOARD_ENTRY.search(index.read_text(errors="replace"))
        _board_seen = (mtime, m.group(0) if m else None)
    return _board_seen[1]


def _warm_items() -> None:
    from slate.api.items import _ITEM_ADAPTER
    from slate.service import item_cache, items

    try:
        with SessionLocal() as session:
            n = item_cache.warm(
                session, lambda s: items.query_items(s, only_ids=True), items.views, _ITEM_ADAPTER
            )
        log.info("item cache warm: %d items", n)
    except Exception:  # a cold cache is only slower
        log.exception("item cache warm-up failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Start order: MCP task group, scheduler (recurrence + due events are hooks on its tick),
    # webhook dispatcher (its own ~1 s loop; refuses to start, logged, without a valid
    # SLATE_SECRET_KEY), automations engine (bus-woken). Stopped in reverse.
    # SLATE_SCHEDULER_ENABLED=false turns off the scheduler and dispatcher (tests);
    # SLATE_AUTOMATIONS_ENABLED=false the automations engine, SLATE_PUSH_ENABLED=false push.
    async with app.state.mcp_manager.run():  # MCP at /mcp needs its task group running
        dispatcher: asyncio.Task | None = None
        engine_task: asyncio.Task | None = None
        if settings.scheduler_enabled:
            monitor.track("scheduler", scheduler.start())
            dispatcher = notify.start_dispatcher()
            monitor.track(
                "webhooks", dispatcher, None if dispatcher else "no valid SLATE_SECRET_KEY"
            )
        else:
            monitor.track("scheduler", None, "SLATE_SCHEDULER_ENABLED=false")
            monitor.track("webhooks", None, "SLATE_SCHEDULER_ENABLED=false")
        if settings.automations_enabled:
            engine_task = automator.start()
        push_task = pusher.start() if settings.push_enabled else None
        # Email has its own switch: the SMTP settings (slate.mailer.enabled).
        email_task = emailer.start()
        # The Health page shows each engine as running, off, or stopped (slate.monitor).
        monitor.track("automations", engine_task, "SLATE_AUTOMATIONS_ENABLED=false")
        monitor.track(
            "push",
            push_task,
            "SLATE_SECRET_KEY is not set" if settings.push_enabled else "SLATE_PUSH_ENABLED=false",
        )
        monitor.track("email", email_task, "SLATE_SMTP_HOST / SLATE_SMTP_FROM not set")
        # The first board to open after a restart finds its items built (docs/PERFORMANCE.md).
        threading.Thread(target=_warm_items, name="slate-warm", daemon=True).start()
        try:
            yield
        finally:
            if email_task is not None:
                email_task.cancel()
                with suppress(asyncio.CancelledError):
                    await email_task
            if push_task is not None:
                push_task.cancel()
                with suppress(asyncio.CancelledError):
                    await push_task
            if engine_task is not None:
                engine_task.cancel()
                with suppress(asyncio.CancelledError):
                    await engine_task
            if dispatcher is not None:
                dispatcher.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatcher
            await scheduler.stop()


# The interactive API page (`/docs`) loads Swagger UI from a CDN, pinned to one release and
# checked by Subresource Integrity, under its own policy: a changed or hijacked file does not
# run, and nothing else may load, so the page can never read the board's stored token.
SWAGGER_VERSION = "5.34.1"
_SWAGGER = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}"
SWAGGER_FILES = {
    f"{_SWAGGER}/swagger-ui-bundle.js": (
        "sha384-XFYN47VzPiDY6qPtxYKg0nqUeW/upo20xf1j2SUE7gT/i35FJB4cuS/71Iegzk8Z"
    ),
    f"{_SWAGGER}/swagger-ui.css": (
        "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF"
    ),
}
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)


def api_docs_page(openapi_url: str) -> HTMLResponse:
    html = get_swagger_ui_html(
        openapi_url=openapi_url,
        title="Slate API",
        swagger_js_url=f"{_SWAGGER}/swagger-ui-bundle.js",
        swagger_css_url=f"{_SWAGGER}/swagger-ui.css",
        swagger_favicon_url="/icons/icon.svg",
    ).body.decode()
    for url, digest in SWAGGER_FILES.items():
        html = html.replace(f'"{url}"', f'"{url}" integrity="{digest}" crossorigin="anonymous"', 1)
    inline = [
        "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
        for body in _INLINE_SCRIPT.findall(html)
    ]
    csp = "; ".join(
        [
            "default-src 'none'",
            f"script-src {' '.join(inline)} {_SWAGGER}/",
            f"style-src {_SWAGGER}/ 'unsafe-inline'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        ]
    )
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )


# `/health` has no token and builds the whole report (counts and sums over big tables), so
# its answer is kept for a few seconds: a status page, not a load generator.
HEALTH_CACHE_SECONDS = 10.0
_health_lock = threading.Lock()
_health_cached: tuple[float, dict] | None = None


def _health_body() -> dict:
    global _health_cached
    with _health_lock:
        now = time.monotonic()
        if _health_cached is None or now - _health_cached[0] >= HEALTH_CACHE_SECONDS:
            with SessionLocal() as session:
                _health_cached = (now, jsonable_encoder(health.summary(session)))
        return _health_cached[1]


def create_app() -> FastAPI:
    global _health_cached
    _health_cached = None
    app = FastAPI(
        title="Slate",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # served below, pinned; the ReDoc page is not served
        redoc_url=None,
    )

    @app.get("/docs", include_in_schema=False)
    def api_docs() -> HTMLResponse:
        return api_docs_page(app.openapi_url or "/openapi.json")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        last = scheduler.last_tick_at
        age = None
        if settings.scheduler_enabled and last is not None:
            age = round((utcnow() - last).total_seconds(), 1)
        # Deploy checks read these (deploy/server/RUNBOOK.md); the board shows `rev` top-left.
        build = build_info()
        return {
            "ok": True,
            "version": __version__,
            "rev": build["rev"],
            "commit": build["commit"],
            "db": "ok",
            "scheduler_last_tick_age_s": age,
            "webhooks_enabled": bool(settings.secret_key),
            "board": board_bundle(),
            "min_runner_protocol": runner_compat.MIN_RUNNER_PROTOCOL,
        }

    @app.get("/health", include_in_schema=False)
    def health_summary() -> JSONResponse:
        # The Health page's verdict without details, for agents and uptime checks: 503 on fail.
        body = _health_body()
        return JSONResponse(body, status_code=503 if body["status"] == "fail" else 200)

    api.install(app)
    events_api.install(app)
    recurrences_api.install(app)
    mcp_server.install(app)
    fastlane.install(app)
    runner_compat.install(app)
    uploads.install(app)  # outermost: the body cap sees every request first

    if settings.static_dir and settings.static_dir.is_dir():
        app.mount("/", BoardFiles(directory=settings.static_dir, html=True), name="board")

    return app


app = create_app()
