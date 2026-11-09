"""A project's guests talk with the project's own agent; what passes between a member and a
workspace agent on that project is members-only (`Message.members_only`, service/chat.py
`members_only_post`). A guest on a shared project must not see a workspace
agent's replies, not even as "Someone"; guests see no trace of a workspace agent."""

from __future__ import annotations

import pytest

from conftest import API
from slate.models import Event, Message


def _account(client, alex, handle, kind, role):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": kind, "role": role},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _share(client, alex, account_id, section_id):
    r = client.put(
        f"{API}/shares",
        json={
            "account_id": account_id,
            "scope_kind": "section",
            "scope_id": section_id,
            "level": "edit",
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def project(client, alex):
    sec = client.post(
        f"{API}/sections", json={"name": "Example Project", "audience": "personal"}, headers=alex
    ).json()
    assert (
        client.patch(
            f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex
        ).status_code
        == 200
    )
    agent, ch = _account(client, alex, "agent", "agent", "member")
    agent2, gh = _account(client, alex, "agent2", "agent", "guest")
    guest, jh = _account(client, alex, "guest", "person", "guest")
    for a in (agent, agent2, guest):
        _share(client, alex, a["id"], sec["id"])
    return {
        "key": f"section:{sec['id']}",
        "sid": sec["id"],
        "agent": (agent, ch),
        "agent2": (agent2, gh),
        "guest": (guest, jh),
    }


def _post(client, h, key, body, **extra):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _bodies(client, h, key):
    r = client.get(f"{API}/channels/{key}/messages", params={"limit": 50}, headers=h)
    assert r.status_code == 200, r.text
    return [m["body_md"] for m in r.json()]


def test_agent_talk_is_hidden_from_guests_but_not_from_alex(client, alex, project, db):
    key = project["key"]
    _, ch = project["agent"]
    _, jh = project["guest"]
    _, gh = project["agent2"]
    _post(client, alex, key, "@agent2 what are our next steps")
    ask = _post(client, alex, key, "@agent check the deploy")
    _post(client, ch, key, "Deploy is fine.")
    reply = _post(client, ch, key, "In the thread too.", parent_id=ask["id"])
    _post(client, jh, key, "Hi from Guest")

    everything = _bodies(client, alex, key)
    assert "@agent check the deploy" in everything and "Deploy is fine." in everything
    for guest in (jh, gh):
        seen = _bodies(client, guest, key)
        assert "@agent2 what are our next steps" in seen and "Hi from Guest" in seen
        assert "@agent check the deploy" not in seen and "Deploy is fine." not in seen
        r = client.get(f"{API}/messages/{reply['id']}/thread", headers=guest)
        assert r.status_code == 404 or reply["id"] not in r.text
    assert db.get(Message, reply["id"]).members_only


def test_guests_hear_no_live_event_and_see_no_agent_seat(client, alex, project, db):
    from slate.api.events import _gate_check
    from slate.service.access import EventGate
    from slate.service.core import event_payload

    key = project["key"]
    agent, ch = project["agent"]
    guest, jh = project["guest"]
    hidden = _post(client, ch, key, "Only for Alex.")
    shown = _post(client, jh, key, "For everyone.")
    me = client.get(f"{API}/me", headers=jh).json()
    gate = EventGate(guest["id"], me["token_id"])
    gate.load(db)

    def posted(mid):
        ev = db.query(Event).filter(Event.type == "message.posted", Event.subject_id == mid).one()
        return event_payload(ev)

    assert not _gate_check(gate, posted(hidden["id"]))
    assert _gate_check(gate, posted(shown["id"]))
    seats = client.get(f"{API}/sections/{project['sid']}/project", headers=jh).json()
    handles = {p["handle"] for p in seats["participants"]}
    assert "agent" not in handles and {"agent2", "guest"} <= handles
    own = client.get(f"{API}/sections/{project['sid']}/project", headers=alex).json()
    assert "agent" in {p["handle"] for p in own["participants"]}


def test_a_guest_cannot_wake_agent_and_agent2_is_not_woken_by_members_only(client, alex, project):
    key = project["key"]
    _, jh = project["guest"]
    agent, agent2 = project["agent"][0]["id"], project["agent2"][0]["id"]
    _post(client, jh, key, "@agent are you there?")
    _post(client, alex, key, "@agent and @agent2, a members-only aside")
    r = client.get(
        f"{API}/events", params={"type": "message.mentioned_agent", "limit": 50}, headers=alex
    )
    woken = [h for e in r.json()["events"] for h in (e["after"] or {}).get("for", [])]
    assert woken == [agent], woken
    # A plain guest question to the project's own agent still wakes it.
    _post(client, jh, key, "@agent2 hello")
    r = client.get(
        f"{API}/events", params={"type": "message.mentioned_agent", "limit": 50}, headers=alex
    )
    woken = [h for e in r.json()["events"] for h in (e["after"] or {}).get("for", [])]
    assert sorted(woken) == sorted([agent, agent2]), woken


def test_an_edit_that_adds_agent_hides_the_message(client, alex, project, db):
    key = project["key"]
    _, jh = project["guest"]
    msg = _post(client, alex, key, "plain note")
    assert "plain note" in _bodies(client, jh, key)
    r = client.patch(
        f"{API}/messages/{msg['id']}", json={"body_md": "plain note @agent"}, headers=alex
    )
    assert r.status_code == 200, r.text
    assert "plain note @agent" not in _bodies(client, jh, key)
    assert db.get(Message, msg["id"]).members_only


def test_a_guest_has_no_door_to_the_agents_own_chat(client, alex, project):
    agent, _ = project["agent"]
    agent2, _ = project["agent2"]
    _, jh = project["guest"]
    keys = {c["key"] for c in client.get(f"{API}/channels", headers=jh).json()}
    assert f"agent:{agent2['id']}" in keys and f"agent:{agent['id']}" not in keys
    r = client.get(f"{API}/channels/agent:{agent['id']}/messages", headers=jh)
    assert r.status_code in (403, 404)


# --- a guest cannot reach a workspace agent from a card or a page ---------------------------


def _card(client, alex, sid, name="Todo", **extra):
    lst = client.post(f"{API}/lists", json={"section_id": sid, "name": name}, headers=alex)
    assert lst.status_code == 201, lst.text
    item = client.post(
        f"{API}/items", json={"list_id": lst.json()["id"], "title": "A3", **extra}, headers=alex
    )
    assert item.status_code == 201, item.text
    return item.json()


def test_a_guest_cannot_ask_assign_or_run_agent_on_a_card(client, alex, project):
    _, jh = project["guest"]
    card = _card(client, alex, project["sid"], assignees=["agent"])
    url = f"{API}/items/{card['id']}"
    # The guest reads the card and the workspace agent's answers, but cannot address it.
    assert client.get(url, headers=jh).status_code == 200
    r = client.post(url + "/comments", json={"body_md": "Agent?", "assignee": "agent"}, headers=jh)
    # Refused, or the agent is not even an account a guest can name.
    assert r.status_code in (403, 422), r.text
    # The agent is not even among the card's assignees for the guest, so there is no agent to run.
    assert client.post(url + "/run", headers=jh).status_code in (403, 422)
    seen = client.get(url, headers=jh).json()
    assert [a["handle"] for a in seen["assignees"]] == []
    assert [a["handle"] for a in client.get(url, headers=alex).json()["assignees"]] == ["agent"]
    other = _card(client, alex, project["sid"], name="Later")
    r = client.patch(f"{API}/items/{other['id']}", json={"assignees": ["agent"]}, headers=jh)
    assert r.status_code in (403, 422), r.text
    # An @agent in the guest's comment notifies no one; the project's agent stays reachable.
    r = client.post(url + "/comments", json={"body_md": "@agent @agent2 hi"}, headers=jh)
    assert r.status_code == 201, r.text
    ev = client.get(
        f"{API}/events", params={"type": "item.commented", "limit": 5}, headers=alex
    ).json()["events"][-1]
    assert ev["after"]["mentions"] == ["agent2"]
    r = client.post(
        url + "/comments", json={"body_md": "Agent2?", "assignee": "agent2"}, headers=jh
    )
    assert r.status_code == 201, r.text
    # Alex still asks the agent as before.
    r = client.post(
        url + "/comments", json={"body_md": "Agent?", "assignee": "agent"}, headers=alex
    )
    assert r.status_code == 201, r.text


def test_a_guest_cannot_ask_or_wake_agent_on_a_page(client, alex, project):
    _, jh = project["guest"]
    lst = client.post(
        f"{API}/lists", json={"section_id": project["sid"], "name": "Docs"}, headers=alex
    ).json()
    doc = client.post(
        f"{API}/docs", json={"list_id": lst["id"], "title": "Plan", "body_md": "x"}, headers=alex
    ).json()
    url = f"{API}/docs/{doc['id']}/comments"
    r = client.post(url, json={"body_md": "Agent?", "assignee": "agent"}, headers=jh)
    assert r.status_code in (403, 422), r.text
    r = client.post(url, json={"body_md": "@agent look"}, headers=jh)
    assert r.status_code == 201, r.text
    woken = client.get(
        f"{API}/events", params={"type": "doc.mentioned_agent", "limit": 20}, headers=alex
    ).json()["events"]
    assert not woken


def test_a_guest_sees_no_trace_of_agent_on_a_card(client, alex, project):
    _, ch = project["agent"]
    _, jh = project["guest"]
    card = _card(client, alex, project["sid"])
    url = f"{API}/items/{card['id']}"
    client.post(url + "/comments", json={"body_md": "For Agent", "assignee": "agent"}, headers=alex)
    client.post(url + "/comments", json={"body_md": "Agent's answer"}, headers=ch)
    client.post(url + "/comments", json={"body_md": "For everyone"}, headers=alex)
    r = client.post(f"{API}/items/{card['id']}/report", json={"state": "working"}, headers=ch)
    assert r.status_code in (200, 201, 422), r.text

    def bodies(h):
        got = client.get(url + "/comments", headers=h).json()
        rows = got if isinstance(got, list) else got.get("comments", [])
        return [c["body_md"] for c in rows]

    assert bodies(alex) == ["For Agent", "Agent's answer", "For everyone"]
    assert bodies(jh) == ["For everyone"]
    history = client.get(f"{API}/events", params={"item_id": card["id"]}, headers=jh).json()
    text = str(history)
    assert "Agent's answer" not in text and "For Agent" not in text
    view = client.get(url, headers=jh).json()
    assert view["agent"] is None or view["agent"]["by"] != project["agent"][0]["id"]


def test_a_guests_live_stream_carries_no_trace_of_agent(client, alex, project, db):
    from slate.service.access import EventGate
    from slate.service.core import event_payload

    agent, ch = project["agent"]
    guest, jh = project["guest"]
    card = _card(client, alex, project["sid"])
    url = f"{API}/items/{card['id']}"
    client.post(url + "/comments", json={"body_md": "For Agent", "assignee": "agent"}, headers=alex)
    client.post(url + "/comments", json={"body_md": "Agent's answer"}, headers=ch)
    client.post(url + "/comments", json={"body_md": "For everyone"}, headers=alex)
    assert client.patch(url, json={"title": "A3 renamed"}, headers=ch).status_code == 200
    me = client.get(f"{API}/me", headers=jh).json()
    gate = EventGate(guest["id"], me["token_id"])
    gate.load(db)
    seen = [
        gate.project(db, event_payload(e))
        for e in db.query(Event).filter(Event.item_id == card["id"]).order_by(Event.id)
    ]
    seen = [p for p in seen if p is not None]
    text = str(seen)
    assert "For Agent" not in text and "Agent's answer" not in text and agent["id"] not in text
    assert "For everyone" in text and "A3 renamed" in text


def _strings(value):
    """Every string value in a JSON body (the handle and name must not be one)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_a_guests_card_history_never_names_agent(client, alex, project):
    agent, ch = project["agent"]
    _, jh = project["guest"]
    card = _card(client, alex, project["sid"])
    url = f"{API}/items/{card['id']}"
    assert (
        client.patch(url, json={"assignees": ["agent", "guest"]}, headers=alex).status_code == 200
    )
    client.post(url + "/comments", json={"body_md": "Agent's answer"}, headers=ch)
    client.post(url + "/comments", json={"body_md": "For everyone"}, headers=alex)
    hist = client.get(url + "/events", headers=jh)
    assert hist.status_code == 200, hist.text
    text = str(hist.json())
    words = _strings(hist.json())
    assert not any(w.lower() == "agent" or "agent's answer" in w.lower() for w in words)
    assert agent["id"] not in text
    assert "For everyone" in text and "guest" in text
    mine = str(client.get(url + "/events", headers=alex).json())
    assert "Agent's answer" in mine
