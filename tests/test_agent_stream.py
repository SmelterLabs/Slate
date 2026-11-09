"""GET /checkin/stream: an agent's check-in pushed live (news with why, work hints), resumable
from a cursor and limited to what the token can see. Runs on the real server of
test_api_events (SSE never finishes, so TestClient cannot read it)."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack

import httpx

from test_api_events import _bearer, _open, live  # noqa: F401 (live is a fixture)

API = "/api/v1"


async def _setup(client: httpx.AsyncClient, admin: str) -> dict:
    h = _bearer(admin)
    section = (
        await client.post(f"{API}/sections", json={"name": "Ops", "audience": "ai"}, headers=h)
    ).json()
    todo = (
        await client.post(
            f"{API}/lists", json={"section_id": section["id"], "name": "Todo"}, headers=h
        )
    ).json()
    return todo


async def _agent(client: httpx.AsyncClient, admin: str, handle: str, **limit) -> tuple[dict, str]:
    h = _bearer(admin)
    acct = (
        await client.post(
            f"{API}/accounts",
            json={"handle": handle, "display_name": handle.title(), "kind": "agent"},
            headers=h,
        )
    ).json()
    body = {"scopes": ["write"], **({"limit": limit} if limit else {})}
    tok = await client.post(f"{API}/accounts/{acct['id']}/tokens", json=body, headers=h)
    assert tok.status_code == 201, tok.text
    return acct, tok.json()["token"]


async def _item(client, admin, list_id, title, **kw) -> dict:
    r = await client.post(
        f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=_bearer(admin)
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_assignment_arrives_as_news_and_a_work_hint_within_a_second(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        stream = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
        assert await stream.ready() == []

        start = time.monotonic()
        item = await _item(client, live.admin_token, todo["id"], "Do it", assignees=["agent"])
        got = [await stream.event(timeout=1), await stream.event(timeout=1)]
        assert time.monotonic() - start < 1
        kinds = {(m.event, m.data.get("why")) for m in got}
        assert ("news", "assigned") in kinds
        assert ("work", None) in kinds
        hint = next(m for m in got if m.event == "work")
        assert hint.data["item_id"] == item["id"]

        # Taking it: my own events are not news to me, and nothing is hinted.
        r = await client.post(f"{API}/work/next", json={}, headers=_bearer(tok))
        assert r.json()["item"]["id"] == item["id"]
        r = await client.post(
            f"{API}/items/{item['id']}/comments",
            json={"body_md": "how is it going?"},
            headers=_bearer(live.admin_token),
        )
        msg = await stream.event(timeout=1)
        assert msg.event == "news" and msg.data["why"] == "on_your_item"
        assert msg.data["type"] == "item.commented"


async def test_resume_from_last_event_id_replays_what_was_missed(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        first = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
        await first.ready()
        a = await _item(client, live.admin_token, todo["id"], "A", assignees=["agent"])
        seen = await first.event()
        cursor = seen.id

        # Offline: two more things happen.
        b = await _item(client, live.admin_token, todo["id"], "B", assignees=["agent"])
        await client.post(
            f"{API}/items/{a['id']}/comments",
            json={"body_md": "@agent ping"},
            headers=_bearer(live.admin_token),
        )
        again = await _open(
            client,
            stack,
            f"{API}/checkin/stream",
            headers={**_bearer(tok), "Last-Event-ID": cursor},
        )
        replayed = await again.ready()
        news = [(m.data["why"], m.data.get("item_title")) for m in replayed if m.event == "news"]
        assert ("assigned", "B") in news
        assert ("on_your_item", "A") in news or ("mentioned", "A") in news
        hints = [m.data["item_id"] for m in replayed if m.event == "work"]
        assert len(hints) == len(set(hints))  # one hint per task in a replay
        assert b["id"] in hints


async def test_pool_hints_follow_list_limits(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        other = (
            await client.post(
                f"{API}/lists",
                json={"section_id": todo["section_id"], "name": "Secret"},
                headers=_bearer(live.admin_token),
            )
        ).json()
        _, tok = await _agent(client, live.admin_token, "agent", list_ids=[todo["id"]])
        stream = await _open(
            client, stack, f"{API}/checkin/stream", params={"pool": "true"}, headers=_bearer(tok)
        )
        await stream.ready()
        await _item(client, live.admin_token, other["id"], "hidden")
        visible = await _item(client, live.admin_token, todo["id"], "open to anyone")
        msg = await stream.event(timeout=1)
        assert msg.event == "work" and msg.data["item_id"] == visible["id"]
        try:
            extra = await stream.event(timeout=0.3)
        except TimeoutError:
            extra = None
        assert extra is None


async def test_checkin_wait_returns_as_soon_as_news_arrives(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url, timeout=10) as client:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        since = (await client.get(f"{API}/checkin", headers=_bearer(tok))).json()["next_since"]

        async def later():
            await asyncio.sleep(0.3)
            await _item(client, live.admin_token, todo["id"], "ping", assignees=["agent"])

        start = time.monotonic()
        task = asyncio.create_task(later())
        r = await client.get(
            f"{API}/checkin", params={"since": since, "wait": 5}, headers=_bearer(tok)
        )
        await task
        assert r.status_code == 200, r.text
        assert [n["why"] for n in r.json()["news"]] == ["assigned"]
        assert time.monotonic() - start < 3

        # Nothing new: it waits out `wait` and answers empty.
        start = time.monotonic()
        r = await client.get(
            f"{API}/checkin",
            params={"since": r.json()["next_since"], "wait": 1},
            headers=_bearer(tok),
        )
        assert r.json()["news"] == [] and time.monotonic() - start >= 0.9


async def test_a_chat_mention_reaches_the_runner_as_a_work_hint(live):  # noqa: F811
    """Chat wakes (docs/chat.md): a person's @mention in a channel
    is a `work` hint with the channel and thread, the runner takes exactly that turn, a second
    mention while it is open is hinted as queued, and the reply ends it."""
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        stream = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
        assert await stream.ready() == []
        key = f"list:{todo['id']}"
        admin = _bearer(live.admin_token)

        start = time.monotonic()
        r = await client.post(
            f"{API}/channels/{key}/messages", json={"body_md": "@agent summarise?"}, headers=admin
        )
        assert r.status_code == 201, r.text
        msg = r.json()
        got = [await stream.event(timeout=1), await stream.event(timeout=1)]
        assert time.monotonic() - start < 1
        news = next(m for m in got if m.event == "news")
        assert news.data["why"] == "mentioned" and news.data["type"] == "message.posted"
        hint = next(m for m in got if m.event == "work")
        assert hint.data == {
            **hint.data,
            "type": "message.mentioned_agent",
            "channel": key,
            "thread_id": None,
            "message_id": msg["id"],
            "queued": False,
        }
        assert "item_id" not in hint.data

        # The runner answers the hint with exactly that turn.
        r = await client.post(f"{API}/work/next", json={"channel": key}, headers=_bearer(tok))
        assert r.status_code == 200, r.text
        assert r.json()["chat"]["message_id"] == msg["id"] and r.json()["item"] is None
        run_id = r.json()["chat"]["lease"]["run_id"]

        # A second mention while the turn is open: hinted, marked queued (no second run).
        r = await client.post(
            f"{API}/channels/{key}/messages", json={"body_md": "@agent also this"}, headers=admin
        )
        got = [await stream.event(timeout=1), await stream.event(timeout=1)]
        hint = next(m for m in got if m.event == "work")
        assert hint.data["queued"] is True and hint.data["message_id"] == r.json()["id"]

        # The reply ends the turn; the agent's own events are never hinted back to it.
        r = await client.post(
            f"{API}/work/chat/finish",
            json={
                "channel": key,
                "outcome": "done",
                "reply": "Two things, both open.",
                "run_id": run_id,
            },
            headers=_bearer(tok),
        )
        assert r.status_code == 200, r.text
        assert r.json()["message"]["body_md"] == "Two things, both open."
        assert r.json()["pending"] is True
        # The turn is pending again for the queued mention; one more mention folds into it and
        # is hinted as a fresh turn (not queued): the runner's next take answers both.
        r = await client.post(
            f"{API}/channels/{key}/messages", json={"body_md": "@agent and this"}, headers=admin
        )
        got = [await stream.event(timeout=1), await stream.event(timeout=1)]
        hint = next(m for m in got if m.event == "work")
        assert hint.data["queued"] is False and hint.data["message_id"] == r.json()["id"]
        r = await client.post(f"{API}/work/next", json={"channel": key}, headers=_bearer(tok))
        assert r.json()["chat"]["message_id"] == hint.data["message_id"]
