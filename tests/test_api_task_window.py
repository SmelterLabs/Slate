"""The task window's API: comments with mentions and reactions, checklists, attachments,
watchers and the merged activity feed (docs/task-window.md)."""

from __future__ import annotations

import base64

from conftest import API


def _item(client, headers, list_id, **fields):
    r = client.post(
        f"{API}/items", json={"list_id": list_id, "title": "Thing", **fields}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_comment_mentions_and_reactions(client, alex, todo, make_account):
    agent, agent_h = make_account("agent")
    item = _item(client, alex, todo["id"])
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "@agent please check, cc @nobody and email a@b.c"},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    comment = r.json()
    assert comment["mentions"] == ["agent"] and comment["reactions"] == []

    r = client.post(f"{API}/comments/{comment['id']}/reactions", json={"emoji": "👍"}, headers=alex)
    assert r.json()["reactions"] == [{"emoji": "👍", "account_ids": [r.json()["author_id"]]}]
    client.post(f"{API}/comments/{comment['id']}/reactions", json={"emoji": "👍"}, headers=agent_h)
    again = client.post(
        f"{API}/comments/{comment['id']}/reactions", json={"emoji": "👍"}, headers=agent_h
    ).json()
    assert len(again["reactions"][0]["account_ids"]) == 2  # idempotent per account

    gone = client.delete(f"{API}/comments/{comment['id']}/reactions/👍", headers=agent_h).json()
    assert gone["reactions"][0]["account_ids"] == [comment["author_id"]]
    bad = client.post(
        f"{API}/comments/{comment['id']}/reactions", json={"emoji": "a b"}, headers=alex
    )
    assert bad.status_code == 422

    listed = client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    assert [c["id"] for c in listed] == [comment["id"]]
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["comments"][0]["reactions"][0]["emoji"] == "👍"

    types = [e["type"] for e in client.get(f"{API}/items/{item['id']}/events", headers=alex).json()]
    assert types.count("comment.reacted") == 2 and types.count("comment.unreacted") == 1


def test_checklists_tick_and_progress(client, alex, todo):
    item = _item(client, alex, todo["id"])
    r = client.post(
        f"{API}/items/{item['id']}/checklists",
        json={"name": "Steps", "items": ["call", "pay"]},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    cl = r.json()
    assert [e["title"] for e in cl["items"]] == ["call", "pay"]

    cl = client.post(
        f"{API}/checklists/{cl['id']}/items",
        json={"title": "file receipt", "assignee": "alex"},
        headers=alex,
    ).json()
    assert cl["items"][-1]["assignee_id"] is not None
    first = cl["items"][0]["id"]
    cl = client.patch(f"{API}/checklist-items/{first}", json={"done": True}, headers=alex).json()
    assert cl["items"][0]["done"] is True and cl["items"][0]["done_at"]

    row = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert (row["checklist_done"], row["checklist_total"]) == (1, 3)
    assert row["checklists"][0]["name"] == "Steps"

    cl = client.patch(f"{API}/checklists/{cl['id']}", json={"name": "Tasks"}, headers=alex).json()
    assert cl["name"] == "Tasks"
    assert client.delete(f"{API}/checklist-items/{first}", headers=alex).status_code == 204
    assert client.delete(f"{API}/checklists/{cl['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/items/{item['id']}/checklists", headers=alex).json() == []

    events = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    types = [e["type"] for e in events]
    assert "checklist.entry_checked" in types and types[-1] == "checklist.deleted"
    assert events[-1]["before"] == {"name": "Tasks", "items": ["pay", "file receipt"]}
    bad = client.patch(f"{API}/checklist-items/nope", json={"done": True}, headers=alex)
    assert bad.status_code == 404


def test_attachments_upload_download_remove(client, alex, todo):
    item = _item(client, alex, todo["id"])
    r = client.post(
        f"{API}/items/{item['id']}/attachments?filename=../notes.txt",
        content=b"hello world",
        headers={**alex, "Content-Type": "text/plain"},
    )
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["filename"] == "_notes.txt" and att["size"] == 11
    assert att["content_type"] == "text/plain"

    got = client.get(f"{API}/attachments/{att['id']}", headers=alex)
    assert got.content == b"hello world"
    assert got.headers["content-disposition"].startswith("inline;")
    assert "sandbox" in got.headers["content-security-policy"]
    dl = client.get(f"{API}/attachments/{att['id']}?download=true", headers=alex)
    assert dl.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"{API}/attachments/{att['id']}").status_code == 401

    png = client.post(
        f"{API}/items/{item['id']}/attachments/base64",
        json={"filename": "dot.png", "content_base64": base64.b64encode(b"\x89PNG").decode()},
        headers=alex,
    ).json()
    assert png["content_type"] == "image/png"
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["attachment_count"] == 2
    assert [a["filename"] for a in detail["attachments"]] == ["_notes.txt", "dot.png"]

    assert client.delete(f"{API}/attachments/{att['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/attachments/{att['id']}", headers=alex).status_code == 404
    empty = client.post(
        f"{API}/items/{item['id']}/attachments?filename=x", content=b"", headers=alex
    )
    assert empty.status_code == 422


def test_watchers(client, alex, todo, make_account):
    make_account("agent")
    item = _item(client, alex, todo["id"])
    assert [
        w["handle"] for w in client.post(f"{API}/items/{item['id']}/watch", headers=alex).json()
    ] == ["alex"]
    both = client.put(
        f"{API}/items/{item['id']}/watchers", json={"watchers": ["alex", "agent"]}, headers=alex
    ).json()
    assert [w["handle"] for w in both] == ["agent", "alex"]
    left = client.post(f"{API}/items/{item['id']}/unwatch", headers=alex).json()
    assert [w["handle"] for w in left] == ["agent"]
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert [w["handle"] for w in detail["watchers"]] == ["agent"]


def test_activity_interleaves_comments_and_history(client, alex, todo):
    item = _item(client, alex, todo["id"])
    client.patch(f"{API}/items/{item['id']}", json={"priority": "high"}, headers=alex)
    c = client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": "on it"}, headers=alex
    ).json()
    client.post(f"{API}/comments/{c['id']}/reactions", json={"emoji": "🎉"}, headers=alex)
    client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=alex)

    feed = client.get(f"{API}/items/{item['id']}/activity", headers=alex).json()
    kinds = [(e["kind"], (e["event"] or {}).get("type")) for e in feed]
    assert kinds[0] == ("event", "item.created")
    assert ("comment", None) in kinds
    assert ("event", "item.commented") not in kinds and ("event", "comment.reacted") not in kinds
    assert kinds.index(("comment", None)) < kinds.index(("event", "item.status_changed"))
    comment = next(e["comment"] for e in feed if e["kind"] == "comment")
    assert comment["reactions"][0]["emoji"] == "🎉"

    only = client.get(f"{API}/items/{item['id']}/activity?include=comments", headers=alex).json()
    assert [e["kind"] for e in only] == ["comment"]
    last = client.get(f"{API}/items/{item['id']}/activity?limit=2", headers=alex).json()
    assert last == feed[-2:]
