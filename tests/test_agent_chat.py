"""An agent's own chat (`agent:<id>`): talk to an agent about anything, not a card.

Workspace-agent direct chats implicitly mention the agent; guest-role project agents need an
explicit `@handle` (test_project_agent_mentions.py). Members and admins read every agent's
chat; a project's guests read and talk in the chat of each agent shared into that project, and
no other agent's."""

from __future__ import annotations

from conftest import API
from conftest import workspace_events as _events


def _agent(client, alex, handle="agent", wake="push", role="member"):
    # A project's own agent is a guest; a member agent is the workspace's,
    # which a project's guests never see (docs/collaboration.md "Members-only chat").
    r = client.post(
        f"{API}/accounts",
        json={
            "handle": handle,
            "display_name": handle.title(),
            "kind": "agent",
            "wake": wake,
            "role": role,
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _person(client, alex, handle, role):
    acct = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "person", "role": role},
        headers=alex,
    ).json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _post(client, h, key, body, **extra):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_each_agent_has_a_chat_after_general(client, alex, todo):
    agent, _ = _agent(client, alex, "agent")
    agent3, _ = _agent(client, alex, "agent3")
    _person(client, alex, "member", "member")  # people get no agent chat
    chans = client.get(f"{API}/channels", headers=alex).json()
    keys = [c["key"] for c in chans]
    assert keys[:3] == ["general", f"agent:{agent['id']}", f"agent:{agent3['id']}"]
    agent3_row = chans[2]
    assert agent3_row["kind"] == "agent" and agent3_row["name"] == "Agent3"
    assert agent3_row["account_id"] == agent3["id"] and agent3_row["list_id"] is None
    assert f"section:{todo['section_id']}" in keys and f"list:{todo['id']}" in keys
    # Slate's own system account is never one.
    assert all(c["name"] != "Slate" for c in chans if c["kind"] == "agent")
    one = client.get(f"{API}/channels/agent:{agent['id']}", headers=alex).json()
    assert one["kind"] == "agent" and one["account_id"] == agent["id"]


def test_a_person_account_or_unknown_id_is_not_an_agent_chat(client, alex):
    member, _ = _person(client, alex, "member", "member")
    for key in (f"agent:{member['id']}", "agent:nosuchagent0"):
        r = client.get(f"{API}/channels/{key}/messages", headers=alex)
        assert r.status_code == 404, r.text


def test_a_message_in_an_agents_chat_wakes_it_without_a_mention(client, alex):
    agent, ch = _agent(client, alex)
    key = f"agent:{agent['id']}"
    msg = _post(client, alex, key, "What did we decide about the deploy timer?")
    assert msg["mentions"] == ["agent"]

    wake = _events(client, alex, "message.mentioned_agent")[-1]["after"]
    assert wake["channel"] == key and wake["for"] == [agent["id"]]
    assert wake["channel_name"] == "Agent"

    r = client.post(f"{API}/work/next", json={"channel": key}, headers=ch)
    turn = r.json()["chat"]
    assert turn["message_id"] == msg["id"] and turn["channel_name"] == "Agent"

    r = client.post(
        f"{API}/work/chat/finish",
        json={
            "channel": key,
            "outcome": "done",
            "reply": "We kept it at 03:00.",
            "run_id": turn["lease"]["run_id"],
        },
        headers=ch,
    )
    assert r.status_code == 200, r.text
    reply = r.json()["message"]
    # The agent's own post does not mention itself, and is unread for Alex.
    assert reply["mentions"] == [] and reply["channel"] == key
    row = client.get(f"{API}/channels/{key}", headers=alex).json()
    assert row["unread"] == 1 and row["mentions"] == 0


def test_the_agent_hears_it_in_its_inbox_even_when_not_push(client, alex):
    agent4, ph = _agent(client, alex, "agent4", wake="pull")
    key = f"agent:{agent4['id']}"
    _post(client, alex, key, "Could you tidy the Todo list tomorrow?")
    assert not [
        e for e in _events(client, alex, "message.mentioned_agent") if e["after"]["channel"] == key
    ]
    inbox = client.get(f"{API}/inbox", headers=ph).json()
    entries = inbox["entries"] if isinstance(inbox, dict) else inbox
    assert any(e.get("channel") == key for e in entries), entries


