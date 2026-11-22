"""The released-alert relay against fake Slate and broker endpoints."""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from bridge import DEFAULT_REASONS, Bridge, http  # noqa: E402


def alert(cursor: str, loudness: int = 2, reason: str = "assigned", **fields) -> dict:
    return {
        "cursor": cursor,
        "at": "2026-11-21T10:00:00Z",
        "loudness": loudness,
        "title": "My Agent assigned you",
        "body": "Print labels",
        "item_id": "I1",
        "reason": reason,
        "entry": None,
        **fields,
    }


class Fake:
    def __init__(self) -> None:
        self.released: list[dict] = []
        self.alerts: list[dict] = []
        self.gets: list[dict[str, list[str]]] = []
        self.posts: list[str] = []
        self.head = "start"
        self.include_header = True
        self.slate_status = 200
        self.broker_status = 200
        self.broker_fail_on: str | None = None

    def __call__(self, method, url, headers, body):
        if method == "POST":
            assert url.startswith("https://push.example.com/")
            assert headers["Authorization"] == "Bearer broker-key"
            self.posts.append(url)
            payload = json.loads(body)
            if self.broker_fail_on == payload["title"]:
                return 503, None, {}
            if 200 <= self.broker_status < 300:
                self.alerts.append(payload)
            return self.broker_status, None, {}
        assert method == "GET"
        assert headers["Authorization"] == "Bearer slate-token"
        parsed = urlsplit(url)
        assert parsed.netloc == "slate.example.com"
        assert parsed.path == "/api/v1/inbox/alerts"
        query = parse_qs(parsed.query, keep_blank_values=True)
        self.gets.append(query)
        if self.slate_status != 200:
            return self.slate_status, None, {}
        if "after" not in query:
            newest = self.released[-1]["cursor"] if self.released else self.head
            return 200, [], {"x-slate-cursor": newest} if self.include_header else {}
        after = query["after"][0]
        cursors = [self.head, *(row["cursor"] for row in self.released)]
        return 200, self.released[cursors.index(after) :][: int(query["limit"][0])], {}


def make(tmp_path, fake, reasons=("assigned", "mention"), **kwargs):
    return Bridge(
        "https://slate.example.com",
        "slate-token",
        "https://push.example.com",
        "broker-key",
        "https://board.example.com",
        tmp_path / "state.json",
        set(reasons),
        send=fake,
        **kwargs,
    )


def test_first_run_uses_the_header_and_only_relays_later_releases(tmp_path):
    fake = Fake()
    fake.released = [alert("old /?&+")]
    bridge = make(tmp_path, fake)
    assert bridge.run_once() == 0
    assert fake.gets == [{"limit": ["200"]}]
    assert bridge.load() == "old /?&+"
    assert fake.alerts == []

    fake.released.append(alert("later"))
    assert bridge.run_once() == 1
    assert fake.gets[-1] == {"after": ["old /?&+"], "limit": ["200"]}
    assert fake.alerts[0]["click_url"] == "https://board.example.com/#/inbox?task=I1"
    assert bridge.load() == "later"
    assert bridge.run_once() == 0


def test_an_old_state_file_bootstraps_the_released_stream(tmp_path):
    fake = Fake()
    fake.released = [alert("latest")]
    bridge = make(tmp_path, fake)
    bridge.state_path.write_text('{"at":"2026-11-21T10:00:00Z","ids":["E1"]}', encoding="utf-8")
    assert bridge.run_once() == 0
    assert bridge.load() == "latest"
    assert fake.gets == [{"limit": ["200"]}]
    assert fake.posts == []


def test_bootstrap_requires_a_cursor_header(tmp_path):
    fake = Fake()
    fake.include_header = False
    bridge = make(tmp_path, fake)
    with pytest.raises(RuntimeError, match="X-Slate-Cursor"):
        bridge.run_once()
    assert not bridge.state_path.exists()


def test_an_older_server_cannot_relay_raw_inbox_entries(tmp_path):
    fake = Fake()
    fake.slate_status = 404
    bridge = make(tmp_path, fake)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        bridge.run_once()
    assert fake.gets == [{"limit": ["200"]}]
    assert not bridge.state_path.exists()


