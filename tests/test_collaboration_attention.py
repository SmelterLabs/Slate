"""Collaboration attention regressions: requests are durable, deliveries are scoped."""

from datetime import timedelta

import pytest

from conftest import API
from slate.models import utcnow
from slate.service import push
from test_assigned_comments import _comment, _item
from test_push import Browser


def test_ask_only_reaches_human_inbox_and_push_without_following(
    client, alex, todo, db, make_account
):
    human, headers = make_account("member", kind="person")
    browser = Browser()
    assert (
        client.post(API + "/push/subscriptions", json=browser.body(), headers=headers).status_code
        == 201
    )
    received = []

    def send(endpoint, key, auth, note):
        received.append(note)
        return 201, ""

    push.process(db, send)
    task = _item(client, alex, todo["id"])
    ask = _comment(client, alex, task["id"], "Can you review tomorrow?", assignee="member")
    entries = client.get(API + "/inbox", headers=headers).json()
    assert len(entries) == 1
    assert entries[0]["event"]["type"] == "comment.assigned"
    assert push.process(db, send) == 1
    assert "asked you" in received[0]["title"]
    assert received[0]["url"] == f"/#/inbox?task={task['id']}"
    assert client.post(API + "/inbox/clear", json={"all": True}, headers=headers).status_code == 200
    view = client.get(f"{API}/items/{task['id']}", headers=headers).json()
    assert view["open_asks"] == [human["id"]]
    assert client.post(f"{API}/comments/{ask['id']}/resolve", headers=headers).status_code == 200
    assert client.get(f"{API}/items/{task['id']}", headers=headers).json()["open_asks"] == []


def test_reply_returns_to_requester_and_participants_not_bystanders(
    client, alex, todo, db, make_account
):
    member, jh = make_account("member", kind="person")
    _, ph = make_account("member2", kind="person")
    _, bh = make_account("bystander", kind="person")
    received = []
    for headers, suffix in ((alex, "requester"), (ph, "participant"), (bh, "bystander")):
        response = client.post(
            API + "/push/subscriptions",
            headers=headers,
            json=Browser(f"https://push.example.com/{suffix}").body(),
        )
        assert response.status_code == 201

    def send(endpoint, key, auth, note):
        received.append((endpoint, note))
        return 201, ""

    push.process(db, send)
    task = _item(client, alex, todo["id"])
    ask = _comment(client, alex, task["id"], "Please review", assignee="member")
    _comment(client, ph, task["id"], "I have context too")
    reply = _comment(client, jh, task["id"], "I need more time")
    view = client.get(f"{API}/items/{task['id']}", headers=jh).json()
    assert view["open_asks"] == [member["id"]]
    for headers in (alex, ph):
        entries = client.get(API + "/inbox", headers=headers).json()
        entry = next(e for e in entries if e["event"]["subject_id"] == reply["id"])
        assert entry["reason"] == "reply"
        assert "prefs" not in str(entry)
        news = client.get(API + "/checkin", headers=headers).json()["news"]
        assert next(e for e in news if e["subject_id"] == reply["id"])["why"] == "reply"
    assert client.get(API + "/inbox", headers=bh).json() == []
    push.process(db, send)
    assert sorted(
        endpoint for endpoint, note in received if note["event_id"] == entry["event"]["id"]
    ) == ["https://push.example.com/participant", "https://push.example.com/requester"]
    assert client.post(f"{API}/comments/{ask['id']}/resolve", headers=alex).status_code == 200


def guest(client, alex, todo):
    r = client.post(
        API + "/accounts",
        headers=alex,
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
    )
    assert r.status_code == 201, r.text
    account = r.json()
    token = client.post(
        f"{API}/accounts/{account['id']}/tokens", headers=alex, json={"scopes": ["write"]}
    ).json()
    headers = {"Authorization": f"Bearer {token['token']}"}
    share = client.put(
        API + "/shares",
        headers=alex,
        json={
            "account_id": account["id"],
            "scope_kind": "list",
            "scope_id": todo["id"],
            "level": "edit",
        },
    )
    assert share.status_code == 200, share.text
    return account, headers, share.json()


def test_push_renders_task_references_for_each_recipient(client, alex, todo, db):
    _, gh, _ = guest(client, alex, todo)
    private = client.post(
        API + "/lists", headers=alex, json={"section_id": todo["section_id"], "name": "Private"}
    ).json()
    hidden = _item(client, alex, private["id"], title="Private payroll secret")
    visible = _item(client, alex, todo["id"], title="Public project task")
    assert client.get(f"{API}/items/{hidden['id']}", headers=gh).status_code == 404
    for headers, endpoint in ((gh, "guest"), (alex, "owner")):
        browser = Browser(f"https://push.example.com/{endpoint}")
        assert (
            client.post(
                API + "/push/subscriptions", headers=headers, json=browser.body()
            ).status_code
            == 201
        )
    received = {}

    def send(endpoint, key, auth, note):
        received[endpoint] = note
        return 201, ""

    push.process(db, send)
    # The guest's own post is not sent to them, so use a separate permitted author.
    client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        headers=alex,
        json={"body_md": f"Look #{hidden['id']} and #{visible['id']}"},
    )
    assert push.process(db, send) == 1
    text = str(received["https://push.example.com/guest"])
    assert "Private payroll secret" not in text
    assert "Public project task" in text
    # The owner still sees the title when the guest posts the same opaque reference.
    client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        headers=gh,
        json={"body_md": f"Look #{hidden['id']}"},
    )
    assert push.process(db, send) == 1
    assert "Private payroll secret" in str(received["https://push.example.com/owner"])


@pytest.mark.parametrize("revoke", ["disable", "share"])
def test_revoked_recipients_get_no_push_or_renudge(client, alex, todo, db, revoke):
    account, gh, share = guest(client, alex, todo)
    assert (
        client.post(API + "/push/subscriptions", headers=gh, json=Browser().body()).status_code
        == 201
    )
    received = []

    def send(endpoint, key, auth, note):
        received.append(note)
        return 201, ""

    push.process(db, send)
    client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        headers=alex,
        json={"body_md": "Private project update"},
    )
    if revoke == "disable":
        r = client.patch(f"{API}/accounts/{account['id']}", headers=alex, json={"disabled": True})
    else:
        r = client.delete(f"{API}/shares/{share['id']}", headers=alex)
    assert r.status_code in (200, 204), r.text
    assert push.process(db, send) == 0
    assert push.renudge(db, send, {}, now=utcnow() + timedelta(minutes=11)) == 0
    assert received == []
