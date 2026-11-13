"""AI add (service/aiadd.py): one item from plain words, filled in by the configured model as
Slate's own account. The model is faked; nothing leaves the test."""

from __future__ import annotations

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
    """Turn AI add on and answer with whatever the test puts in `answer`."""
    monkeypatch.setattr(settings, "ai_url", "http://model.test")
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


def _add(client, h, list_id, text=TEXT, **defaults):
    r = client.post(
        f"{API}/items/ai", json={"list_id": list_id, "text": text, "defaults": defaults}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_off_until_configured(client, alex, todo):
    assert client.get(f"{API}/me", headers=alex).json()["ai_add"] is False
    r = client.post(f"{API}/items/ai", json={"list_id": todo["id"], "text": "x"}, headers=alex)
    assert r.status_code == 409


def test_files_one_item_then_the_model_fills_it_in_as_slate(client, alex, todo, model):
    _agent(client, alex, "agent")
    model["answer"] = {
        "title": "Fix the office Wi-Fi",
        "body_md": "- [ ] Check the router logs\n- [ ] Check the access point status",
        "assignees": ["@agent", "nobody-here"],
        "due_at": "2026-11-06T15:00",
        "priority": "high",
        "status": "nonsense",
        "start_at": "next week",
    }
    assert client.get(f"{API}/me", headers=alex).json()["ai_add"] is True
    item = _add(client, alex, todo["id"])
    # The TestClient runs background tasks before returning, so the fill is done here.
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["title"] == "Fix the office Wi-Fi"
    assert "router logs" in got["body_md"]
    assert [a["handle"] for a in got["assignees"]] == ["agent"]
    assert got["priority"] == "high" and got["due_at"].startswith("2026-11-06T21:00")
    assert got["start_at"] is None and got["status"]["name"] == "To do"
    system, text = model["calls"][0]
    assert text == TEXT and "agent: Agent" in system and "The person filing it is @alex" in system
    assert "(tomorrow)" in system
    # Exactly one item, no children; the fill is Slate's, not Alex's.
    assert got["subtask_count"] == 0
    events = client.get(
        f"{API}/events", params={"item_id": item["id"], "limit": 50}, headers=alex
    ).json()["events"]
    fills = [e for e in events if e["session_ref"] == "ai-add"]
    assert fills and {e["source"] for e in fills} == {"system"}
    assert {e["actor_id"] for e in fills} != {item["created_by"]}


def test_the_card_starts_as_typed_with_the_columns_meaning(client, alex, todo, model):
    model["answer"] = {"title": "Call the dentist", "body_md": "Call the dentist."}
    item = _add(client, alex, todo["id"], text="call the dentist. tomorrow", priority="low")
    assert item["title"] == "call the dentist." and item["body_md"] == "call the dentist. tomorrow"
    assert item["priority"] == "low"


def test_a_model_that_is_down_leaves_the_card_as_typed(client, alex, todo, model):
    model["answer"] = RuntimeError("connection refused")
    item = _add(client, alex, todo["id"])
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["title"] == item["title"] and got["body_md"] == TEXT.strip()


def test_a_field_changed_before_the_model_answers_is_left_alone(client, alex, todo, monkeypatch):
    monkeypatch.setattr(settings, "ai_url", "http://model.test")
    real_fill, asks = aiadd.fill, []
    monkeypatch.setattr(aiadd, "fill", asks.append)  # hold the fill back
    item = _add(client, alex, todo["id"])
    client.patch(f"{API}/items/{item['id']}", json={"title": "My own title"}, headers=alex)
    monkeypatch.setattr(
        aiadd, "call_model", lambda s, t: {"title": "Model title", "priority": "urgent"}
    )
    assert real_fill(asks[0]) == {"priority": "urgent"}
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["title"] == "My own title" and got["priority"] == "urgent"


def test_a_guest_uses_it_and_only_sees_their_project_people(client, alex, model):
    sec = client.post(
        f"{API}/sections", json={"name": "Example Project", "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    work = client.post(
        f"{API}/lists", json={"section_id": sec["id"], "name": "Work"}, headers=alex
    ).json()
    agent = _agent(client, alex, "agent")  # a workspace agent, shared into the project too
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
    model["answer"] = {
        "title": "Send the venue contract",
        "body_md": "Include the floor plan.",
        "assignees": ["guest", "agent"],
        "priority": "urgent",
    }
    assert client.get(f"{API}/me", headers=gh).json()["ai_add"] is True
    item = _add(client, gh, work["id"], text="I need to send the venue contract, urgent")
    got = client.get(f"{API}/items/{item['id']}", headers=gh).json()
    assert got["title"] == "Send the venue contract" and got["priority"] == "urgent"
    assert [a["handle"] for a in got["assignees"]] == ["guest"]  # agent was never offered
    system, _ = model["calls"][0]
    assert "agent" not in system and "@guest" in system


def test_reserved_defaults_are_ignored_and_bad_ones_refused(client, alex, todo, model):
    model["answer"] = {}
    item = _add(client, alex, todo["id"], text="water the plants", title="sneaky")
    assert item["title"] == "water the plants"
    r = client.post(
        f"{API}/items/ai",
        json={"list_id": todo["id"], "text": "x", "defaults": {"priority": "whenever"}},
        headers=alex,
    )
    assert r.status_code == 422


def test_first_sentence():
    assert aiadd.first_sentence("Call the plumber. He said Tuesday.") == "Call the plumber."
    long = "word " * 40
    t = aiadd.first_sentence(long)
    assert len(t) <= 81 and t.endswith("…")


def test_a_tag_column_keeps_its_tag_and_the_model_adds_its_own(client, alex, todo, model):
    model["answer"] = {
        "title": "Call the supplier",
        "body_md": "About phase 2.",
        "tags": ["example"],
    }
    item = _add(
        client, alex, todo["id"], text="call the supplier about phase 2, example", tags=["work"]
    )
    assert item["tags"] == ["work"]
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    # The column's tag stays and the model's is added.
    assert got["title"] == "Call the supplier" and sorted(got["tags"]) == ["example", "work"]


def _ask(n: int) -> aiadd.Ask:
    return aiadd.Ask(item_id=f"item{n:08d}", text="words", me="alex", people={}, statuses=[])


def test_fills_wait_for_one_worker_and_never_hold_request_threads(monkeypatch):
    """A slow model delays only other fills: they run one at a time outside the request thread
    pool, and beyond the queue cap a card stays as typed."""
    import threading

    import anyio
    import anyio.to_thread

    started: list[str] = []
    release = threading.Event()

    def slow(system, text):
        started.append(text)
        release.wait(10)
        return {}

    monkeypatch.setattr(aiadd, "call_model", slow)
    monkeypatch.setattr(aiadd, "MAX_QUEUED", 3)
    seen: dict = {}

    async def main():
        async with anyio.create_task_group() as tg:
            for n in range(5):
                tg.start_soon(aiadd.fill_later, _ask(n))
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
    assert seen["running"] == 1  # one at a time
    assert seen["pool"] == 0  # no request-serving thread is held while the model thinks
    assert seen["queued"] == 3  # two over the cap were dropped
    assert seen["done"] == 3
    assert aiadd.queued() == 0


def test_the_route_schedules_the_bounded_fill(client, alex, todo, model, monkeypatch):
    scheduled = []

    async def spy(a):
        scheduled.append(a.item_id)

    monkeypatch.setattr(aiadd, "fill_later", spy)
    item = _add(client, alex, todo["id"])
    assert scheduled == [item["id"]]
    assert model["calls"] == []  # the real fill never ran in the request's thread pool


def test_an_oversized_model_answer_is_refused(monkeypatch):
    import httpx

    monkeypatch.setattr(settings, "ai_url", "http://model.test")
    huge = b'{"message": {"content": "' + b"x" * (aiadd.MAX_ANSWER_BYTES + 10) + b'"}}'
    monkeypatch.setattr(
        aiadd, "transport", httpx.MockTransport(lambda r: httpx.Response(200, content=huge))
    )
    with pytest.raises(ValueError, match="too long"):
        aiadd.call_model("system", "words")
    ok = b'{"message": {"content": "{\\"title\\": \\"Water the plants\\"}"}}'
    monkeypatch.setattr(
        aiadd, "transport", httpx.MockTransport(lambda r: httpx.Response(200, content=ok))
    )
    assert aiadd.call_model("system", "words") == {"title": "Water the plants"}


def test_one_bad_tag_does_not_drop_the_good_ones(client, alex, todo, model):
    client.post(f"{API}/tags", json={"name": "errands"}, headers=alex)
    model["answer"] = {
        "title": "Buy stamps",
        "body_md": "At the post office.",
        "tags": ["Errands", "x" * 300, "  ", "#postal"],
    }
    item = _add(client, alex, todo["id"], text="buy stamps at the post office, tag it postal")
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["title"] == "Buy stamps"
    assert sorted(got["tags"]) == ["errands", "postal"]


def test_the_model_does_not_invent_tags_the_words_never_named(client, alex, todo, model):
    model["answer"] = {"title": "Buy stamps", "body_md": "x", "tags": ["shopping", "misc"]}
    item = _add(client, alex, todo["id"], text="buy stamps, file it under shopping")
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["tags"] == ["shopping"]  # named in the words; `misc` exists nowhere
    assert [t["name"] for t in client.get(f"{API}/tags", headers=alex).json()] == ["shopping"]
