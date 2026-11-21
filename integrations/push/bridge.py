"""Slate -> push webhook: one account's Slate notifications, posted to an alert broker.

Optional adapter (stdlib only). It reads the account's inbox through Slate's public REST API
(`GET /inbox?unread=true`) and posts each new entry as JSON to a push-notification webhook
(`POST <broker><alert-path>`, default path `/v1/alert`) with a bearer key. Slate knows nothing
about it; deleting this folder breaks nothing in `tests/`.

Payload: `{"severity": 2-4, "title": "Slate: ...", "body": "...", "click_url": "..."}`.
Severity follows the inbox entry's server-provided loudness. Levels 0 and 1 are skipped.
An entry from an older server without loudness uses level 2.

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

# Card push reasons, without chat and the daily digest, plus `failed` (a run the agent
# could not finish). A routine digest is not an alert.
DEFAULT_REASONS = "mention,reply,assigned,waiting,due,unblocked,nudge,reminder,health,access,failed"

Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


def http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


def wording(entry: dict[str, Any], who: str) -> tuple[str, str]:
    """The inbox entry's headline and body before adding the broker prefix."""
    reason = entry["reason"]
    ev = entry["event"]
    after = ev.get("after") if isinstance(ev.get("after"), dict) else {}
    thing = entry.get("item_title") or "Slate"
    text = (after.get("body_md") or "")[:200]
    heads = {
        "mention": f"{who} mentioned you",
        "reply": f"{who} replied",
        "chat": f"{who} wrote",
        "assigned": f"{who} assigned you",
        "waiting": f"{who} is waiting on you",
        "due": "Overdue" if ev.get("type") == "item.overdue" else "Due now",
        "unblocked": "Ready to start",
        "nudge": "An agent has a task waiting",
        "reminder": "Reminder",
        "health": "Slate health",
        "access": "Guest access ending",
        "failed": f"{who} could not run",
        "comment": f"{who} commented",
        "status": f"{who} changed the status",
    }
    head = heads.get(reason, f"{who} changed it")
    if reason == "waiting":
        question = str(after.get("question") or "").splitlines()
        first = question[0].strip() if question else ""
        body = f"{first}\n{thing}" if first else thing
        return head, body
    return head, f"{thing}: {text}" if text else thing


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
        """The actor's display name, from the roster any read token may list (`GET /agents
        ?kind=all`; `/accounts` is for admins, and the bridge needs no more than read).
        "Someone" when it is not there or the roster cannot be read: a name is never a reason
        to hold an alert back."""
        if account_id not in self.names:
            try:
                roster = self._get("/agents?kind=all") or []
            except RuntimeError as e:
                log.warning("names unavailable: %s", e)
                roster = []
            for row in roster:
                a = row.get("account") or {}
                if a.get("id"):
                    self.names[a["id"]] = a.get("display_name") or a.get("handle") or "Someone"
        return self.names.get(account_id, "Someone")

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
        by_id = {e["event"]["id"]: e for e in entries}
        read_entries_loaded = False
        for e in entries:
            ev = e["event"]
            if ev["at"] < state["at"] or (ev["at"] == state["at"] and ev["id"] in state["ids"]):
                continue
            severity = e.get("loudness", 2)
            if e["reason"] in self.reasons and 2 <= severity <= 4:
                original = None
                if ev.get("type") == "inbox.escalated":
                    original_id = ev["after"]["event_id"]
                    if original_id not in by_id and not read_entries_loaded:
                        by_id.update(
                            (entry["event"]["id"], entry)
                            for entry in self._get("/inbox?limit=200") or []
                        )
                        read_entries_loaded = True
                    original = by_id.get(original_id)
                self.publish(e, severity, original)
                sent += 1
            if ev["at"] != state["at"]:
                state = {"at": ev["at"], "ids": []}
            state["ids"].append(ev["id"])
            self.save(state)
        return sent

    def publish(self, e: dict[str, Any], severity: int, original: dict[str, Any] | None) -> None:
        ev = e["event"]
        source = original or e
        item_id = ev.get("item_id") or source["event"].get("item_id")
        head, body = wording(source, self.name(source["event"]["actor_id"]))
        if ev.get("type") == "inbox.escalated":
            head = f"Still waiting: {head}" if original else "Still waiting"
        title = f"Slate: {head}"[:80]
        click = f"{self.board}/#/inbox?task={item_id}" if item_id else f"{self.board}/#/inbox"
        payload = {"severity": severity, "title": title, "body": body[:260], "click_url": click}
        status, data = self.send("POST", self.target, self.kauth, json.dumps(payload).encode())
        if not 200 <= status < 300:
            raise RuntimeError(f"Push broker refused alert ({status}); retrying next pass")
        log.info("severity %d %s -> %s", severity, title, (data or {}).get("id"))


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
        "--reasons", default=DEFAULT_REASONS, help="comma-separated inbox reasons to relay"
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