def test_an_explicit_mention_is_not_doubled(client, alex):
    agent, _ = _agent(client, alex)
    msg = _post(client, alex, f"agent:{agent['id']}", "@agent hello")
    assert msg["mentions"] == ["agent"]


def test_a_disabled_agents_chat_is_archived(client, alex):
    agent, _ = _agent(client, alex)
    key = f"agent:{agent['id']}"
    r = client.patch(f"{API}/accounts/{agent['id']}", json={"disabled": True}, headers=alex)
    assert r.status_code == 200, r.text
    keys = [c["key"] for c in client.get(f"{API}/channels", headers=alex).json()]
    assert key not in keys
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": "hi"}, headers=alex)
    assert r.status_code == 422, r.text


def test_members_see_every_agent_chat(client, alex, todo):
    agent, _ = _agent(client, alex)
    key = f"agent:{agent['id']}"
    _post(client, alex, key, "a word with agent")
    # A private list makes members' access restricted (the read filter applies).
    r = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "Secret"}, headers=alex
    )
    secret = r.json()
    client.patch(f"{API}/lists/{secret['id']}", json={"private": True}, headers=alex)

    _, mh = _person(client, alex, "member2", "member")
    assert key in [c["key"] for c in client.get(f"{API}/channels", headers=mh).json()]
    page = client.get(f"{API}/channels/{key}/messages", headers=mh)
    assert page.status_code == 200 and [m["body_md"] for m in page.json()] == ["a word with agent"]


