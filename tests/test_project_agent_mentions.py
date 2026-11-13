"""Project (guest-role) agent chats are shared conversation, not implicit requests."""

import pytest

from conftest import API
from test_agent_chat import _agent, _events, _person, _post, _project, _share
from test_chat_wake import _finish, _next, _turns


def project_chat(client, alex, handle="agent2"):
    section, _ = _project(client, alex)
    agent, _ = _agent(client, alex, handle, role="guest")
    guest, guest_h = _person(client, alex, "guest", "guest")
    for account in (agent, guest):
        _share(client, alex, account["id"], "section", section["id"])
    token = client.post(
        f"{API}/accounts/{agent['id']}/tokens",
        json={"scopes": ["write"], "limit": {"section_ids": [section["id"]], "level": "edit"}},
        headers=alex,
    )
    assert token.status_code == 201, token.text
    return agent, {"Authorization": f"Bearer {token.json()['token']}"}, guest_h


@pytest.mark.parametrize("handle", ["agent2", "project-agent"])
@pytest.mark.parametrize("author", ["owner", "guest"])
@pytest.mark.parametrize("threaded", [False, True])
def test_shared_agent_chat_requires_explicit_mentions(client, alex, handle, author, threaded):
    agent, ah, guest_h = project_chat(client, alex, handle)
    writer = alex if author == "owner" else guest_h
    key = f"agent:{agent['id']}"
    root = _post(client, ah, key, "Project discussion")
    for viewer in (alex, guest_h, ah):
        row = client.get(f"{API}/channels/{key}", headers=viewer).json()
        listed = next(
            c for c in client.get(f"{API}/channels", headers=viewer).json() if c["key"] == key
        )
        assert row["mention_required"] is True
        assert listed["mention_required"] is True
    thread_id = root["id"] if threaded else None
    extra = {"parent_id": thread_id} if threaded else {}

    for body in ("Which draft is current?", "@alex can you answer?", "Thanks, following up"):
        msg = _post(client, writer, key, body, **extra)
        assert handle not in msg["mentions"]
    assert _events(client, alex, "message.mentioned_agent") == []
    assert _turns(client, alex, key) == []
    assert _next(client, ah, channel=key, thread_id=thread_id)["chat"] is None

    ask = _post(client, writer, key, f"@{handle} please check", **extra)
    assert ask["mentions"] == [handle]
    assert _turns(client, alex, key) == [(agent["id"], thread_id, "waiting")]
    assert _next(client, ah, channel=key, thread_id=thread_id)["chat"]["message_id"] == ask["id"]
    assert _turns(client, alex, key) == [(agent["id"], thread_id, "working")]
    # Human conversation during a run does not queue another turn.
    _post(client, writer, key, "@alex I'll send you the draft", **extra)
    done = _finish(client, ah, channel=key, thread_id=thread_id, outcome="done", reply="Checked.")
    assert done["pending"] is False
    # Nor does a plain follow-up after the answer (including a thread reply).
    _post(client, writer, key, "What about the other draft?", **extra)
    assert _turns(client, alex, key) == []
    assert _next(client, ah, channel=key, thread_id=thread_id)["chat"] is None
    assert len(_events(client, alex, "message.mentioned_agent")) == 1


def test_edit_only_wakes_project_agent_when_its_mention_is_added(client, alex):
    agent, ah, writer = project_chat(client, alex)
    key = f"agent:{agent['id']}"
    msg = _post(client, writer, key, "A question")

    def edit(body):
        r = client.patch(f"{API}/messages/{msg['id']}", json={"body_md": body}, headers=writer)
        assert r.status_code == 200, r.text
        return r.json()

    assert edit("@alex a question")["mentions"] == ["alex"]
    assert _events(client, alex, "message.mentioned_agent") == []
    assert edit("@agent2 a question")["mentions"] == ["agent2"]
    assert _next(client, ah, channel=key)["chat"]["message_id"] == msg["id"]
    edit("@agent2 a clarified question")
    assert len(_events(client, alex, "message.mentioned_agent")) == 1
    assert not _finish(client, ah, channel=key, outcome="done", reply="Answered.")["pending"]
    edit("Thanks, @alex")
    assert _next(client, ah, channel=key)["chat"] is None
    edit("@agent2 another question")
    assert _next(client, ah, channel=key)["chat"] is not None
    assert len(_events(client, alex, "message.mentioned_agent")) == 2


def test_other_agent_mention_wakes_only_that_agent(client, alex):
    agent, ah, _ = project_chat(client, alex)
    other, oh = _agent(client, alex, "reviewer")
    # A project agent's chat is its project's: the reviewer reads it once it is on the project.
    [place] = client.get(f"{API}/shares", params={"account_id": agent["id"]}, headers=alex).json()
    _share(client, alex, other["id"], "section", place["scope_id"])
    key = f"agent:{agent['id']}"
    msg = _post(client, alex, key, "@reviewer please check")
    assert msg["mentions"] == ["reviewer"]
    assert _next(client, ah, channel=key)["chat"] is None
    assert _next(client, oh, channel=key)["chat"]["message_id"] == msg["id"]
    assert _events(client, alex, "message.mentioned_agent")[-1]["after"]["for"] == [other["id"]]


@pytest.mark.parametrize("role", ["member", "admin"])
def test_workspace_agent_direct_chat_keeps_implicit_addressing_on_followup(client, alex, role):
    agent, ah = _agent(client, alex, role=role)
    key = f"agent:{agent['id']}"
    assert client.get(f"{API}/channels/{key}", headers=alex).json()["mention_required"] is False
    for body in ("Hello", "@alex FYI", "And one more question"):
        msg = _post(client, alex, key, body)
        assert "agent" in msg["mentions"]
        assert _next(client, ah, channel=key)["chat"]["message_id"] == msg["id"]
        _finish(client, ah, channel=key, outcome="done", reply="Answered.")
