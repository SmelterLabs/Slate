"""The Slate -> push webhook bridge against fake Slate and broker endpoints (no network)."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bridge import Bridge, tier_for  # noqa: E402


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
        self.priorities = {"I1": "urgent", "I2": "low", "I3": None}
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
        item = url.rsplit("/", 1)[1]
        return 200, {"id": item, "priority": self.priorities.get(item)}


def make(tmp_path, fake, reasons=("assigned", "mention"), **kw):
    return Bridge(
        "http://slate",
        "t",
        "http://push",
        "k",
        "https://board",
        tmp_path / "s.json",
        set(reasons),
        2,
        fake,
        **kw,
    )


def test_tiers_follow_the_card_flag_and_never_exceed_4():
    assert [tier_for(p, 2) for p in ("urgent", "high", "normal", "low")] == [4, 3, 2, 1]
    assert tier_for(None, 2) == 2


def test_first_run_sets_cursor_without_replaying_backlog(tmp_path):
    fake = Fake()
    fake.inbox = [entry("e1", "2026-11-06T10:00:00Z")]
    b = make(tmp_path, fake)
    assert b.run_once() == 0
    assert fake.alerts == []
    fake.inbox.append(entry("e2", "2026-11-06T10:01:00Z"))
    assert b.run_once() == 1
    a = fake.alerts[0]
    assert a["severity"] == 4
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


def test_card_tier_unflagged_and_cardless(tmp_path):
    fake = Fake()
    b = make(tmp_path, fake, reasons=("assigned", "health"))
    b.run_once()
    fake.inbox = [
        entry("e1", "2026-11-06T10:01:00Z", item="I2"),
        entry("e2", "2026-11-06T10:02:00Z", item="I3"),
        entry("e3", "2026-11-06T10:03:00Z", reason="health", item=None),
        entry("e4", "2026-11-06T10:04:00Z", reason="watching"),  # not a chosen reason
    ]
    assert b.run_once() == 3
    assert [a["severity"] for a in fake.alerts] == [1, 2, 2]
    assert fake.alerts[2]["click_url"] == "https://board/#/inbox"


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
