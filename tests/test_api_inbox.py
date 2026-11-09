"""The notifications inbox: mentions, assignments and changes on followed items, from events."""

from __future__ import annotations

API = "/api/v1"


def new_item(client, headers, todo, **extra):
    r = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Fix gutter", **extra},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def inbox(client, headers, **params):
    r = client.get(f"{API}/inbox", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def count(client, headers):
    return client.get(f"{API}/inbox/count", headers=headers).json()["unread"]


def test_own_changes_are_not_news(client, alex, todo):
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "note"}, headers=alex)
    assert inbox(client, alex) == []
    assert count(client, alex) == 0


def test_mention_assignment_and_followed_changes(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)
    client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": "On it @alex"}, headers=agent
    )
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["alex"]}, headers=agent)
    client.patch(f"{API}/items/{item['id']}", json={"due_at": "2026-11-01"}, headers=agent)
    client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=agent)

    entries = inbox(client, alex)
    reasons = [e["reason"] for e in entries]
    assert reasons[0] == "status"
    assert set(reasons) >= {"mention", "assigned", "update", "status"}
    assert all(e["item_title"] == "Fix gutter" and not e["read"] for e in entries)
    assert all(e["event"]["actor_id"] != entries[0]["event"]["subject_id"] for e in entries)
    assert count(client, alex) == len(entries)
    assert [e["reason"] for e in inbox(client, alex, reason="mention")] == ["mention"]


def test_your_own_items_are_followed_until_you_unwatch(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)  # filed by alex: auto-watched (service/watchers.py)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=agent)
    assert [e["reason"] for e in inbox(client, alex)] == ["comment"]
    client.post(f"{API}/items/{item['id']}/unwatch", headers=alex)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "again"}, headers=agent)
    # Following is judged now, so unwatching also drops the item's recent history.
    assert inbox(client, alex) == []


def test_strangers_hear_only_about_mentions_and_assignments(client, alex, todo, make_account):
    _, agent = make_account("agent")
    _, agent2 = make_account("agent2")
    item = new_item(client, alex, todo)
    client.patch(f"{API}/items/{item['id']}", json={"title": "Fix the gutter"}, headers=alex)
    assert inbox(client, agent) == []
    client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": "@agent look"}, headers=agent2
    )
    assert [e["reason"] for e in inbox(client, agent)] == ["mention"]
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["agent"]}, headers=alex)
    # Assigned, agent now follows the item, so its recent history shows up too.
    assert [e["reason"] for e in inbox(client, agent)] == ["assigned", "mention", "update"]
    client.patch(f"{API}/items/{item['id']}", json={"priority": "high"}, headers=alex)
    assert inbox(client, agent)[0]["reason"] == "update"


def test_read_one_item_and_all(client, alex, todo, make_account):
    _, agent = make_account("agent")
    a = new_item(client, alex, todo)
    b = new_item(client, alex, todo, title="Pay water")
    for it in (a, b):
        client.post(f"{API}/items/{it['id']}/watch", headers=alex)
        client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "hi"}, headers=agent)
    assert count(client, alex) == 2

    first = inbox(client, alex)[0]
    r = client.post(f"{API}/inbox/read", json={"event_ids": [first["event"]["id"]]}, headers=alex)
    assert r.status_code == 200, r.text
    assert count(client, alex) == 1
    assert inbox(client, alex, unread=True)[0]["item_title"] == "Fix gutter"

    client.post(f"{API}/inbox/read", json={"item_id": a["id"]}, headers=alex)
    assert count(client, alex) == 0
    assert len(inbox(client, alex)) == 2  # read, not cleared

    client.post(f"{API}/items/{a['id']}/comments", json={"body_md": "again"}, headers=agent)
    assert count(client, alex) == 1
    client.post(f"{API}/inbox/read", json={"all": True}, headers=alex)
    assert count(client, alex) == 0


def test_reading_an_item_marks_all_its_unread_entries(client, alex, todo, make_account):
    # Opening a task marks every entry for it read in one call. Two or more fresh marks must
    # not trip the write guard (a second lookup autoflushing the first mark before its event).
    _, agent = make_account("agent")
    a = new_item(client, alex, todo)
    client.post(f"{API}/items/{a['id']}/watch", headers=alex)
    for body in ("one", "two", "three"):
        client.post(f"{API}/items/{a['id']}/comments", json={"body_md": body}, headers=agent)
    assert count(client, alex) == 3

    r = client.post(f"{API}/inbox/read", json={"item_id": a["id"]}, headers=alex)
    assert r.status_code == 200, r.text
    assert len(r.json()["event_ids"]) == 3
    assert count(client, alex) == 0

    r = client.post(f"{API}/inbox/clear", json={"item_id": a["id"]}, headers=alex)
    assert r.status_code == 200, r.text
    assert inbox(client, alex) == []


def test_clear_removes_entries(client, alex, todo, make_account):
    _, agent = make_account("agent")
    a = new_item(client, alex, todo)
    client.post(f"{API}/items/{a['id']}/watch", headers=alex)
    client.post(f"{API}/items/{a['id']}/comments", json={"body_md": "hi"}, headers=agent)
    client.post(f"{API}/inbox/clear", json={"item_id": a["id"]}, headers=alex)
    assert inbox(client, alex) == []
    client.post(f"{API}/items/{a['id']}/comments", json={"body_md": "more"}, headers=agent)
    assert len(inbox(client, alex)) == 1
    client.post(f"{API}/inbox/clear", json={"all": True}, headers=alex)
    assert inbox(client, alex) == []


def test_marking_is_an_event_on_the_account(client, alex, todo, make_account):
    _, agent = make_account("agent")
    a = new_item(client, alex, todo)
    client.post(f"{API}/items/{a['id']}/comments", json={"body_md": "hi"}, headers=agent)
    client.post(f"{API}/inbox/read", json={"all": True}, headers=alex)
    me = client.get(f"{API}/me", headers=alex).json()
    evs = client.get(f"{API}/events", params={"type": "inbox.read"}, headers=alex).json()
    rows = evs["events"] if isinstance(evs, dict) else evs
    assert rows and rows[-1]["subject_id"] == me["account"]["id"]


def test_bad_marks_are_refused(client, alex):
    assert client.post(f"{API}/inbox/read", json={}, headers=alex).status_code == 422
    r = client.post(f"{API}/inbox/read", json={"event_ids": ["nope"]}, headers=alex)
    assert r.status_code == 422