def _share(client, alex, account_id, kind, scope_id, level="edit"):
    r = client.put(
        f"{API}/shares",
        json={"account_id": account_id, "scope_kind": kind, "scope_id": scope_id, "level": level},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _project(client, alex, name="Example Project"):
    sec = client.post(f"{API}/sections", json={"name": name, "audience": "work"}, headers=alex)
    sec = sec.json()
    r = client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    assert r.status_code == 200, r.text
    lst = client.post(f"{API}/lists", json={"section_id": sec["id"], "name": "Work"}, headers=alex)
    return sec, lst.json()


def _keys(client, h):
    r = client.get(f"{API}/channels", headers=h)
    assert r.status_code == 200, r.text
    return [c["key"] for c in r.json()]


def test_a_projects_guests_read_and_talk_in_its_agents_chat(client, alex):
    sec, _ = _project(client, alex)
    agent2, gh_agent = _agent(client, alex, "agent2", role="guest")
    agent, _ = _agent(client, alex, "agent")  # not in the project
    _share(client, alex, agent2["id"], "section", sec["id"])
    guest, gh = _person(client, alex, "guest", "guest")
    _share(client, alex, guest["id"], "section", sec["id"])
    agent2_key, agent_key = f"agent:{agent2['id']}", f"agent:{agent['id']}"

    # What the project's agent says, the guest sees.
    _post(client, gh_agent, agent2_key, "The revised draft is uploaded.")
    keys = _keys(client, gh)
    assert agent2_key in keys and agent_key not in keys and "general" not in keys
    assert f"section:{sec['id']}" in keys
    page = client.get(f"{API}/channels/{agent2_key}/messages", headers=gh)
    assert page.status_code == 200, page.text
    assert [m["body_md"] for m in page.json()] == ["The revised draft is uploaded."]
    assert client.get(f"{API}/channels/{agent2_key}", headers=gh).json()["unread"] == 1

    # The guest explicitly addresses the project agent; ordinary conversation stays quiet.
    asked = _post(client, gh, agent2_key, "@agent2 Which draft is current?")
    assert asked["mentions"] == ["agent2"]
    wake = _events(client, alex, "message.mentioned_agent")[-1]["after"]
    assert wake["channel"] == agent2_key and wake["for"] == [agent2["id"]]
    assert any(e["subject_id"] == asked["id"] for e in _events(client, gh, "message.posted"))

    # An agent outside the guest's projects stays out of reach.
    assert client.get(f"{API}/channels/{agent_key}/messages", headers=gh).status_code == 404
    r = client.post(f"{API}/channels/{agent_key}/messages", json={"body_md": "hi"}, headers=gh)
    assert r.status_code in (403, 404), r.text


def test_a_view_only_guest_reads_but_cannot_post(client, alex):
    sec, _ = _project(client, alex)
    agent2, gh_agent = _agent(client, alex, "agent2", role="guest")
    _share(client, alex, agent2["id"], "section", sec["id"])
    guest2, vh = _person(client, alex, "guest2", "guest")
    _share(client, alex, guest2["id"], "section", sec["id"], level="view")
    key = f"agent:{agent2['id']}"
    _post(client, gh_agent, key, "Status: on track.")
    assert client.get(f"{API}/channels/{key}/messages", headers=vh).status_code == 200
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": "ok"}, headers=vh)
    assert r.status_code == 403, r.text


def test_an_agent_shared_through_a_list_counts_and_unsharing_takes_it_away(client, alex):
    sec, work = _project(client, alex)
    agent2, _ = _agent(client, alex, "agent2", role="guest")
    share = _share(client, alex, agent2["id"], "list", work["id"])
    guest, gh = _person(client, alex, "guest", "guest")
    _share(client, alex, guest["id"], "section", sec["id"])
    key = f"agent:{agent2['id']}"
    assert key in _keys(client, gh)
    r = client.delete(f"{API}/shares/{share['id']}", headers=alex)
    assert r.status_code == 204, r.text
    assert key not in _keys(client, gh)
    assert client.get(f"{API}/channels/{key}/messages", headers=gh).status_code == 404


def test_a_guest_role_agent_still_reads_and_answers_its_own_chat(client, alex):
    sec, _ = _project(client, alex)
    r = client.post(
        f"{API}/accounts",
        json={"handle": "agent2", "display_name": "Agent 2", "kind": "agent", "role": "guest"},
        headers=alex,
    )
    agent2 = r.json()
    tok = client.post(
        f"{API}/accounts/{agent2['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    ah = {"Authorization": f"Bearer {tok['token']}"}
    _share(client, alex, agent2["id"], "section", sec["id"])
    key = f"agent:{agent2['id']}"
    msg = _post(client, alex, key, "@agent2 Anything blocking?")
    got = client.post(f"{API}/work/next", json={"channel": key}, headers=ah).json()
    assert got["chat"]["message_id"] == msg["id"]
    _post(client, ah, key, "Nothing blocking.")
    assert key in _keys(client, ah)


def test_notifications_name_an_agents_chat_as_a_chat_with_it(client, alex):
    from slate.service import chat

    agent, ch = _agent(client, alex)
    assert chat.place(f"agent:{agent['id']}", "Agent") == "chat with Agent"
    assert chat.place("list:L1", "Todo") == "#Todo"
    # The person hears the agent's mention of them in the inbox as their chat with it.
    _post(client, ch, f"agent:{agent['id']}", "@alex done: the list is tidy.")
    inbox = client.get(f"{API}/inbox", headers=alex).json()
    entries = inbox["entries"] if isinstance(inbox, dict) else inbox
    assert any(e.get("item_title") == "chat with Agent" for e in entries), entries


def test_the_live_stream_carries_the_project_agents_chat_to_its_guest(client, alex, db):
    from slate.service.access import EventGate

    sec, _ = _project(client, alex)
    agent2, _ = _agent(client, alex, "agent2", role="guest")
    agent, _ = _agent(client, alex, "agent")
    _share(client, alex, agent2["id"], "section", sec["id"])
    guest, gh = _person(client, alex, "guest", "guest")
    _share(client, alex, guest["id"], "section", sec["id"])
    me = client.get(f"{API}/me", headers=gh).json()
    gate = EventGate(guest["id"], me["token_id"])
    gate.load(db)

    def heard(channel):
        payload = {"type": "message.posted", "subject_id": "m1", "after": {"channel": channel}}
        return gate.allows(db, payload)

    assert heard(f"agent:{agent2['id']}")
    assert not heard(f"agent:{agent['id']}")
    assert not heard("general")
