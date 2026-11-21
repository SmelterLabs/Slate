"""The Slate -> push webhook bridge against fake Slate and broker endpoints (no network)."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bridge import DEFAULT_REASONS, Bridge  # noqa: E402


def entry(eid: str, at: str, reason: str = "assigned", item: str | None = "I1") -> dict:
    return {
        "event": {
            "id": eid,
            "at": at,
            "type": "item.assigned",
            "item_id": item,
            "actor_id": "A1",
            "after": None,
        },
        "reason": reason,
        "item_title": "Pay the water bill",
    }


class Fake:
    def __init__(self) -> None:
        self.inbox: list[dict] = []
        self.alerts: list[dict] = []
        self.urls: list[str] = []
        self.broker_status = 200
        self.roster_status = 200

    def __call__(self, method, url, headers, body):
        if url.startswith("http://push/"):
            self.urls.append(url)
            if self.broker_status != 200:
                return self.broker_status, None
            self.alerts.append(json.loads(body))
            return 200, {"id": "abc"}
        if "/inbox" in url:
            return 200, list(self.inbox)
        if url.endswith("/accounts"):
            return 403, {"detail": "admin only"}  # the bridge holds an ordinary token
        if url.endswith("/agents?kind=all"):
            if self.roster_status != 200:
                return self.roster_status, None
            account = {"id": "A1", "handle": "my-agent", "display_name": "My Agent"}
            return 200, [{"account": account}]
        raise AssertionError(f"Unexpected request: {method} {url}")


def make(tmp_path, fake, reasons=("assigned", "mention"), **kw):
    return Bridge(
        "http://slate",
        "t",
        "http://push",
        "k",
        "https://board",
        tmp_path / "s.json",
        set(reasons),
        send=fake,
        **kw,
    )


def test_first_run_sets_cursor_without_replaying_backlog(tmp_path):
    fake = Fake()
    fake.inbox = [entry("e1", "2026-11-06T10:00:00Z")]
    b = make(tmp_path, fake)
    assert b.run_once() == 0
    assert fake.alerts == []
    fake.inbox.append(entry("e2", "2026-11-06T10:01:00Z"))
    assert b.run_once() == 1
    a = fake.alerts[0]
    assert a["severity"] == 2  # Older server.
    assert a["title"] == "Slate: My Agent assigned you"
    assert a["click_url"] == "https://board/#/inbox?task=I1"
    assert fake.urls == ["http://push/v1/alert"]
    assert b.run_once() == 0  # nothing is sent twice


def test_the_alert_path_is_configurable(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake, alert_path="hooks/slate")
    b.run_once()
    fake.inbox = [entry("e1", "2026-11-06T10:01:00Z")]
    assert b.run_once() == 1
    assert fake.urls == ["http://push/hooks/slate"]


def test_server_levels_and_reason_filter(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake, reasons=("assigned", "health"))
    b.run_once()
    fake.inbox = [
        dict(entry(f"e{level}", f"2026-11-06T10:0{level}:00Z"), loudness=level)
        for level in range(5)
    ] + [
        entry("e5", "2026-11-06T10:05:00Z", reason="health", item=None),
        dict(entry("e6", "2026-11-06T10:06:00Z", reason="watching"), loudness=4),
    ]
    assert b.run_once() == 4
    assert [a["severity"] for a in fake.alerts] == [2, 3, 4, 2]
    assert fake.alerts[-1]["click_url"] == "https://board/#/inbox"
    assert b.run_once() == 0


def test_failed_publish_is_retried_not_lost(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake)
    b.run_once()
    fake.inbox = [entry("e1", "2026-11-06T10:01:00Z")]
    fake.broker_status = 503
    with contextlib.suppress(RuntimeError):
        b.run_once()
    assert fake.alerts == []
    fake.broker_status = 200
    assert b.run_once() == 1


def test_names_come_from_the_roster_an_ordinary_token_may_read(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake)
    assert b.name("A1") == "My Agent"
    assert b.name("nobody") == "Someone"


def test_a_roster_it_cannot_read_still_sends_the_alert(tmp_path):
    fake = Fake()
    fake.roster_status = 403
    b = make(tmp_path, fake)
    b.run_once()  # first run: the cursor
    fake.inbox = [
        {
            "reason": "assigned",
            "event": {"id": "E1", "at": "2026-11-13T10:00:00Z", "actor_id": "A1", "item_id": "I1"},
            "title": "Renew passport",
        }
    ]
    assert b.run_once() == 1
    assert fake.alerts[0]["title"].startswith("Slate: Someone")


def _alert(reason: str, item: str, after: dict | None, title: str = "Pay the water bill") -> dict:
    return {
        "event": {
            "id": reason + item,
            "at": "2026-11-06T12:00:00Z",
            "type": "item.reported",
            "item_id": item,
            "actor_id": "A1",
            "after": after,
        },
        "reason": reason,
        "item_title": title,
    }


def test_waiting_uses_server_loudness_and_leads_with_the_question(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake, reasons=("waiting", "failed"))
    b.run_once()
    fake.inbox = [
        dict(
            _alert(
                "waiting", "I2", {"question": "Start the import now?\nmore", "body_md": "noise"}
            ),
            loudness=2,
        ),
        dict(_alert("waiting", "I1", {"question": "Ship it?"}), loudness=4),
    ]
    # A second pass needs a later timestamp than the cursor the first inbox wrote.
    fake.inbox[0]["event"]["at"] = "2026-11-06T12:01:00Z"
    fake.inbox[1]["event"]["at"] = "2026-11-06T12:02:00Z"
    assert b.run_once() == 2
    low, urgent = fake.alerts
    assert low["severity"] == 2
    assert urgent["severity"] == 4
    assert low["body"] == "Start the import now?\nPay the water bill"
    assert urgent["body"].startswith("Ship it?\n")
    assert "noise" not in low["body"]


def test_failed_is_a_default_reason_and_says_who_could_not_run(tmp_path):
    assert "failed" in DEFAULT_REASONS.split(",")
    fake = Fake()
    b = make(tmp_path, fake, reasons=set(DEFAULT_REASONS.split(",")))
    b.run_once()
    row = _alert("failed", "I3", {"note": "exit 1"}, title="Print labels")
    row["event"]["at"] = "2026-11-06T12:01:00Z"
    fake.inbox = [row]
    assert b.run_once() == 1
    assert fake.alerts[0]["title"] == "Slate: My Agent could not run"
    assert fake.alerts[0]["severity"] == 2


def test_escalation_reuses_original_wording_and_item(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake, reasons=("waiting",))
    original = dict(_alert("waiting", "I1", {"question": "Ship it?"}), loudness=3)
    fake.inbox = [original]
    b.run_once()
    fake.inbox.append(
        {
            "reason": "waiting",
            "loudness": 4,
            "event": {
                "id": "E2",
                "at": "2026-11-06T14:00:00Z",
                "type": "inbox.escalated",
                "subject_id": "P1",
                "actor_id": "system",
                "after": {"event_id": original["event"]["id"], "reason": "waiting", "minutes": 120},
            },
        }
    )
    assert b.run_once() == 1
    assert fake.alerts == [
        {
            "severity": 4,
            "title": "Slate: Still waiting: My Agent is waiting on you",
            "body": "Ship it?\nPay the water bill",
            "click_url": "https://board/#/inbox?task=I1",
        }
    ]
