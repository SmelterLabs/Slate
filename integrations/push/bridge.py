"""Slate -> push webhook: one account's Slate notifications, posted to an alert broker.

Optional adapter (stdlib only). It reads the account's released alerts
(`GET /inbox/alerts`) and posts selected alerts as JSON to a push-notification webhook
(`POST <broker><alert-path>`, default path `/v1/alert`) with a bearer key. Slate knows nothing
about it; deleting this folder breaks nothing in `tests/`.

Payload: `{"severity": 2-4, "title": "Slate: ...", "body": "...", "click_url": "..."}`.
Severity, title and body come from Slate. Levels below 2 are skipped.

State (`--state`, JSON) holds the opaque release cursor. The first run stores the newest
cursor from the response header and replays no backlog. A failed publish stops the pass
without advancing past that alert.

    python integrations/push/bridge.py --slate http://127.0.0.1:18448 \\
        --token-file ~/.slate-push.token --broker https://push.example.com \\
        --key-file ~/.push-key --board-url https://slate.example.com --state ~/.slate-push.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("slate-push")

# Card push reasons, without chat and the daily digest, plus `failed` (a run the agent
# could not finish) and `stalled` (it stopped without finishing). A routine digest is not
# an alert.
DEFAULT_REASONS = (
    "mention,reply,assigned,waiting,due,unblocked,nudge,reminder,health,access,failed,stalled"
)

Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any, dict[str, str]]]


def http(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, Any, dict[str, str]]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r) if method == "GET" else None
            return r.status, data, {key.lower(): value for key, value in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, None, {}


class Bridge:
    def __init__(
        self,
        slate: str,
        token: str,
        broker: str,
        broker_key: str,
        board_url: str,
        state_path: Path,
        reasons: set[str],
        send: Http = http,
        alert_path: str = "/v1/alert",
    ) -> None:
        self.slate = slate.rstrip("/") + "/api/v1"
        self.auth = {"Authorization": f"Bearer {token}"}
        self.target = broker.rstrip("/") + "/" + alert_path.lstrip("/")
        self.kauth = {"Authorization": f"Bearer {broker_key}", "Content-Type": "application/json"}
        self.board = board_url.rstrip("/")
        self.state_path = state_path
        self.reasons = reasons
        self.send = send

    # --- state ------------------------------------------------------------------------------

    def load(self) -> str | None:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")).get("cursor")
        except FileNotFoundError:
            return None

    def save(self, cursor: str) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # --- one pass ----------------------------------------------------------------------------

    def run_once(self) -> int:
        """Relay one page in server order and return how many alerts were sent."""
        cursor = self.load()
        query = {"limit": "200"}
        if cursor is not None:
            query["after"] = cursor
        path = f"/inbox/alerts?{urllib.parse.urlencode(query)}"
        status, alerts, headers = self.send("GET", self.slate + path, self.auth, None)
        if status != 200:
            raise RuntimeError(f"Slate GET /inbox/alerts -> HTTP {status}")
        if cursor is None:
            newest = headers.get("x-slate-cursor")
            if newest is None:
                raise RuntimeError("Slate GET /inbox/alerts did not return X-Slate-Cursor")
            self.save(newest)
            log.info("started at the current release cursor")
            return 0
        sent = 0
        for alert in alerts:
            if alert["reason"] in self.reasons and 2 <= alert["loudness"] <= 4:
                self.publish(alert)
                sent += 1
            self.save(alert["cursor"])
        return sent

    def publish(self, alert: dict[str, Any]) -> None:
        item_id = alert["item_id"]
        click = f"{self.board}/#/inbox?task={item_id}" if item_id else f"{self.board}/#/inbox"
        title = f"Slate: {alert['title']}"
        severity = alert["loudness"]
        payload = {"severity": severity, "title": title, "body": alert["body"], "click_url": click}
        status, _, _ = self.send("POST", self.target, self.kauth, json.dumps(payload).encode())
        if not 200 <= status < 300:
            raise RuntimeError(f"Push broker refused alert ({status}); retrying next pass")
        log.info("severity %d %s", severity, title)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--slate", default="http://127.0.0.1:18448")
    p.add_argument("--token-file", required=True, help="the Slate account's API token")
    p.add_argument(
        "--broker",
        default=os.environ.get("PUSH_BROKER_URL"),
        required="PUSH_BROKER_URL" not in os.environ,
        help="the push broker's base URL (env PUSH_BROKER_URL)",
    )
    p.add_argument("--alert-path", default="/v1/alert", help="the broker's alert endpoint")
    p.add_argument("--key-file", default="~/.push-key", help="the broker's bearer key")
    p.add_argument("--board-url", required=True, help="the board's public URL, for the tap link")
    p.add_argument("--state", default="~/.slate-push.json")
    p.add_argument(
        "--reasons", default=DEFAULT_REASONS, help="comma-separated alert reasons to relay"
    )
    p.add_argument("--interval", type=float, default=20.0)
    p.add_argument("--once", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    read = lambda f: Path(f).expanduser().read_text(encoding="utf-8").strip()  # noqa: E731
    bridge = Bridge(
        a.slate,
        read(a.token_file),
        a.broker,
        read(a.key_file),
        a.board_url,
        Path(a.state).expanduser(),
        {r.strip() for r in a.reasons.split(",") if r.strip()},
        alert_path=a.alert_path,
    )
    while True:
        try:
            bridge.run_once()
        except Exception as exc:  # a pass that fails is retried; the loop never dies on it
            log.warning("pass failed: %s", exc)
            if a.once:
                return 1
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
