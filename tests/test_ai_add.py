"""AI add (service/aiadd.py): parse plain words with Claude or Ollama, route to the right list,
create one item (and optional recurrence), and fall back deterministically when offline."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from conftest import API
from slate.service import aiadd
from slate.settings import settings

TEXT = (
    "ok so Agent needs to look into the office Wi-Fi. Check the router logs and the access "
    "point status, by friday at 3, it's important"
)


@pytest.fixture
def model(monkeypatch):
    """Turn AI add on (Anthropic) and answer with whatever the test puts in `answer`."""
    monkeypatch.setattr(settings, "ai_provider", "")
    monkeypatch.setattr(settings, "ai_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_url", "")
    monkeypatch.setattr(settings, "ai_model", "")
    seen: dict = {"answer": {}, "calls": []}

    def fake(system, text):
        seen["calls"].append((system, text))
        if isinstance(seen["answer"], Exception):
            raise seen["answer"]
        return seen["answer"]

    monkeypatch.setattr(aiadd, "call_model", fake)
    return seen


def _agent(client, alex, handle):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "agent"},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _add(client, h, text=TEXT, *, list_id=None, route=None, **defaults):
    payload: dict = {"text": text, "defaults": defaults}
    if list_id is not None:
        payload["list_id"] = list_id
    if route is not None:
        payload["route"] = route
    r = client.post(f"{API}/items/ai", json=payload, headers=h)
    assert r.status_code == 201, r.text
    return r


def test_off_until_configured(client, alex, todo, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_url", "")
    assert client.get(f"{API}/me", headers=alex).json()["ai_add"] is False
    r = client.post(f"{API}/items/ai", json={"list_id": todo["id"], "text": "x"}, headers=alex)
    assert r.status_code == 409


def test_provider_selection_prefers_anthropic_or_explicit(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_url", "")
    monkeypatch.setattr(settings, "ai_model", "")
    assert aiadd.provider() is None

    monkeypatch.setattr(settings, "ai_url", "http://ollama.test")
    assert aiadd.provider() == "ollama"
    assert aiadd.model_name() == aiadd.DEFAULT_OLLAMA_MODEL

    monkeypatch.setattr(settings, "ai_api_key", "sk-ant-test")
    assert aiadd.provider() == "anthropic"
    assert aiadd.model_name() == aiadd.DEFAULT_ANTHROPIC_MODEL

    monkeypatch.setattr(settings, "ai_provider", "ollama")
    assert aiadd.provider() == "ollama"


def test_parses_first_and_creates_one_item_with_ai_session_ref(client, alex, todo, model):
    _agent(client, alex, "my-agent")
    model["answer"] = {
        "title": "Fix the office Wi-Fi",
        "body_md": "- [ ] Check the router logs\n- [ ] Check the access point status",
        "assignees": ["@my-agent", "nobody-here"],
        "due_at": "2026-11-06T15:00",
        "priority": "high",
        "status": "nonsense",
        "start_at": "next week",
    }
    assert client.get(f"{API}/me", headers=alex).json()["ai_add"] is True
    r = _add(client, alex, list_id=todo["id"])
    assert "x-slate-ai-fallback" not in r.headers
    item = r.json()
    assert item["title"] == "Fix the office Wi-Fi"
    assert "router logs" in item["body_md"]
    assert [a["handle"] for a in item["assignees"]] == ["my-agent"]
    assert item["priority"] == "high"
    assert item["due_at"].startswith("2026-11-06T21:00")
    assert item["due_date_only"] is False
    assert item["start_at"] is None
    assert item["status"]["name"] == "To do"
    assert item["subtask_count"] == 0

    system, text = model["calls"][0]
    assert text == TEXT
    assert "my-agent: My-Agent" in system
    assert "The person filing it is @alex" in system
    assert "Next week" in system

    events = client.get(
        f"{API}/events", params={"item_id": item["id"], "limit": 50}, headers=alex
    ).json()["events"]
    assert events and {e["session_ref"] for e in events} == {"ai-add"}
    assert item["created_by"] == client.get(f"{API}/me", headers=alex).json()["account"]["id"]


def test_auto_routes_across_lists_and_defaults_to_inbox(client, alex, todo, model):
    sec_id = todo["section_id"]
    # Create a specialized list with a clear note and a reference list
    money = client.post(
        f"{API}/lists",
        json={"section_id": sec_id, "name": "Money", "note_md": "Taxes, bills and finance."},
        headers=alex,
    ).json()
    client.post(
        f"{API}/lists",
        json={"section_id": sec_id, "name": "Ideas", "reference": True},
        headers=alex,
    )
    # Set workspace inbox default to `todo`
    r_inb = client.put(f"{API}/settings/inbox", json={"inbox_list_id": todo["id"]}, headers=alex)
    assert r_inb.status_code == 200
    assert client.get(f"{API}/me", headers=alex).json()["inbox_list_id"] == todo["id"]

    # 1. Model routes to Money when words match Money
    model["answer"] = {"title": "Pay water bill", "body_md": "", "list_id": money["id"]}
    routed = _add(client, alex, text="Pay the water bill").json()
    assert routed["list_id"] == money["id"]
    assert routed["title"] == "Pay water bill"
    assert routed["body_md"] == ""
    system, _ = model["calls"][-1]
    assert f"Default list: {todo['id']}" in system
    assert "Taxes, bills and finance." in system

    # 2. Model omits list_id (or returns unknown id) -> lands in inbox default `todo`
    model["answer"] = {"title": "Buy milk", "body_md": "", "list_id": "nonexistent"}
    defaulted = _add(client, alex, text="Buy milk").json()
    assert defaulted["list_id"] == todo["id"]


def test_creates_and_adopts_recurrence_and_rolls_past_same_day_due(
    client, alex, todo, model, monkeypatch
):
    # Tuesday 2026-11-24 22:22 America/Chicago = 2026-11-25 04:22 UTC
    late_tuesday = datetime(2026, 11, 25, 4, 22, tzinfo=UTC)
    monkeypatch.setattr(aiadd, "utcnow", lambda: late_tuesday)
    # Model outputs today's date (already 5h past 5pm!) and T17:00
    model["answer"] = {
        "title": "Take medication",
        "body_md": "",
        "assignees": ["alex"],
        "due_at": "2026-11-24T17:00",
        "rrule": "FREQ=WEEKLY;BYDAY=TU",
    }
    item = _add(
        client,
        alex,
        list_id=todo["id"],
        text="Hey, create a recurring task every Tuesday for me to take medication",
    ).json()
    assert item["title"] == "Take medication"
    assert item["body_md"] == ""
    assert [a["handle"] for a in item["assignees"]] == ["alex"]
    assert item["recurrence_id"] is not None
    # Rolled forward to next Tuesday (2026-12-01 at 17:00 Chicago = 23:00 UTC) and date-only!
    assert item["due_at"].startswith("2026-12-01T23:00")
    assert item["due_date_only"] is True
    assert item["subtask_count"] == 0

    rule = client.get(f"{API}/recurrences/{item['recurrence_id']}", headers=alex).json()
    assert rule["rrule"] == "FREQ=WEEKLY;BYDAY=TU"
    assert rule["title_template"] == "Take medication"


def test_salvages_invalid_rrule_from_model_and_strips_bogus_1700(client, alex, todo, model):
    model["answer"] = {
        "title": "Change HVAC filter",
        "body_md": "",
        "due_at": "2026-12-01T17:00",
        "rrule": "FREQ=TRIMONTHLY",  # invalid RFC 5545!
        "trigger": "on_done",
    }
    item = _add(
        client,
        alex,
        list_id=todo["id"],
        text="Change the HVAC filter every 3 months after I finish it",
    ).json()
    assert item["due_date_only"] is True
    assert item["recurrence_id"] is not None
    rule = client.get(f"{API}/recurrences/{item['recurrence_id']}", headers=alex).json()
    assert rule["rrule"] == "FREQ=MONTHLY;INTERVAL=3"
    assert rule["trigger"] == "on_done"


def test_model_failure_falls_back_to_deterministic_parser(client, alex, todo, model, monkeypatch):
    late_tuesday = datetime(2026, 11, 25, 4, 22, tzinfo=UTC)
    monkeypatch.setattr(aiadd, "utcnow", lambda: late_tuesday)
    client.put(f"{API}/settings/inbox", json={"inbox_list_id": todo["id"]}, headers=alex)
    model["answer"] = RuntimeError("connection refused")

    r = _add(
        client,
        alex,
        text="Hey, create a recurring task every Tuesday for me to take vitamins !high #health",
    )
    assert r.headers.get("x-slate-ai-fallback") == "1"
    item = r.json()
    assert item["list_id"] == todo["id"]
    assert item["title"] == "Take vitamins"
    assert item["priority"] == "high"
    assert item["tags"] == ["health"]
    assert [a["handle"] for a in item["assignees"]] == ["alex"]
    assert item["recurrence_id"] is not None
    assert item["due_at"].startswith("2026-12-01T23:00")


def test_guest_only_sees_project_lists_and_project_people_and_not_workspace_inbox(
    client, alex, todo, model
):
    client.put(f"{API}/settings/inbox", json={"inbox_list_id": todo["id"]}, headers=alex)
    sec = client.post(
        f"{API}/sections", json={"name": "Example Project", "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    work = client.post(
        f"{API}/lists", json={"section_id": sec["id"], "name": "Work"}, headers=alex
    ).json()
    agent = _agent(client, alex, "my-agent")
    client.put(
        f"{API}/shares",
        json={
            "account_id": agent["id"],
            "scope_kind": "section",
            "scope_id": sec["id"],
            "level": "edit",
        },
        headers=alex,
    )
    guest = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    client.put(
        f"{API}/shares",
        json={
            "account_id": guest["id"],
            "scope_kind": "section",
            "scope_id": sec["id"],
            "level": "edit",
        },
        headers=alex,
    )
    tok = client.post(
        f"{API}/accounts/{guest['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    gh = {"Authorization": f"Bearer {tok['token']}"}

    me = client.get(f"{API}/me", headers=gh).json()
    assert me["ai_add"] is True
    assert me["inbox_list_id"] is None  # workspace inbox is hidden from guests

    model["answer"] = {
        "title": "Send the venue contract",
        "body_md": "Include the floor plan.",
        "list_id": todo["id"],  # workspace list -> must be ignored!
        "assignees": ["guest", "my-agent"],
        "priority": "urgent",
    }
    item = _add(client, gh, text="I need to send the venue contract, urgent").json()
    assert item["list_id"] == work["id"]  # landed in guest's own editable list
    assert item["title"] == "Send the venue contract"
    assert item["priority"] == "urgent"
    assert [a["handle"] for a in item["assignees"]] == ["guest"]  # workspace agent dropped
    system, _ = model["calls"][0]
    assert "my-agent" not in system
    assert todo["id"] not in system
    assert "@guest" in system


def test_reserved_defaults_are_ignored_and_bad_ones_refused(client, alex, todo, model):
    model["answer"] = {"title": "Water the plants", "body_md": ""}
    item = _add(client, alex, list_id=todo["id"], text="water the plants", title="sneaky").json()
    assert item["title"] == "Water the plants"
    r = client.post(
        f"{API}/items/ai",
        json={"list_id": todo["id"], "text": "x", "defaults": {"priority": "whenever"}},
        headers=alex,
    )
    assert r.status_code == 422


def test_column_tag_stays_and_incompatible_status_dropped_when_routed(client, alex, todo, model):
    other = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "Other"}, headers=alex
    ).json()
    model["answer"] = {
        "title": "Call the supplier",
        "body_md": "About phase 2.",
        "list_id": other["id"],
        "tags": ["example"],
    }
    item = _add(
        client,
        alex,
        list_id=todo["id"],
        route=True,
        text="call the supplier about phase 2, example",
        tags=["work"],
        status="To do",
    ).json()
    assert item["list_id"] == other["id"]
    assert item["title"] == "Call the supplier"
    assert sorted(item["tags"]) == ["example", "work"]


def test_first_sentence():
    assert aiadd.first_sentence("Call the plumber. He said Tuesday.") == "Call the plumber."
    long = "word " * 40
    t = aiadd.first_sentence(long)
    assert len(t) <= 81 and t.endswith("…")


def _ask(n: int) -> aiadd.Ask:
    return aiadd.Ask(text=f"task {n}", me="alex", default_list_id="L1")


def test_parses_wait_for_one_worker_and_never_hold_request_threads(monkeypatch):
    """A slow model delays only other parses: they run one at a time outside the request thread
    pool, and beyond the queue cap requests fall back locally immediately."""
    import threading

    import anyio
    import anyio.to_thread

    started: list[str] = []
    release = threading.Event()

    def slow(system, text):
        started.append(text)
        release.wait(10)
        return {"title": f"Model {text}", "body_md": ""}

    monkeypatch.setattr(aiadd, "call_model", slow)
    monkeypatch.setattr(aiadd, "MAX_QUEUED", 3)
    seen: dict = {"results": []}

    async def run_one(n: int):
        seen["results"].append(await aiadd.parse_async(_ask(n)))

    async def main():
        async with anyio.create_task_group() as tg:
            for n in range(5):
                tg.start_soon(run_one, n)
            with anyio.fail_after(5):
                while not started:
                    await anyio.sleep(0.01)
            await anyio.sleep(0.1)
            seen["running"] = len(started)
            seen["queued"] = aiadd.queued()
            seen["pool"] = anyio.to_thread.current_default_thread_limiter().borrowed_tokens
            release.set()
        seen["done"] = len(started)

    anyio.run(main)
    assert seen["running"] == 1
    assert seen["pool"] == 0
    assert seen["queued"] == 3
    assert seen["done"] == 3
    assert aiadd.queued() == 0
    # 3 ran through the model, 2 overflowed into local fallback
    fallbacks = [fb for _, fb in seen["results"]]
    assert fallbacks.count(False) == 3 and fallbacks.count(True) == 2


def test_anthropic_and_ollama_transports_and_size_cap(monkeypatch):
    # Anthropic tool_use response
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ai_url", "")
    monkeypatch.setattr(settings, "ai_model", "claude-sonnet-5-5")

    captured: dict = {}

    def anth_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "create_task",
                        "input": {"title": "Water the plants", "body_md": ""},
                    }
                ]
            },
        )

    monkeypatch.setattr(aiadd, "transport", httpx.MockTransport(anth_handler))
    assert aiadd.call_model("system", "words") == {"title": "Water the plants", "body_md": ""}
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["key"] == "sk-test"
    assert '"claude-sonnet-5-5"' in captured["body"]

    # Oversized answer is refused
    huge = (
        b'{"content": [{"type": "tool_use", "input": {"title": "'
        + b"x" * (aiadd.MAX_ANSWER_BYTES + 10)
        + b'"}}]}'
    )
    monkeypatch.setattr(
        aiadd, "transport", httpx.MockTransport(lambda r: httpx.Response(200, content=huge))
    )
    with pytest.raises(ValueError, match="too long"):
        aiadd.call_model("system", "words")

    # Ollama response still works
    monkeypatch.setattr(settings, "ai_provider", "ollama")
    monkeypatch.setattr(settings, "ai_url", "http://model.test")
    ok = b'{"message": {"content": "{\\"title\\": \\"Water the plants\\", \\"body_md\\": \\"\\"}"}}'
    monkeypatch.setattr(
        aiadd, "transport", httpx.MockTransport(lambda r: httpx.Response(200, content=ok))
    )
    assert aiadd.call_model("system", "words") == {"title": "Water the plants", "body_md": ""}


def test_one_bad_tag_does_not_drop_the_good_ones(client, alex, todo, model):
    client.post(f"{API}/tags", json={"name": "errands"}, headers=alex)
    model["answer"] = {
        "title": "Buy stamps",
        "body_md": "At the post office.",
        "tags": ["Errands", "x" * 300, "  ", "#postal"],
    }
    item = _add(
        client, alex, list_id=todo["id"], text="buy stamps at the post office, tag it postal"
    ).json()
    assert item["title"] == "Buy stamps"
    assert sorted(item["tags"]) == ["errands", "postal"]


def test_the_model_does_not_invent_tags_the_words_never_named(client, alex, todo, model):
    model["answer"] = {"title": "Buy stamps", "body_md": "", "tags": ["shopping", "misc"]}
    item = _add(client, alex, list_id=todo["id"], text="buy stamps, file it under shopping").json()
    assert item["tags"] == ["shopping"]
    assert [t["name"] for t in client.get(f"{API}/tags", headers=alex).json()] == ["shopping"]