def test_released_order_levels_and_reason_filter(tmp_path):
    fake = Fake()
    bridge = make(tmp_path, fake)
    bridge.run_once()
    fake.released = [alert(cursor, level) for level, cursor in enumerate(("z", "y", "x", "w", "v"))]
    fake.released.append(alert("a", 4, "watching"))
    assert bridge.run_once() == 3
    assert [a["severity"] for a in fake.alerts] == [2, 3, 4]
    assert bridge.load() == "a"
    assert bridge.run_once() == 0


def test_escalation_and_quiet_hours_summary_use_exact_server_wording(tmp_path):
    fake = Fake()
    bridge = make(tmp_path, fake, reasons=("waiting",))
    bridge.run_once()
    question = "Ship it?\n" + "x" * 300
    title = "Still waiting: " + "x" * 100
    fake.released = [
        alert("escalation", 4, "waiting", title=title, body=question),
        alert(
            "summary",
            3,
            "waiting",
            title="3 things while you were away",
            body="First\nSecond\nThird",
            item_id=None,
        ),
    ]
    assert bridge.run_once() == 2
    assert fake.alerts == [
        {
            "severity": 4,
            "title": f"Slate: {title}",
            "body": question,
            "click_url": "https://board.example.com/#/inbox?task=I1",
        },
        {
            "severity": 3,
            "title": "Slate: 3 things while you were away",
            "body": "First\nSecond\nThird",
            "click_url": "https://board.example.com/#/inbox",
        },
    ]
    assert len(fake.gets) == 2


def test_stalled_is_a_default_reason_relayed_in_the_servers_words(tmp_path):
    assert "stalled" in DEFAULT_REASONS.split(",")
    fake = Fake()
    b = make(tmp_path, fake, reasons=set(DEFAULT_REASONS.split(",")))
    b.run_once()
    fake.released = [
        alert(
            "c1",
            reason="stalled",
            title="My Agent stopped without finishing: Print labels",
            body="Run now carries it on",
        )
    ]
    assert b.run_once() == 1
    assert fake.alerts[0]["title"] == "Slate: My Agent stopped without finishing: Print labels"
    assert fake.alerts[0]["severity"] == 2


def test_failed_publish_resumes_after_the_last_success(tmp_path):
    fake = Fake()
    bridge = make(tmp_path, fake)
    bridge.run_once()
    fake.released = [alert("first"), alert("retry", title="Try again"), alert("later")]
    fake.broker_fail_on = "Slate: Try again"
    with pytest.raises(RuntimeError, match="503"):
        bridge.run_once()
    assert len(fake.alerts) == 1
    assert bridge.load() == "first"

    fake.broker_fail_on = None
    assert bridge.run_once() == 2
    assert fake.gets[-1]["after"] == ["first"]
    assert bridge.load() == "later"


def test_alert_path_is_configurable_and_accepts_empty_success(tmp_path):
    fake = Fake()
    fake.broker_status = 204
    bridge = make(tmp_path, fake, alert_path="hooks/slate")
    bridge.run_once()
    fake.released = [alert("next")]
    assert bridge.run_once() == 1
    assert fake.posts == ["https://push.example.com/hooks/slate"]


def test_failed_is_still_a_default_reason():
    assert "failed" in DEFAULT_REASONS.split(",")


def test_page_boundary_preserves_the_next_release(tmp_path):
    fake = Fake()
    bridge = make(tmp_path, fake)
    bridge.run_once()
    fake.released = [alert(f"c{n}") for n in range(201)]
    assert bridge.run_once() == 200
    assert bridge.load() == "c199"
    assert bridge.run_once() == 1
    assert fake.gets[-1]["after"] == ["c199"]
    assert bridge.load() == "c200"


def test_http_exposes_the_cursor_header(monkeypatch):
    response = SimpleNamespace(status=200, headers={"X-Slate-Cursor": "opaque"}, read=lambda: b"[]")
    monkeypatch.setattr("bridge.urllib.request.urlopen", lambda *a, **k: nullcontext(response))
    status, rows, headers = http("GET", "https://slate.example.com/api/v1/inbox/alerts", {}, None)
    assert (status, rows, headers) == (200, [], {"x-slate-cursor": "opaque"})
