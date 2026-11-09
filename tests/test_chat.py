"""Chat channels: General, one per section and list; threads, reactions, mentions, task links."""

from __future__ import annotations

import pytest

from slate.models import Event, Source
from slate.service import chat, push
from slate.service.core import Actor
from slate.service.errors import Invalid

API = "/api/v1"


def post(client, headers, key, body, **extra):
    r = client.post(
        f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def channels(client, headers):
    r = client.get(f"{API}/channels", headers=headers)
    assert r.status_code == 200, r.text
    return {c["key"]: c for c in r.json()}


def test_channels_follow_the_board(client, alex, todo):
    found = channels(client, alex)
    key = f"list:{todo['id']}"
    assert list(found)[0] == "general"
    assert found[key]["name"] == "Todo" and found[key]["kind"] == "list"
    assert found[f"section:{todo['section_id']}"]["kind"] == "section"
    assert found[key]["unread"] == 0 and found[key]["last_message_id"] is None
    assert client.get(f"{API}/channels/list:nope", headers=alex).status_code == 404
    assert client.get(f"{API}/channels/bogus", headers=alex).status_code == 422


def test_post_read_and_unread_counts(client, alex, todo, make_account):
    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    first = post(client, agent, key, "Morning @alex, anything for me?")
    post(client, agent, key, "Also the gutters")
    assert first["mentions"] == ["alex"] and first["author_id"] != ""

    mine = channels(client, alex)[key]
    assert (mine["unread"], mine["mentions"]) == (2, 1)
    # Your own messages are never unread.
    assert channels(client, agent)[key]["unread"] == 0

    page = client.get(f"{API}/channels/{key}/messages", headers=alex).json()
    assert [m["body_md"] for m in page] == ["Morning @alex, anything for me?", "Also the gutters"]
    r = client.post(f"{API}/channels/{key}/read", json={}, headers=alex)
    assert r.json()["changed"] is True
    assert channels(client, alex)[key]["unread"] == 0
    # Reading again changes nothing.
    assert client.post(f"{API}/channels/{key}/read", json={}, headers=alex).json()["changed"] is (
        False
    )


def test_threads_reply_and_notify_followers(client, alex, todo, make_account):
    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    root = post(client, alex, key, "Who can take the gutter job?")
    reply = post(client, agent, key, "I can", parent_id=root["id"])
    assert reply["parent_id"] == root["id"]
    # A reply to a reply lands in the same thread.
    again = post(client, alex, key, "Great", parent_id=reply["id"])
    assert again["parent_id"] == root["id"]

    top = client.get(f"{API}/channels/{key}/messages", headers=alex).json()
    assert [m["id"] for m in top] == [root["id"]]
    assert top[0]["reply_count"] == 2 and top[0]["last_reply_at"]
    thread = client.get(f"{API}/messages/{reply['id']}/thread", headers=alex).json()
    assert [m["id"] for m in thread] == [root["id"], reply["id"], again["id"]]
    following = client.get(
        f"{API}/channels/{key}/messages", params={"after": root["id"]}, headers=agent
    ).json()
    assert [m["id"] for m in following] == [reply["id"], again["id"]]

    # Alex hears the agent's reply; the agent hears Alex's.
    inbox = client.get(f"{API}/inbox", headers=alex).json()
    assert [(e["reason"], e["channel"], e["item_title"]) for e in inbox] == [
        ("reply", key, "#Todo")
    ]
    news = client.get(f"{API}/checkin", headers={**agent}).json()["news"]
    assert [n["why"] for n in news] == ["reply"]


def test_mentions_reach_inbox_checkin_and_push(client, alex, todo, make_account, db):
    agent_acct, agent = make_account("agent")
    post(client, alex, "general", "@agent please sort the receipts")
    news = client.get(f"{API}/checkin", headers=agent).json()["news"]
    assert [(n["type"], n["why"]) for n in news] == [("message.posted", "mentioned")]
    assert news[0]["after"]["channel"] == "general"
    entries = client.get(f"{API}/inbox", headers=agent).json()
    assert entries[0]["reason"] == "mention" and entries[0]["channel"] == "general"

    ev = db.query(Event).filter(Event.type == "message.posted").one()
    note = push.notification_for(db, ev, "mention")
    assert note["title"] == "Alex mentioned you"
    assert note["body"].startswith("#General: @agent please")
    assert note["url"].startswith("/#/chat/general?thread=")


def test_push_text_names_linked_tasks(client, alex, todo, make_account, db):
    make_account("agent")
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Fix the gutter"}, headers=alex
    ).json()
    post(client, alex, "general", f"@agent take #{item['id']} please, not #abcdefABCDEF")
    ev = db.query(Event).filter(Event.type == "message.posted").one()
    note = push.notification_for(db, ev, "mention")
    assert note["body"] == "#General: @agent take #Fix the gutter please, not #abcdefABCDEF"


