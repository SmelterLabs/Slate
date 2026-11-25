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
import re
import shutil
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 256 * 1024
TOKEN = os.environ.get("SLATE_BRIDGE_TOKEN", "").strip()


def _claude_models() -> list[dict[str, str]]:
    cfg = Path.home() / ".claude.json"
    if not cfg.is_file():
        return []
    try:
        raw = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    found: dict[str, tuple[int, int, int, str]] = {}
    fam_rank = {"sonnet": 0, "opus": 1, "haiku": 2}
    for m in re.finditer(
        r"\b(claude-(sonnet|opus|haiku)-(\d+)(?:-(\d+))?(?:-\d{8})?)\b", raw, re.I
    ):
        mid = m.group(1).lower()
        if re.search(r"-\d{8}$", mid):
            continue
        fam = m.group(2).lower()
        major = int(m.group(3))
        minor = int(m.group(4) or 0)
        ver_label = f"{major}.{minor}" if m.group(4) else str(major)
        found[mid] = (major, minor, -fam_rank[fam], f"Claude {fam.title()} {ver_label} ({mid})")
    for mid, info in sorted(found.items(), key=lambda kv: kv[1][:3], reverse=True):
        seen.add(mid)
        out.append({"id": mid, "label": info[3]})
    try:
        data = json.loads(raw)
        for opt in data.get("additionalModelOptionsCache") or []:
            if not isinstance(opt, dict):
                continue
            val = str(opt.get("value") or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            desc = str(opt.get("description") or "").split("·")[0].strip()
            lbl = desc or str(opt.get("label") or val).strip()
            out.append({"id": val, "label": f"{lbl} ({val})"})
    except ValueError:
        pass
    return out


def _codex_models() -> list[dict[str, str]]:
    cache = Path.home() / ".codex" / "models_cache.json"
    if not cache.is_file():
        return [{"id": "", "label": "Default (codex)"}]
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        out = [{"id": "", "label": "Default (codex)"}]
        seen: set[str] = set()
        for m in data.get("models") or []:
            slug = str(m.get("slug") or "").strip()
            if not slug or slug in seen or m.get("visibility") == "hide":
                continue
            seen.add(slug)
            out.append({"id": slug, "label": str(m.get("display_name") or slug).strip()})
        return out
    except Exception:  # noqa: BLE001
        return [{"id": "", "label": "Default (codex)"}]


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
            models = _claude_models() if cli == "claude" else _codex_models()
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
