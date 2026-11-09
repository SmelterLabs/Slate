"""Watchers (ClickUp-style): anyone adds or removes anyone, auto-watch on create / assign /
comment for people (account prefs), and a watcher hears about every change to the task."""

from __future__ import annotations

from slate import webpush
from slate.service import push
from test_push import browser  # noqa: F401  (test_push's fixture)

API = "/api/v1"


def new_item(client, headers, todo, **extra):
    body = {"list_id": todo["id"], "title": "Fix gutter", **extra}
    r = client.post(f"{API}/items", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def watchers(client, headers, item_id):
    r = client.get(f"{API}/items/{item_id}/watchers", headers=headers)
    assert r.status_code == 200, r.text
    return sorted(w["handle"] for w in r.json())


def inbox(client, headers):
    r = client.get(f"{API}/inbox", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_people_auto_watch_what_they_create_are_assigned_or_comment_on(
    client, alex, todo, make_account
):
    _, member = make_account("member", kind="person")
    _, member2 = make_account("member2", kind="person")
    item = new_item(client, alex, todo)
    assert watchers(client, alex, item["id"]) == ["alex"]
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["member"]}, headers=alex)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=member2)
    assert watchers(client, alex, item["id"]) == ["alex", "member", "member2"]
    # Unassigning does not unwatch (ClickUp): member stays until removed.
    client.patch(f"{API}/items/{item['id']}", json={"assignees": []}, headers=alex)
    assert "member" in watchers(client, alex, item["id"])


def test_agents_do_not_auto_watch_by_default(client, alex, todo, make_account):
    _, agent = make_account("agent")  # an agent
    item = new_item(client, agent, todo)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "done"}, headers=agent)
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["agent"]}, headers=alex)
    assert watchers(client, alex, item["id"]) == []


def test_auto_watch_follows_account_prefs(client, alex, todo, make_account):
    r = client.patch(f"{API}/me/prefs", json={"auto_watch": {"created": False}}, headers=alex)
    assert r.status_code == 200, r.text
    item = new_item(client, alex, todo)
    assert watchers(client, alex, item["id"]) == []
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "note"}, headers=alex)
    assert watchers(client, alex, item["id"]) == ["alex"]  # commented is still on
    _, agent = make_account("agent")
    client.patch(f"{API}/me/prefs", json={"auto_watch": {"created": True}}, headers=agent)
    other = new_item(client, agent, todo)
    assert watchers(client, alex, other["id"]) == ["agent"]  # an agent can opt in
    bad = client.patch(f"{API}/me/prefs", json={"auto_watch": {"viewed": True}}, headers=alex)
    assert bad.status_code == 422


def test_creating_and_assigning_yourself_watches_once(client, alex, todo):
    item = new_item(client, alex, todo, assignees=["alex"])
    assert watchers(client, alex, item["id"]) == ["alex"]


def test_anyone_adds_or_removes_anyone(client, alex, todo, make_account):
    _, agent = make_account("agent")
    make_account("member3", kind="person")
    item = new_item(client, alex, todo)
    r = client.post(f"{API}/items/{item['id']}/watch", json={"account": "member3"}, headers=agent)
    assert r.status_code == 200, r.text
    assert sorted(w["handle"] for w in r.json()) == ["alex", "member3"]
    r = client.post(f"{API}/items/{item['id']}/unwatch", json={"account": "alex"}, headers=agent)
    assert [w["handle"] for w in r.json()] == ["member3"]
    client.post(f"{API}/items/{item['id']}/watch", headers=agent)  # no body: yourself
    assert watchers(client, alex, item["id"]) == ["agent", "member3"]


def test_someone_added_by_hand_is_told(client, alex, todo, make_account):
    acct, member = make_account("member", kind="person")
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/watch", json={"account": "member"}, headers=alex)
    entries = inbox(client, member)
    assert [e["reason"] for e in entries] == ["watching"]
    assert entries[0]["event"]["type"] == "item.watched"
    # Being assigned auto-watches too, but "assigned" already says so: no second entry.
    other = new_item(client, alex, todo, title="Other")
    client.patch(f"{API}/items/{other['id']}", json={"assignees": ["member"]}, headers=alex)
    assert [e["reason"] for e in inbox(client, member) if e["event"]["item_id"] == other["id"]] == [
        "assigned"
    ]


def test_watchers_hear_about_any_activity(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)  # alex auto-watches
    iid = item["id"]
    client.patch(f"{API}/items/{iid}", json={"tags": ["roof"]}, headers=agent)
    client.patch(f"{API}/items/{iid}", json={"assignees": ["agent"]}, headers=agent)
    cl = client.post(f"{API}/items/{iid}/checklists", json={"name": "Steps"}, headers=agent)
    assert cl.status_code == 201, cl.text
    client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Buy ladder", "parent_id": iid},
        headers=agent,
    )
    for text in ("one", "one two", "one two three"):  # description autosaves
        client.patch(f"{API}/items/{iid}", json={"body_md": text}, headers=agent)
    client.patch(f"{API}/items/{iid}", json={"position": 9}, headers=agent)  # bookkeeping
    entries = inbox(client, alex)
    assert {e["reason"] for e in entries} == {"watching"}
    kinds = [(e["event"]["type"], e["event"]["field"]) for e in entries]
    assert ("item.updated", "tags") in kinds
    assert ("item.assigned", "assignees") in kinds
    assert ("checklist.created", None) in kinds
    assert ("item.created", None) in kinds  # the subtask
    assert kinds.count(("item.updated", "body_md")) == 1  # one entry for the run of saves
    assert ("item.updated", "position") not in kinds


def test_unwatching_stops_the_news(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/unwatch", headers=alex)
    client.patch(f"{API}/items/{item['id']}", json={"tags": ["x"]}, headers=agent)
    assert inbox(client, alex) == []


def test_watch_activity_is_pushed_once_per_description_edit_run(
    client,
    alex,
    todo,
    db,
    browser,  # noqa: F811
    make_account,
):
    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    push.process(db, webpush.send)
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)
    for text in ("a", "ab", "abc"):
        client.patch(f"{API}/items/{item['id']}", json={"body_md": text}, headers=agent)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "done"}, headers=agent)
    push.process(db, webpush.send)
    got = [(n["reason"], n["title"]) for n in browser.received]
    assert got == [("watching", "Agent changed the description"), ("comment", "Agent commented")]


def test_watching_can_be_left_out_of_push(client, alex, todo, db, browser, make_account):  # noqa: F811
    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    client.patch(f"{API}/me/prefs", json={"push_reasons": ["mention"]}, headers=alex)
    push.process(db, webpush.send)
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "done"}, headers=agent)
    push.process(db, webpush.send)
    assert browser.received == []


def test_an_agent_watching_hears_through_checkin(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/watch", json={"account": "agent"}, headers=alex)
    start = client.get(f"{API}/checkin", headers=agent).json()["next_since"]
    client.post(f"{API}/items/{item['id']}/checklists", json={"name": "Steps"}, headers=alex)
    news = client.get(f"{API}/checkin", params={"since": start}, headers=agent).json()["news"]
    assert [n["why"] for n in news] == ["watching"]
