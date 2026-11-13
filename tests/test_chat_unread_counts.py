"""Chat unread counts are counted in SQL: the channel list never loads the unread messages
themselves, and one channel's view counts only that channel."""

from __future__ import annotations

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select

from slate.models import Account, Message
from slate.service import chat

API = "/api/v1"


def post(client, headers, key, body, **extra):
    r = client.post(
        f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def loaded_messages():
    seen: list[str] = []

    def on_load(target, _ctx):
        seen.append(target.id)

    sa_event.listen(Message, "load", on_load)
    yield seen
    sa_event.remove(Message, "load", on_load)


def reference(session, account) -> dict:
    """The counts as defined: built from the unread messages one by one."""
    out: dict = {}
    for m in chat.unread_messages(session, account):
        c = out.setdefault(
            m.channel,
            {
                "unread": 0,
                "mentions": 0,
                "first_unread_id": m.id,
                "first_unread_thread_id": m.parent_id,
                "unread_threads": {},
            },
        )
        c["unread"] += 1
        if m.parent_id:
            c["unread_threads"][m.parent_id] = c["unread_threads"].get(m.parent_id, 0) + 1
        if account.handle in (m.mentions_json or []):
            c["mentions"] += 1
    return out


def test_counts_match_the_definition(client, alex, todo, make_account, db):
    _, agent = make_account("my-agent")
    _, other = make_account("other-agent")
    a, b = f"list:{todo['id']}", "general"
    first = post(client, agent, a, "one")
    root = post(client, agent, a, "@alex a question")
    post(client, other, a, "reply", parent_id=root["id"])
    post(client, other, a, "@alex reply two", parent_id=root["id"])
    gone = post(client, agent, a, "@alex deleted")
    client.delete(f"{API}/messages/{gone['id']}", headers=agent)
    post(client, alex, a, "my own")
    for i in range(4):
        post(client, other, b, f"general {i}")
    # alex reads General through its second message and sees two messages past it exactly.
    gen = client.get(f"{API}/channels/{b}/messages", headers=alex).json()
    ids = sorted(m["id"] for m in gen)
    client.post(f"{API}/channels/{b}/read", json={"through": ids[1]}, headers=alex)
    client.post(f"{API}/channels/{b}/read", json={"message_ids": [ids[3]]}, headers=alex)
    # And sees the first message of the list channel exactly (it was the first unread).
    client.post(f"{API}/channels/{a}/read", json={"message_ids": [first["id"]]}, headers=alex)

    me = db.scalar(select(Account).where(Account.handle == "alex"))
    db.expire_all()
    want = reference(db, me)
    assert chat.unread(db, me) == want
    assert want[a]["unread"] == 3 and want[a]["mentions"] == 2
    assert want[a]["first_unread_id"] == root["id"]
    assert want[a]["unread_threads"] == {root["id"]: 2}
    assert want[b]["unread"] == 1
    rows = {r["key"]: r for r in client.get(f"{API}/channels", headers=alex).json()}
    assert rows[a]["unread"] == 3 and rows[a]["mentions"] == 2
    one = client.get(f"{API}/channels/{a}", headers=alex).json()
    assert one == rows[a]


def test_the_channel_list_never_loads_unread_messages(
    client, alex, todo, make_account, loaded_messages
):
    _, agent = make_account("my-agent")
    key = f"list:{todo['id']}"
    for i in range(60):
        post(client, agent, key, f"@alex status {i}")
    loaded_messages.clear()
    rows = {r["key"]: r for r in client.get(f"{API}/channels", headers=alex).json()}
    one = client.get(f"{API}/channels/{key}", headers=alex).json()
    assert rows[key]["unread"] == 60 and rows[key]["mentions"] == 60
    assert one["unread"] == 60 and one["position"] == rows[key]["position"]
    assert loaded_messages == []


def test_one_batched_exposure_read_is_one_event(client, alex, todo, make_account, db):
    """The board sends what scrolled into view together, once a second: one read call with
    many ids is one `channel.read` event, not one per message."""
    from slate.models import Event

    _, agent = make_account("my-agent")
    key = f"list:{todo['id']}"
    ids = [post(client, agent, key, f"line {i}")["id"] for i in range(150)]
    before = db.scalar(select(func.count()).where(Event.type == "channel.read"))
    r = client.post(f"{API}/channels/{key}/read", json={"message_ids": ids}, headers=alex)
    assert r.status_code == 200 and r.json()["changed"] is True
    after = db.scalar(select(func.count()).where(Event.type == "channel.read"))
    assert after - before == 1
    assert client.get(f"{API}/channels/{key}", headers=alex).json()["unread"] == 0