def test_edit_delete_and_only_the_author(client, alex, todo, make_account):
    _, agent = make_account("agent")
    msg = post(client, alex, "general", "draft")
    assert (
        client.patch(f"{API}/messages/{msg['id']}", json={"body_md": "x"}, headers=agent)
    ).status_code == 403
    r = client.patch(f"{API}/messages/{msg['id']}", json={"body_md": "final @agent"}, headers=alex)
    assert r.json()["edited_at"] and r.json()["mentions"] == ["agent"]
    # The edit's new mention is news to the agent.
    news = client.get(f"{API}/checkin", headers=agent).json()["news"]
    assert ("message.edited", "mentioned") in [(n["type"], n["why"]) for n in news]

    assert client.delete(f"{API}/messages/{msg['id']}", headers=agent).status_code == 403
    gone = client.delete(f"{API}/messages/{msg['id']}", headers=alex).json()
    assert gone["deleted_at"] and gone["body_md"] == ""


def test_reactions(client, alex, todo):
    msg = post(client, alex, "general", "shipped")
    r = client.post(f"{API}/messages/{msg['id']}/reactions", json={"emoji": "🎉"}, headers=alex)
    client.post(f"{API}/messages/{msg['id']}/reactions", json={"emoji": "🎉"}, headers=alex)
    assert r.json()["reactions"][0]["emoji"] == "🎉"
    assert len(r.json()["reactions"][0]["account_ids"]) == 1
    r = client.delete(f"{API}/messages/{msg['id']}/reactions/🎉", headers=alex)
    assert r.json()["reactions"] == []


def test_turn_a_message_into_a_task(client, alex, todo):
    key = f"list:{todo['id']}"
    msg = post(client, alex, key, "**Call the plumber** about the leak\nbefore Friday")
    r = client.post(f"{API}/messages/{msg['id']}/task", json={"due_at": "2026-11-01"}, headers=alex)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["title"] == "Call the plumber about the leak"
    assert item["list_id"] == todo["id"]
    assert (
        "> **Call the plumber**"
        in client.get(f"{API}/items/{item['id']}", headers=alex).json()["body_md"]
    )
    again = client.get(f"{API}/messages/{msg['id']}", headers=alex).json()
    assert again["item_id"] == item["id"]
    assert again["linked_items"][0]["title"] == "Call the plumber about the leak"
    # Once is enough; General has no list of its own.
    assert client.post(f"{API}/messages/{msg['id']}/task", json={}, headers=alex).status_code == 422
    loose = post(client, alex, "general", "something")
    r = client.post(f"{API}/messages/{loose['id']}/task", json={}, headers=alex)
    assert r.status_code == 422 and "list_id" in r.json()["detail"]
    r = client.post(
        f"{API}/messages/{loose['id']}/task", json={"list_id": todo["id"]}, headers=alex
    )
    assert r.status_code == 201


def test_task_links_render_as_linked_items(client, alex, todo):
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Fix gutter"}, headers=alex
    ).json()
    msg = post(
        client,
        alex,
        "general",
        f"see https://slate.example/#/list/{todo['id']}?task={item['id']} and #{item['id']}",
    )
    assert [i["id"] for i in msg["linked_items"]] == [item["id"]]
    assert msg["linked_items"][0]["status"] == "To do"
    # A tag-like word that is no item's id links nothing.
    assert chat.item_refs("#hashtagword1 and #short") == ["hashtagword1"]
    assert post(client, alex, "general", "#hashtagword1")["linked_items"] == []


def test_readable_names_tasks_but_never_pages_or_lists(client, alex, todo, db):
    """Notification text: `#<id>` becomes the task's title; `[[doc:<id>]]` / `[[list:<id>]]`
    (the board's page and list links) read as "a page" / "a list", never the title, because
    the text goes to people who may not be allowed to see that page."""
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Fix the oven"}, headers=alex
    ).json()
    doc = client.post(
        f"{API}/docs", json={"title": "Oven manual", "list_id": todo["id"]}, headers=alex
    ).json()
    text = chat.readable(db, f"see #{item['id']} in [[doc:{doc['id']}]] and [[list:{todo['id']}]]")
    assert text == "see #Fix the oven in a page and a list"
    assert "Oven manual" not in text and "Todo" not in text


def test_archived_list_channel_refuses_posts(client, alex, todo):
    client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=alex)
    r = client.post(
        f"{API}/channels/list:{todo['id']}/messages", json={"body_md": "hi"}, headers=alex
    )
    assert r.status_code == 422
    assert f"list:{todo['id']}" not in channels(client, alex)


def test_read_all(client, alex, todo, make_account):
    _, agent = make_account("agent")
    post(client, agent, "general", "one")
    post(client, agent, f"list:{todo['id']}", "two")
    r = client.post(f"{API}/channels/read-all", headers=alex)
    assert sorted(r.json()["channels"]) == sorted(["general", f"list:{todo['id']}"])
    assert all(c["unread"] == 0 for c in channels(client, alex).values())


def test_every_message_write_is_an_event(db, client, alex, todo):
    from slate.models import Account

    acct = db.query(Account).filter_by(handle="alex").one()
    actor = Actor(account_id=acct.id, source=Source.api)
    msg = chat.post(db, actor, "general", body_md="hi")
    types = [e.type for e in db.query(Event).filter(Event.subject_id == msg.id)]
    assert types == ["message.posted"]
    with pytest.raises(Invalid):
        chat.post(db, actor, "general", body_md="   ")
