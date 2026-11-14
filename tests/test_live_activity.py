"""What an agent's run is doing right now (`slate.liveactivity`): posted by the run that holds
the task, read by a board, sent over `/events/stream` as an id-less `item.activity`, and never
written anywhere (no event row, no comment)."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx
from sqlalchemy import func, select

from conftest import API
from run_proofs import remember, run_fields
from slate import db as slate_db
from slate import liveactivity
from slate.models import Event, Source
from slate.service import access, accounts, work
from slate.service import items as item_service
from slate.service import lists as list_service
from slate.service import sections as section_service
from slate.service.bus import bus
from slate.service.core import Actor
from test_api_events import _bearer, _open, live  # noqa: F401  (the real-server fixture)
from test_guest_boundary import world  # noqa: F401


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _take(client, h, item_id):
    r = client.post(f"{API}/work/next", json={"item_id": item_id}, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _say(client, h, item_id, *lines, **kw):
    body = run_fields(client, h, item_id, lines=list(lines)) | kw
    return client.post(f"{API}/work/{item_id}/activity", json=body, headers=h)


def _events() -> int:
    with slate_db.SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(Event))


def test_a_run_says_what_it_is_doing_and_nothing_is_stored(client, alex, todo, make_account):
    agent, ah = make_account("agent")
    task = _item(client, alex, todo["id"], "Fix the build", assignees=["agent"])
    _take(client, ah, task["id"])
    before = _events()

    r = _say(client, ah, task["id"], "terminal: uv run pytest -q", "read_file: docs/board.md")
    assert r.status_code == 204, r.text

    got = client.get(f"{API}/work/{task['id']}/activity", headers=alex).json()
    assert got["by"] == agent["id"]
    assert [line["text"] for line in got["lines"]] == [
        "terminal: uv run pytest -q",
        "read_file: docs/board.md",
    ]
    assert _events() == before  # not a change: no event, so no history line and no webhook
    assert client.get(f"{API}/items/{task['id']}", headers=alex).json()["comments"] == []


def test_only_the_run_that_holds_the_task_may_post(client, alex, todo, make_account):
    _, ah = make_account("agent")
    _, bh = make_account("agent2")
    task = _item(client, alex, todo["id"], "t", assignees=["agent", "agent2"])
    _take(client, ah, task["id"])

    assert _say(client, bh, task["id"], "not mine").status_code == 409
    assert _say(client, ah, task["id"], "wrong proof", run_id="nope").status_code == 409
    assert _say(client, alex, task["id"], "a person").status_code == 409
    assert client.get(f"{API}/work/{task['id']}/activity", headers=alex).json()["lines"] == []

    empty = run_fields(client, ah, task["id"], lines=[])
    url = f"{API}/work/{task['id']}/activity"
    assert client.post(url, json=empty, headers=ah).status_code == 422
    _, reader = make_account("reader", scopes=("read",))
    assert client.post(url, json={"lines": ["x"]}, headers=reader).status_code == 403


def test_the_lines_end_with_the_run_and_the_next_run_starts_over(client, alex, todo, make_account):
    _, ah = make_account("agent")
    task = _item(client, alex, todo["id"], "t", assignees=["agent"])
    _take(client, ah, task["id"])
    assert _say(client, ah, task["id"], "first run").status_code == 204
    r = client.post(
        f"{API}/work/{task['id']}/finish",
        json=run_fields(client, ah, task["id"], outcome="blocked", reply="Which one?"),
        headers=ah,
    )
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/work/{task['id']}/activity", headers=alex).json() == {
        "by": None,
        "lines": [],
    }

    assert client.post(f"{API}/items/{task['id']}/run", headers=alex).status_code == 202
    _take(client, ah, task["id"])
    assert _say(client, ah, task["id"], "second run").status_code == 204
    got = client.get(f"{API}/work/{task['id']}/activity", headers=alex).json()
    assert [line["text"] for line in got["lines"]] == ["second run"]


def test_lines_are_one_line_each_capped_and_the_box_keeps_the_newest():
    assert liveactivity.clean(["  a\nb\t c \x07", "", "   ", "x" * 400]) == [
        "a b c",
        "x" * 299 + "…",
    ]
    store = liveactivity.LiveActivity()
    for n in range(liveactivity.KEEP + 5):
        store.add("item", "acct", "run", [f"line {n}"])
    kept = store.lines("item", "acct")["lines"]
    assert len(kept) == liveactivity.KEEP and kept[-1]["text"] == f"line {liveactivity.KEEP + 4}"
    assert store.lines("item", "someone-else") == {"by": None, "lines": []}
    store.add("item", "acct", "run-2", ["fresh"])
    assert [line["text"] for line in store.lines("item")["lines"]] == ["fresh"]


# --- the live stream -----------------------------------------------------------------------


def _held_task(live):  # noqa: F811
    """A task an agent holds, made through the service layer of the real-server fixture."""
    with live.Session() as s:
        section = section_service.create_section(s, live.actor, name="Work", audience="work")
        lst = list_service.create_list(s, live.actor, section_id=section.id, name="Todo")
        agent = accounts.create_account(
            s, live.actor, handle="my-agent", display_name="My Agent", kind="agent"
        )
        _, token = accounts.create_token(s, live.actor, account_id=agent.id, scopes=("write",))
        item = item_service.create_item(
            s, live.actor, list_id=lst.id, title="Fix the build", assignees=["my-agent"]
        )
        lease = work.take(s, Actor(account_id=agent.id, source=Source.api), item.id)
        return item.id, agent.id, token, lease.run_id


async def test_lines_reach_an_open_board_with_no_id_and_leave_its_cursor_alone(live):  # noqa: F811
    item_id, agent_id, token, run_id = _held_task(live)
    admin = _bearer(live.admin_token)
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        board = await _open(client, stack, f"{API}/events/stream", headers=admin)
        await board.ready()
        polled = (await client.get(f"{API}/events", headers=admin)).json()

        r = await client.post(
            f"{API}/work/{item_id}/activity",
            json={"run_id": run_id, "lines": ["terminal: uv run pytest -q"]},
            headers=_bearer(token),
        )
        assert r.status_code == 204, r.text

        msg = await board.event(timeout=1)
        assert msg.event == "item.activity" and msg.id is None
        assert msg.data["item_id"] == item_id and msg.data["actor_id"] == agent_id
        assert "id" not in msg.data
        said = [line["text"] for line in msg.data["after"]["lines"]]
        assert said == ["terminal: uv run pytest -q"]

        # Not an event anywhere else: polling has nothing new, and a stored event still follows
        # on the same stream with its id (the id-less message never moved the cursor).
        again = (await client.get(f"{API}/events", headers=admin)).json()
        assert again["next_since"] == polled["next_since"]
        (stored,) = live.ping()
        nxt = await board.event(timeout=1)
        assert nxt.id == stored and nxt.event == "test.pinged"


async def test_engines_and_long_polls_do_not_wake_for_a_line(live):  # noqa: F811
    item_id, _, token, run_id = _held_task(live)
    seq = bus.seq
    async with httpx.AsyncClient(base_url=live.url) as client:
        r = await client.post(
            f"{API}/work/{item_id}/activity",
            json={"run_id": run_id, "lines": ["one"]},
            headers=_bearer(token),
        )
    assert r.status_code == 204, r.text
    assert bus.seq == seq


def test_a_guest_never_sees_a_workspace_agents_lines(client, alex, world, db):  # noqa: F811
    """Not by reading them and not on the stream: they go with the rest of what a workspace
    agent says or does as an agent."""
    card = world["ids"]["item_id"][0]
    agent, guest_id = world["agent"], world["ids"]["account_id"][0]
    ah = _headers(client, alex, agent["id"])
    client.post(f"{API}/items/{card}/run", headers=alex)
    _take(client, ah, card)
    assert _say(client, ah, card, "terminal: cat notes.txt").status_code == 204

    assert client.get(f"{API}/work/{card}/activity", headers=alex).json()["lines"]
    seen = client.get(f"{API}/work/{card}/activity", headers=world["guests"]["guest"])
    assert seen.status_code == 200 and seen.json() == {"by": None, "lines": []}

    line = {
        "type": "item.activity",
        "item_id": card,
        "subject_id": card,
        "actor_id": agent["id"],
        "after": {"lines": [{"at": "2026-01-01T00:00:00.000Z", "text": "terminal: cat notes.txt"}]},
    }
    for account_id, hears in ((guest_id, False), (None, True)):
        gate = access.EventGate(account_id or _me(client, alex))
        gate.load(db)
        assert (gate.project(db, line) is not None) is hears


def _headers(client, alex, account_id):
    tok = client.post(
        f"{API}/accounts/{account_id}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return {"Authorization": f"Bearer {tok['token']}"}


def _me(client, alex):
    return client.get(f"{API}/me", headers=alex).json()["account"]["id"]
