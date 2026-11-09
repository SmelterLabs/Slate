"""Slate -> push webhook: one account's Slate notifications, posted to an alert broker.

Optional adapter (stdlib only). It reads the account's inbox through Slate's public REST API
(`GET /inbox?unread=true`) and posts each new entry as JSON to a push-notification webhook
(`POST <broker><alert-path>`, default path `/v1/alert`) with a bearer key. Slate knows nothing
about it; deleting this folder breaks nothing in `tests/`.

Payload: `{"severity": 1-4, "title": "Slate: ...", "body": "...", "click_url": "..."}`.
Severity = the card's priority flag:

    urgent -> 4    (e.g. a persistent popup and a phone push)
    high   -> 3    (e.g. a toast with sound)
    normal -> 2    (e.g. a silent toast)
    low    -> 1    (e.g. log only)

A card with no flag, or an entry with no card (a health alert, a guest's expiring access),
goes out at `--default-tier` (2). Nothing above 4 is ever sent. What each level does is up to
your broker.

State (`--state`, JSON) holds the cursor: the newest inbox time already handled and the event
ids at that time. The first run only records the cursor, so the backlog is never replayed. An
entry is acknowledged only after the broker answered 2xx; a failed publish stops the pass and
is retried next pass (a broker with a dedupe window absorbs a POST that had landed).

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
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("slate-push")

TIER = {"urgent": 4, "high": 3, "normal": 2, "low": 1}
# Same default as Slate's own push reasons (service/push.py DEFAULT_REASONS) without chat and
# the daily digest: a routine digest is not an alert.
DEFAULT_REASONS = "mention,reply,assigned,waiting,due,unblocked,nudge,reminder,health,access"

Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


def http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


def tier_for(priority: str | None, default: int) -> int:
    return TIER.get(priority or "", default)


def wording(entry: dict[str, Any], who: str) -> tuple[str, str]:
    """(title, body) for the alert. The title starts "Slate:" so broker rules can mute it."""
    reason = entry["reason"]
    ev = entry["event"]
    after = ev.get("after") if isinstance(ev.get("after"), dict) else {}
    thing = entry.get("item_title") or "Slate"
    text = (after.get("body_md") or "")[:200]
    heads = {
        "mention": f"{who} mentioned you",
        "reply": f"{who} replied",
        "assigned": f"{who} assigned you",
        "waiting": f"{who} is waiting on you",
        "due": "Overdue" if ev.get("type") == "item.overdue" else "Due now",
        "unblocked": "Ready to start",
        "nudge": "An agent has a task waiting",
        "reminder": "Reminder",
        "health": "Slate health",
        "access": "Guest access ending",
        "comment": f"{who} commented",
        "status": f"{who} changed the status",
    }
    head = heads.get(reason, f"{who} changed it")
    return f"Slate: {head}"[:80], (f"{thing}: {text}" if text else thing)[:260]


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
        default_tier: int = 2,
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
        self.default_tier = default_tier
        self.send = send
        self.names: dict[str, str] = {}

    # --- state ------------------------------------------------------------------------------

    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def save(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # --- Slate -------------------------------------------------------------------------------

    def _get(self, path: str) -> Any:
        status, data = self.send("GET", self.slate + path, self.auth, None)
        if status != 200:
            raise RuntimeError(f"Slate GET {path} -> HTTP {status}")
        return data

    def name(self, account_id: str) -> str:
        if account_id not in self.names:
            for a in self._get("/accounts") or []:
                self.names[a["id"]] = a.get("display_name") or a.get("handle") or "Someone"
        return self.names.get(account_id, "Someone")

    def priority(self, item_id: str | None) -> str | None:
        if not item_id:
            return None
        status, data = self.send("GET", f"{self.slate}/items/{item_id}", self.auth, None)
        return (data or {}).get("priority") if status == 200 else None

    # --- one pass ----------------------------------------------------------------------------

    def run_once(self) -> int:
        """Publish what is new since the cursor; returns how many alerts were sent."""
        entries = self._get("/inbox?unread=true&limit=200") or []
        entries.sort(key=lambda e: (e["event"]["at"], e["event"]["id"]))
        state = self.load()
        if state is None:
            newest = entries[-1]["event"]["at"] if entries else ""
            ids = [e["event"]["id"] for e in entries if e["event"]["at"] == newest]
            self.save({"at": newest, "ids": ids})
            log.info("first run: cursor at %r, %d not replayed", newest, len(entries))
            return 0
        sent = 0
        for e in entries:
            ev = e["event"]
            if ev["at"] < state["at"] or (ev["at"] == state["at"] and ev["id"] in state["ids"]):
                continue
            if e["reason"] in self.reasons:
                self.publish(e)
                sent += 1
            if ev["at"] != state["at"]:
                state = {"at": ev["at"], "ids": []}
            state["ids"].append(ev["id"])
            self.save(state)
        return sent

    def publish(self, e: dict[str, Any]) -> None:
        ev = e["event"]
        item_id = ev.get("item_id")
        severity = tier_for(self.priority(item_id), self.default_tier)
        title, body = wording(e, self.name(ev["actor_id"]))
        click = f"{self.board}/#/inbox?task={item_id}" if item_id else f"{self.board}/#/inbox"
        payload = {"severity": severity, "title": title, "body": body, "click_url": click}
        status, data = self.send("POST", self.target, self.kauth, json.dumps(payload).encode())
        if not 200 <= status < 300:
            raise RuntimeError(f"Push broker refused alert ({status}); retrying next pass")
        log.info("tier %d %s -> %s", severity, title, (data or {}).get("id"))


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
    p.add_argument("--reasons", default=DEFAULT_REASONS, help="comma-separated inbox reasons")
    p.add_argument("--default-tier", type=int, default=2, choices=[1, 2, 3, 4])
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
        a.default_tier,
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
