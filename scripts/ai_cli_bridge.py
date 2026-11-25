#!/usr/bin/env python3
"""Optional local host bridge so a Dockerized Slate can call host-installed `claude` or `codex`
CLIs (using the host user's own signed-in subscription).

Run on the Docker host (binds to 127.0.0.1 or the Docker bridge):
    python3 scripts/ai_cli_bridge.py --host 0.0.0.0 --port 11435

Then in Slate Settings > AI Task Creation, pick "Claude (CLI Subscription)" or "Codex (CLI
Subscription)" and set Bridge URL to `http://host.docker.internal:11435`.

Optional auth: set `SLATE_BRIDGE_TOKEN=<secret>` in the environment and enter the same token
as the API key in Slate Settings.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 256 * 1024
TOKEN = os.environ.get("SLATE_BRIDGE_TOKEN", "").strip()


def _run_cli(cli: str, model: str, system: str, prompt: str, timeout: float) -> str:
    full = f"{system}\n\n---\n\n{prompt}"
    if cli == "claude":
        cmd = ["claude", "-p", "-"]
        if model and model != "default":
            cmd += ["--model", model]
    elif cli == "codex":
        cmd = ["codex", "exec", "-"]
        if model and model != "default":
            cmd += ["--model", model]
    else:
        raise ValueError(f"unsupported cli: {cli}")
    proc = subprocess.run(
        cmd,
        input=full,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        raise RuntimeError(f"{cli} exited {proc.returncode}: {detail}")
    return proc.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        return

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {TOKEN}"

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self._send(
                200,
                {
                    "ok": True,
                    "claude": shutil.which("claude") is not None,
                    "codex": shutil.which("codex") is not None,
                },
            )
        if parsed.path == "/models":
            cli = (urllib.parse.parse_qs(parsed.query).get("cli") or ["claude"])[0]
            if cli == "claude":
                models = [
                    {"id": "sonnet", "label": "Sonnet"},
                    {"id": "opus", "label": "Opus"},
                    {"id": "haiku", "label": "Haiku"},
                ]
            else:
                models = [{"id": "", "label": "Default (Codex CLI)"}]
            return self._send(200, {"models": models})
        return self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        if self.path != "/complete":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"error": "payload too large"})
        try:
            req = json.loads(self.rfile.read(length))
            text = _run_cli(
                str(req.get("cli") or "claude"),
                str(req.get("model") or ""),
                str(req.get("system") or ""),
                str(req.get("prompt") or ""),
                float(req.get("timeout") or 60.0),
            )
            return self._send(200, {"text": text})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)})


def main() -> None:
    ap = argparse.ArgumentParser(description="Host CLI bridge for Dockerized Slate AI add")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11435)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Slate AI CLI bridge listening on http://{args.host}:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
