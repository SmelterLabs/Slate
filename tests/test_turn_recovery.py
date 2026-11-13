"""A chat or doc turn never strands the agent's answers there.

- A lease that ran out (the runner died, lost its run proof, or was replaced) is shown on the
  board (`expired`), and a person who may post there clears it (`POST /work/chat/clear`,
  `POST /work/doc/clear`, MCP `slate_work chat_clear` / `doc_clear`).
- A person's new mention after the lease ran out is not swallowed by the dead turn: it ends
  the quarantine and is a new turn the agent is handed.
- A pending turn the agent can no longer answer (its share was lowered or removed, the place is
  gone) is dropped, never a 403 on every `/work/next`.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import API
from run_proofs import remember
from slate.models import ChatWake, DocWake, utcnow
from slate.service import chatwake, docwake
from test_chat_wake import _agent, _events, _post


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _person(client, alex, handle="sam", role="member"):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "person", "role": role},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _share(client, alex, account_id, kind, scope_id, level="edit"):
    r = client.put(
        f"{API}/shares",
        json={"account_id": account_id, "scope_kind": kind, "scope_id": scope_id, "level": level},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _private_section(client, alex, name):
    sec = client.post(
        f"{API}/sections", json={"name": name, "audience": "personal"}, headers=alex
    ).json()
    r = client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    assert r.status_code == 200, r.text
    return sec


def _turns(client, h, key):
    r = client.get(f"{API}/channels/{key}", headers=h)
    assert r.status_code == 200, r.text
    return [(t["agent_id"], t["thread_id"], t["state"]) for t in r.json()["agent_turns"]]


def _expire_chat(db):
    assert chatwake.expire(db, utcnow() + timedelta(hours=1)) == 1
    db.expire_all()


def _expire_doc(db):
    assert docwake.expire(db, utcnow() + timedelta(hours=1)) == 1
    db.expire_all()


# --- chat: an expired turn ------------------------------------------------------------------


def test_an_expired_chat_turn_shows_on_the_board(client, alex, todo, db):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent slow one")
    _next(client, ch, channel=key, lease_seconds=60)
    _expire_chat(db)
    assert _turns(client, alex, key) == [(agent["id"], None, "expired")]


def test_a_new_mention_after_expiry_is_a_new_turn(client, alex, todo, db):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent hello")
    _next(client, ch, channel=key, lease_seconds=60)
    _expire_chat(db)

    again = _post(client, alex, key, "@agent are you there?")
    wake = _events(client, alex, "message.mentioned_agent")[-1]["after"]
    assert wake["for"] == [agent["id"]] and wake["queued"] == []
    # The dead turn is settled by the person's mention, in the history.
    ended = _events(client, alex, "message.agent_turn")[-1]
    assert ended["after"]["state"] == "released" and ended["after"]["reconciled"] is True
    assert ended["actor_id"] != agent["id"] and ended["source"] != "system"
    assert _turns(client, alex, key) == [(agent["id"], None, "waiting")]

    got = _next(client, ch, channel=key)
    assert got["chat"] is not None and got["chat"]["message_id"] == again["id"]


def test_a_mention_on_a_lease_nobody_swept_yet_is_not_swallowed(client, alex, todo, db):
    """The lease ran out but the scheduler has not run: the mention still starts a turn."""
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent hello")
    _next(client, ch, channel=key, lease_seconds=60)
    row = db.get(ChatWake, (agent["id"], key, ""))
    old_run = row.run_id
    from slate.service.core import Actor, apply_change

    # Move the lease into the past without the sweep (until is bookkeeping).
    with apply_change(db, Actor(account_id=agent["id"], source="api")):
        row.until = utcnow() - timedelta(minutes=5)
    _post(client, alex, key, "@agent still there?")
    assert _events(client, alex, "message.mentioned_agent")[-1]["after"]["for"] == [agent["id"]]
    got = _next(client, ch, channel=key)
    assert got["chat"] is not None and got["chat"]["lease"]["run_id"] != old_run


def test_a_person_clears_an_expired_chat_turn(client, alex, todo, db):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent first")
    _next(client, ch, channel=key, lease_seconds=60)
    # A mention while the turn was open joins it; the run then dies.
    late = _post(client, alex, key, "@agent and a second thing")
    _expire_chat(db)
    assert _next(client, ch, channel=key)["chat"] is None

    body = {"channel": key, "agent_id": agent["id"]}
    r = client.post(f"{API}/work/chat/clear", json=body, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json() == {"cleared": True, "pending": True}
    ended = _events(client, alex, "message.agent_turn")[-1]
    assert ended["after"]["state"] == "released"
    assert ended["after"]["reconciled"] is True and ended["after"]["pending"] is True
    assert "cleared" in ended["after"]["evidence"]
    # The mention that waited is the agent's next turn.
    got = _next(client, ch, channel=key)
    assert got["chat"]["message_id"] == late["id"]
    # Nothing left to clear: a live turn is never overridden.
    r = client.post(f"{API}/work/chat/clear", json=body, headers=alex)
    assert r.status_code == 409, r.text


def test_clearing_a_chat_turn_with_nothing_waiting_removes_it(client, alex, todo, db):
    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent first")
    _next(client, ch, channel=key, lease_seconds=60)
    _expire_chat(db)
    r = client.post(
        f"{API}/work/chat/clear", json={"channel": key, "agent_id": agent["id"]}, headers=alex
    )
    assert r.status_code == 200 and r.json() == {"cleared": True, "pending": False}
    db.expire_all()
    assert db.get(ChatWake, (agent["id"], key, "")) is None
    assert _turns(client, alex, key) == []


def test_only_a_person_who_may_post_there_clears_a_chat_turn(client, alex, db):
    sec = _private_section(client, alex, "Project")
    key = f"section:{sec['id']}"
    agent, ch = _agent(client, alex)
    other, oh = _agent(client, alex, handle="other-agent")
    viewer, vh = _person(client, alex, "sam", role="guest")
    _share(client, alex, agent["id"], "section", sec["id"])
    _share(client, alex, other["id"], "section", sec["id"])
    _share(client, alex, viewer["id"], "section", sec["id"], level="view")
    _post(client, alex, key, "@agent please look")
    _next(client, ch, channel=key, lease_seconds=60)
    _expire_chat(db)
    body = {"channel": key, "agent_id": agent["id"]}
    assert client.post(f"{API}/work/chat/clear", json=body, headers=vh).status_code == 403
    # An agent settles its own turn with its run proof, never another agent's by clearing.
    assert client.post(f"{API}/work/chat/clear", json=body, headers=oh).status_code == 403
    assert client.post(f"{API}/work/chat/clear", json=body, headers=ch).status_code == 403
    assert client.post(f"{API}/work/chat/clear", json=body, headers=alex).status_code == 200


# --- docs: the same ---------------------------------------------------------------------------


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comment(client, h, doc_id, **body):
    r = client.post(f"{API}/docs/{doc_id}/comments", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_new_doc_mention_after_expiry_is_a_new_turn(client, alex, db):
    agent, ch = _agent(client, alex)
    page = _doc(client, alex, title="Roof", body_md="Notes")
    root = _comment(client, alex, page["id"], body_md="@agent price this")
    _next(client, ch, doc_id=page["id"], thread_id=root["id"], lease_seconds=60)
    _expire_doc(db)

    _comment(client, alex, page["id"], body_md="@agent any news?", parent_id=root["id"])
    wake = _events(client, alex, "doc.mentioned_agent")[-1]["after"]
    assert wake["for"] == [agent["id"]] and wake["queued"] == []
    ended = _events(client, alex, "doc.agent_turn")[-1]["after"]
    assert ended["state"] == "released" and ended["reconciled"] is True
    got = _next(client, ch, doc_id=page["id"], thread_id=root["id"])
    assert got["doc"] is not None


def test_a_person_clears_an_expired_doc_turn(client, alex, db):
    agent, ch = _agent(client, alex)
    page = _doc(client, alex, title="Roof", body_md="Ask @agent to price it")
    _next(client, ch, doc_id=page["id"], lease_seconds=60)
    _expire_doc(db)
    assert _next(client, ch, doc_id=page["id"])["doc"] is None

    body = {"doc_id": page["id"], "agent_id": agent["id"]}
    assert client.post(f"{API}/work/doc/clear", json=body, headers=ch).status_code == 403
    r = client.post(f"{API}/work/doc/clear", json=body, headers=alex)
    assert r.status_code == 200 and r.json() == {"cleared": True, "pending": False}
    ended = _events(client, alex, "doc.agent_turn")[-1]["after"]
    assert ended["state"] == "released" and ended["reconciled"] is True
    db.expire_all()
    assert db.get(DocWake, (agent["id"], page["id"], "")) is None
    assert client.post(f"{API}/work/doc/clear", json=body, headers=alex).status_code == 409


# --- a pending turn the agent can no longer answer -------------------------------------------


def test_a_turn_the_agent_can_no_longer_answer_is_dropped(client, alex, db):
    sec = _private_section(client, alex, "Project")
    other = _private_section(client, alex, "Other")
    agent, ch = _agent(client, alex, handle="my-agent")
    _share(client, alex, agent["id"], "section", sec["id"])
    _share(client, alex, agent["id"], "section", other["id"])
    _post(client, alex, f"section:{sec['id']}", "@my-agent please look")
    _share(client, alex, agent["id"], "section", sec["id"], level="view")
    answerable = _post(client, alex, f"section:{other['id']}", "@my-agent and here")

    got = _next(client, ch)
    assert got["chat"] is not None and got["chat"]["message_id"] == answerable["id"]
    dropped = [
        e["after"]
        for e in _events(client, alex, "message.agent_turn")
        if e["after"]["state"] == "dropped"
    ]
    assert [d["channel"] for d in dropped] == [f"section:{sec['id']}"]
    db.expire_all()
    assert db.get(ChatWake, (agent["id"], f"section:{sec['id']}", "")) is None


def test_a_doc_turn_the_agent_can_no_longer_answer_is_dropped(client, alex, db):
    sec = _private_section(client, alex, "Project")
    agent, ch = _agent(client, alex, handle="my-agent")
    _share(client, alex, agent["id"], "section", sec["id"])
    page = _doc(client, alex, title="Plan", body_md="Ask @my-agent", section_id=sec["id"])
    db.expire_all()
    assert db.get(DocWake, (agent["id"], page["id"], "")) is not None
    _share(client, alex, agent["id"], "section", sec["id"], level="view")
    r = client.post(f"{API}/work/next", json={}, headers=ch)
    assert r.status_code == 200, r.text
    assert r.json()["doc"] is None
    assert _events(client, alex, "doc.agent_turn")[-1]["after"]["state"] == "dropped"
    db.expire_all()
    assert db.get(DocWake, (agent["id"], page["id"], "")) is None


def test_the_sweep_drops_turns_out_of_the_agents_reach(client, alex, db):
    sec = _private_section(client, alex, "Project")
    agent, _ = _agent(client, alex, handle="my-agent")
    share = _share(client, alex, agent["id"], "section", sec["id"])
    _post(client, alex, f"section:{sec['id']}", "@my-agent please look")
    r = client.delete(f"{API}/shares/{share['id']}", headers=alex)
    assert r.status_code in (200, 204), r.text
    assert chatwake.remaining(db, agent["id"]) == 1  # the row the agent can no longer see
    chatwake.expire(db, utcnow())
    db.expire_all()
    assert chatwake.remaining(db, agent["id"]) == 0
    assert _events(client, alex, "message.agent_turn")[-1]["after"]["state"] == "dropped"


# --- MCP ------------------------------------------------------------------------------------


async def test_a_person_clears_expired_turns_through_mcp(client, alex, todo, db):
    from test_mcp import call, http_mcp, ok

    agent, ch = _agent(client, alex)
    key = f"list:{todo['id']}"
    _post(client, alex, key, "@agent first")
    _next(client, ch, channel=key, lease_seconds=60)
    page = _doc(client, alex, title="Roof", body_md="Ask @agent to price it")
    _next(client, ch, doc_id=page["id"], lease_seconds=60)
    chatwake.expire(db, utcnow() + timedelta(hours=1))
    docwake.expire(db, utcnow() + timedelta(hours=1))
    async with http_mcp(alex) as mcp:
        got = ok(await call(mcp, "slate_work", "chat_clear", channel=key, agent_id=agent["id"]))
        assert got == {"cleared": True, "pending": False}
        got = ok(
            await call(mcp, "slate_work", "doc_clear", doc_id=page["id"], agent_id=agent["id"])
        )
        assert got == {"cleared": True, "pending": False}
