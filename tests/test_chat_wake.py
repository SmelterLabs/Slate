"""A chat @mention starts a runner-backed agent (docs/chat.md):
the wake is an event (`message.mentioned_agent`), the turn is a lease the runner takes
with `POST /work/next {channel, thread_id}`, the reply is posted with the ordinary chat `post`,
and one open turn per agent per channel-thread folds later mentions into it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import API
from conftest import workspace_events as _events
from run_proofs import remember, run_fields
from slate.models import ChatWake, utcnow
from slate.service import chatwake


def _agent(client, alex, handle="agent", wake="push"):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "agent", "wake": wake},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _post(client, h, key, body, **extra):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _finish(client, h, **body):
    body = run_fields(client, h, (body["channel"], body.get("thread_id")), **body)
    r = client.post(f"{API}/work/chat/finish", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# --- the wake -------------------------------------------------------------------------------


def test_a_mention_by_a_person_wakes_a_push_agent(client, alex, todo):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    msg = _post(client, alex, key, "@agent can you summarise this list?")

    wakes = _events(client, alex, "message.mentioned_agent")
    assert len(wakes) == 1
    after = wakes[0]["after"]
    assert after == {
        **after,
        "channel": key,
        "channel_name": "Todo",
        "thread_id": None,
        "message_id": msg["id"],
        "for": [agent["id"]],
        "queued": [],
    }
    assert wakes[0]["subject_id"] == msg["id"]

    # The runner takes it for that channel: the turn carries the message and the recent ones.
    got = _next(client, ch, channel=key)
    assert got["item"] is None and got["lease"] is None
    assert got["why"] == "mentioned"
    turn = got["chat"]
    assert turn["channel"] == key and turn["thread_id"] is None
    assert turn["message_id"] == msg["id"] and turn["since_id"] == msg["id"]
    assert turn["message"]["body_md"] == "@agent can you summarise this list?"
    assert [m["id"] for m in turn["messages"]] == [msg["id"]]
    assert turn["lease"]["account_id"] == agent["id"]
    started = _events(client, alex, "message.agent_turn")
    assert started[-1]["after"]["state"] == "started"
    assert started[-1]["actor_id"] == agent["id"]

    # Asking again resumes the same turn (lease renewed), and nothing else is free.
    again = _next(client, ch, channel=key, run_id=got["chat"]["lease"]["run_id"])
    assert again["chat"]["message_id"] == msg["id"] and again["resumed"] is True
    assert _next(client, ch, resume=False)["chat"] is None

    # The reply goes to the channel with the ordinary post; the turn ends with it.
    done = _finish(client, ch, channel=key, outcome="done", reply="Three open tasks, one overdue.")
    assert done["message"]["body_md"] == "Three open tasks, one overdue."
    assert done["message"]["parent_id"] is None and done["pending"] is False
    page = client.get(f"{API}/channels/{key}/messages", headers=alex).json()
    assert [m["author_id"] for m in page] == [page[0]["author_id"], agent["id"]]
    ended = _events(client, alex, "message.agent_turn")[-1]["after"]
    assert ended["state"] == "done" and ended["reply_id"] == done["message"]["id"]
    assert _next(client, ch, channel=key)["chat"] is None


def test_a_mention_in_a_thread_is_answered_in_that_thread(client, alex, todo):
    _, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    root = _post(client, alex, key, "Planning the weekend")
    _post(client, alex, key, "unrelated chatter")
    ask = _post(client, alex, key, "@agent what is due Saturday?", parent_id=root["id"])

    got = _next(client, ch, channel=key, thread_id=root["id"])
    turn = got["chat"]
    assert turn["thread_id"] == root["id"] and turn["message_id"] == ask["id"]
    # The thread is the context, not the channel.
    assert [m["id"] for m in turn["messages"]] == [root["id"], ask["id"]]
    # The channel-level turn is a different key: nothing there.
    assert _next(client, ch, channel=key, resume=False)["chat"] is None

    done = _finish(client, ch, channel=key, thread_id=root["id"], outcome="done", reply="Nothing.")
    assert done["message"]["parent_id"] == root["id"]
    thread = client.get(f"{API}/messages/{root['id']}/thread", headers=alex).json()
    assert [m["body_md"] for m in thread][-1] == "Nothing."


def test_a_generic_next_hands_out_chat_turns_after_items(client, alex, todo):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    item = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Do it", "assignees": ["agent"]},
        headers=alex,
    ).json()
    _post(client, alex, key, "@agent hello")
    first = _next(client, ch)
    assert first["item"]["id"] == item["id"] and first["chat"] is None
    assert first["remaining"] == 1
    client.post(f"{API}/work/{item['id']}/finish", json={"outcome": "done"}, headers=ch)
    second = _next(client, ch)
    assert second["item"] is None and second["chat"]["channel"] == key
    assert second["why"] == "mentioned" and second["remaining"] == 0


def test_remaining_counts_in_database_without_loading_wakes(client, alex, todo, db):
    from sqlalchemy import event

    agent, _ = _agent(client, alex)
    _post(client, alex, f"list:{todo['id']}", "@agent count this")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        assert chatwake.remaining(db, agent["id"]) == 1
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "count(" in statements[0].lower()


# --- guardrails -----------------------------------------------------------------------------


def test_a_second_mention_while_a_turn_is_open_queues_into_it(client, alex, todo):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    first = _post(client, alex, key, "@agent one")
    got = _next(client, ch, channel=key)
    assert got["chat"]["message_id"] == first["id"]

    second = _post(client, alex, key, "@agent two")
    wakes = _events(client, alex, "message.mentioned_agent")
    assert wakes[-1]["after"]["message_id"] == second["id"]
    assert wakes[-1]["after"]["queued"] == [agent["id"]] and wakes[-1]["after"]["for"] == []
    # No second run: a fresh take (not a resume) finds nothing; the open turn is still the one.
    assert _next(client, ch, channel=key, resume=False)["chat"] is None
    assert (
        _next(client, ch, channel=key, run_id=got["chat"]["lease"]["run_id"])["chat"]["message_id"]
        == first["id"]
    )

    # The run finishes having answered the first: the second waits as a new turn.
    done = _finish(client, ch, channel=key, outcome="done", reply="one answered")
    assert done["pending"] is True
    ended = _events(client, alex, "message.agent_turn")[-1]["after"]
    assert ended["state"] == "done" and ended["pending"] is True
    nxt = _next(client, ch, channel=key)
    assert nxt["chat"]["since_id"] == second["id"] and nxt["chat"]["message_id"] == second["id"]
    assert nxt["resumed"] is False


def test_an_agent_mentioning_an_agent_does_not_wake_it(client, alex, todo):
    _, agent_h = _agent(client, alex, "agent")
    _, agent2_h = _agent(client, alex, "agent2")
    key = f"list:{todo['id']}"
    _post(client, agent_h, key, "@agent2 over to you")
    assert _events(client, alex, "message.mentioned_agent") == []
    assert _next(client, agent2_h, channel=key)["chat"] is None
    # The mention itself still reaches agent2's inbox, as before.
    inbox = client.get(f"{API}/inbox", headers=agent2_h).json()
    assert [e["reason"] for e in inbox] == ["mention"]


def test_pull_and_manual_agents_are_not_woken(client, alex, todo):
    _, pull_h = _agent(client, alex, "agent3", wake="pull")
    _, manual_h = _agent(client, alex, "agent4", wake="manual")
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent3 @agent4 anyone?")
    assert _events(client, alex, "message.mentioned_agent") == []
    assert _next(client, pull_h, channel=key)["chat"] is None
    assert _next(client, manual_h, channel=key)["chat"] is None


def test_an_agent_that_cannot_read_the_channel_is_not_woken(client, alex, todo):
    agent, ch = _agent(client, alex)
    r = client.patch(f"{API}/sections/{todo['section_id']}", json={"private": True}, headers=alex)
    assert r.status_code == 200, r.text
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent are you there?")
    assert _events(client, alex, "message.mentioned_agent") == []
    # Nothing to take, and the channel itself is not there for the agent.
    assert client.get(f"{API}/channels/{key}", headers=ch).status_code == 404
    assert _next(client, ch, channel=key)["chat"] is None

    # Shared with the agent: the next mention wakes it.
    r = client.put(
        f"{API}/shares",
        json={
            "account_id": agent["id"],
            "scope_kind": "list",
            "scope_id": todo["id"],
            "level": "comment",
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text
    msg = _post(client, alex, key, "@agent now?")
    assert _events(client, alex, "message.mentioned_agent")[-1]["after"]["message_id"] == msg["id"]
    assert _next(client, ch, channel=key)["chat"]["message_id"] == msg["id"]


def test_an_edit_that_adds_the_mention_wakes_too(client, alex, todo):
    _, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    msg = _post(client, alex, key, "can someone summarise this?")
    assert _events(client, alex, "message.mentioned_agent") == []
    r = client.patch(
        f"{API}/messages/{msg['id']}",
        json={"body_md": "@agent can you summarise this?"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert _events(client, alex, "message.mentioned_agent")[-1]["after"]["message_id"] == msg["id"]
    assert _next(client, ch, channel=key)["chat"]["message_id"] == msg["id"]


# --- the turn's end -------------------------------------------------------------------------


def test_the_agents_own_post_cannot_end_a_fenced_turn(client, alex, todo):
    """An ordinary post does not carry execution proof, even from the same account."""
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    root = _post(client, alex, key, "@agent thoughts?")
    got = _next(client, ch, channel=key)
    # A post in another thread or channel does not count.
    _post(client, ch, key, "aside", parent_id=root["id"])
    assert (
        _next(client, ch, channel=key, run_id=got["chat"]["lease"]["run_id"])["chat"]["message_id"]
        == root["id"]
    )
    _post(client, ch, key, "Here are my thoughts")
    ended = _events(client, alex, "message.agent_turn")[-1]["after"]
    assert ended["state"] == "started"
    _finish(client, ch, channel=key, outcome="done")
    assert _next(client, ch, channel=key)["chat"] is None


def test_release_ends_the_turn_without_posting_and_a_person_can_ask_again(client, alex, todo):
    _, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent ping")
    _next(client, ch, channel=key)
    out = _finish(client, ch, channel=key, outcome="release", note="Runtime unreachable")
    assert out["message"] is None and out["pending"] is False
    ended = _events(client, alex, "message.agent_turn")[-1]["after"]
    assert ended["state"] == "released" and ended["note"] == "Runtime unreachable"
    page = client.get(f"{API}/channels/{key}/messages", headers=alex).json()
    assert len(page) == 1  # nothing was posted into the collaborator's chat
    assert _next(client, ch, channel=key)["chat"] is None
    # Not a loop: only a new mention starts a new turn.
    _post(client, alex, key, "@agent ping again")
    assert _next(client, ch, channel=key)["chat"] is not None


def test_heartbeat_keeps_the_turn_and_expiry_drops_it(client, alex, todo, db):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    msg = _post(client, alex, key, "@agent slow one")
    got = _next(client, ch, channel=key, lease_seconds=60)
    until = got["chat"]["lease"]["until"]
    r = client.post(
        f"{API}/work/chat/heartbeat",
        json=run_fields(client, ch, (key, None), **{"channel": key, "lease_seconds": 600}),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert r.json()["until"] > until
    # A heartbeat is bookkeeping: no event.
    assert [e["after"]["state"] for e in _events(client, alex, "message.agent_turn")] == ["started"]

    row = db.get(ChatWake, (agent["id"], key, ""))
    assert row is not None and row.taken_id == msg["id"]
    released = chatwake.expire(db, utcnow() + timedelta(hours=1))
    assert released == 1
    ended = _events(client, alex, "message.agent_turn")[-1]
    assert ended["after"]["state"] == "expired" and ended["source"] == "system"
    held = db.get(ChatWake, (agent["id"], key, ""))
    assert held is not None and held.until is None and held.run_id
    assert _next(client, ch, channel=key)["chat"] is None


def test_finish_or_heartbeat_without_a_turn_is_a_409(client, alex, todo):
    _, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    r = client.post(
        f"{API}/work/chat/heartbeat",
        json=run_fields(client, ch, (key, None), **{"channel": key}),
        headers=ch,
    )
    assert r.status_code == 409, r.text
    r = client.post(f"{API}/work/chat/finish", json={"channel": key, "outcome": "done"}, headers=ch)
    assert r.status_code == 409, r.text
    r = client.post(f"{API}/work/next", json={"thread_id": "abc"}, headers=ch)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("outcome", ["review", "blocked", "nope"])
def test_chat_finish_takes_only_done_or_release(client, alex, todo, outcome):
    _, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent hi")
    _next(client, ch, channel=key)
    r = client.post(
        f"{API}/work/chat/finish", json={"channel": key, "outcome": outcome}, headers=ch
    )
    assert r.status_code == 422, r.text


# --- the board's initial state ---------------------------------------------------------------


def _turns(client, h, key):
    one = client.get(f"{API}/channels/{key}", headers=h)
    assert one.status_code == 200, one.text
    listed = next(c for c in client.get(f"{API}/channels", headers=h).json() if c["key"] == key)
    assert listed["agent_turns"] == one.json()["agent_turns"]
    return [(t["agent_id"], t["thread_id"], t["state"]) for t in one.json()["agent_turns"]]


def test_the_channel_lists_its_open_agent_turns(client, alex, todo, db):
    """A board that loads mid-turn shows who is working (`agent_turns` on GET /channels)."""
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    assert _turns(client, alex, key) == []

    _post(client, alex, key, "@agent summarise this")
    assert _turns(client, alex, key) == [(agent["id"], None, "waiting")]
    _next(client, ch, channel=key)
    assert _turns(client, alex, key) == [(agent["id"], None, "working")]
    _finish(client, ch, channel=key, outcome="done", reply="Done.")
    assert _turns(client, alex, key) == []

    # A thread turn names its thread.
    root = _post(client, alex, key, "Weekend plans")
    _post(client, alex, key, "@agent anything Saturday?", parent_id=root["id"])
    _next(client, ch, channel=key, thread_id=root["id"])
    assert _turns(client, alex, key) == [(agent["id"], root["id"], "working")]
    _finish(client, ch, channel=key, thread_id=root["id"], outcome="release")
    assert _turns(client, alex, key) == []

    # A lease that ran out is not working any more (expire ended it).
    _post(client, alex, key, "@agent slow one")
    _next(client, ch, channel=key, lease_seconds=60)
    chatwake.expire(db, utcnow() + timedelta(hours=1))
    assert _turns(client, alex, key) == []
