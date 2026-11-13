"""The agent reference describes event payloads as the code writes them."""

from __future__ import annotations

from slate.docgen import EVENT_DOCS

API = "/api/v1"


def _events(client, headers, item_id, type_):
    evs = client.get(f"{API}/items/{item_id}/events", headers=headers).json()
    return [e for e in evs if e["type"] == type_]


def test_item_assigned_carries_handles(client, alex, todo, make_account):
    make_account("my-agent")
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Water the plants"}, headers=alex
    ).json()
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["my-agent"]}, headers=alex)
    (ev,) = _events(client, alex, item["id"], "item.assigned")
    assert ev["before"] == [] and ev["after"] == ["my-agent"]
    assert "handles" in EVENT_DOCS["item.assigned"]
    assert "account ids" not in EVENT_DOCS["item.assigned"]


def test_item_mentioned_is_an_edit_that_adds_mentions(client, alex, todo, make_account):
    make_account("my-agent")
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Water the plants"}, headers=alex
    ).json()
    c = client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": "@my-agent hi"}, headers=alex
    ).json()
    assert _events(client, alex, item["id"], "item.mentioned") == []  # a new comment: commented
    client.patch(f"{API}/comments/{c['id']}", json={"body_md": "@my-agent @alex hi"}, headers=alex)
    (ev,) = _events(client, alex, item["id"], "item.mentioned")
    assert ev["after"] == {"comment_id": c["id"], "mentions": ["alex"]}
    doc = EVENT_DOCS["item.mentioned"]
    assert "{comment_id, mentions}" in doc and "edit" in doc and "account_id" not in doc
