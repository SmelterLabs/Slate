"""Refuse runners older than the API, loudly.

`clients/python/slate_runner.py` sends `X-Slate-Runner: slate-runner/<protocol>` on every call.
When an API change would break older runners (a field they send is removed, a shape they read
changes), raise MIN_RUNNER_PROTOCOL here and `RUNNER_PROTOCOL` in the runner in the same
change. An older runner then gets 426 with how to update on every call, instead of having its
reports refused one by one.
Clients without the header (agents, MCP, the board) are untouched.

Protocol history: 1 = sent `minutes`; 2 = `minutes` dropped; 3 = per-attempt execution proof.
Raising the floor is a coordinated upgrade: close admission and drain old runners before the
migration and the server change, then install the new runner and adapters together. Null
legacy proofs cannot be adopted. A protocol-3 runner refuses to execute a take whose server
returned no proof.
"""

from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

HEADER = "X-Slate-Runner"
MIN_RUNNER_PROTOCOL = 3
_PROTOCOL = re.compile(rb"^slate-runner/(\d+)$")
_HEADER = HEADER.lower().encode()


def too_old(value: bytes | None) -> int | None:
    """The runner's protocol when it is below the floor, else None."""
    m = _PROTOCOL.match((value or b"").strip())
    if m and int(m.group(1)) < MIN_RUNNER_PROTOCOL:
        return int(m.group(1))
    return None


class RunnerCompat:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            value = dict(scope.get("headers") or []).get(_HEADER)
            old = too_old(value)
            if old is not None:
                detail = (
                    f"This slate-runner speaks protocol {old}; this Slate server needs "
                    f"{MIN_RUNNER_PROTOCOL} or newer. Update the runner to the release that "
                    "matches the server (uv tool upgrade slate-runner, or copy "
                    "clients/python/slate_runner.py from the same commit) and restart it."
                )
                await JSONResponse({"detail": detail}, status_code=426)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def install(app) -> None:
    app.add_middleware(RunnerCompat)
