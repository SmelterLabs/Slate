"""What leaves Slate for someone is built as that someone may read it: a webhook body under
its owner's read filter, an automation's webhook payload under its author's."""

from __future__ import annotations

import json

from sqlalchemy import select

from conftest import API
from slate.models import Delivery, utcnow
from slate.service import automations, deliveries


def _share(client, alex, account_id, list_id, level="edit"):
    r = client.put(
        f"{API}/shares",
        json={"account_id": account_id, "scope_kind": "list", "scope_id": list_id, "level": level},
        headers=alex,
    )
    assert r.status_code == 200, r.text


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


def test_a_guests_webhook_body_names_no_workspace_agent(client, alex, todo, db, make_account):
    agent, _ = make_account("my-agent")
    guest, gh = _person(client, alex, "guest", "guest")
    _share(client, alex, guest["id"], todo["id"])
    r = client.post(
        f"{API}/webhooks", json={"url": "https://hooks.example.com/g", "events": ["*"]}, headers=gh
    )
    assert r.status_code == 201, r.text
    cursor = deliveries.latest_event_id(db)
    card = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Shared card"}, headers=gh
    ).json()
    client.patch(f"{API}/items/{card['id']}", json={"assignees": ["my-agent"]}, headers=alex)
    client.patch(f"{API}/items/{card['id']}", json={"priority": "high"}, headers=gh)
    assert client.get(f"{API}/items/{card['id']}", headers=gh).json()["assignees"] == []
    _, queued = deliveries.fan_out(db, cursor, now=utcnow())
    assert queued
    bodies = [json.loads(d.body) for d in db.scalars(select(Delivery))]
    assert {b["type"] for b in bodies} >= {"item.created", "item.updated"}
    for body in bodies:
        assert body["item"]["assignees"] == [], body
        assert agent["id"] not in json.dumps(body) and "my-agent" not in json.dumps(body)


def test_an_owners_webhook_still_hears_everything(client, alex, todo, db, make_account):
    make_account("my-agent")
    r = client.post(
        f"{API}/webhooks",
        json={"url": "https://hooks.example.com/a", "events": ["item.updated"]},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    cursor = deliveries.latest_event_id(db)
    card = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Card", "assignees": ["my-agent"]},
        headers=alex,
    ).json()
    client.patch(f"{API}/items/{card['id']}", json={"priority": "high"}, headers=alex)
    deliveries.fan_out(db, cursor, now=utcnow())
    [body] = [json.loads(d.body) for d in db.scalars(select(Delivery))]
    assert body["item"]["assignees"] == ["my-agent"]


def test_an_automation_payload_is_built_as_its_author_reads(client, alex, todo, db):
    member, mh = _person(client, alex, "member", "member")
    sec = client.post(
        f"{API}/sections", json={"name": "Private", "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    hidden = client.post(
        f"{API}/lists", json={"section_id": sec["id"], "name": "Hidden"}, headers=alex
    ).json()
    secret = client.post(
        f"{API}/items", json={"list_id": hidden["id"], "title": "Secret"}, headers=alex
    ).json()
    automations.start(db)
    r = client.post(
        f"{API}/automations",
        json={
            "name": "Tell me",
            "list_id": todo["id"],
            "trigger": {"type": "priority_changed"},
            "actions": [{"type": "webhook", "url": "https://hooks.example.com/x"}],
        },
        headers=mh,
    )
    assert r.status_code == 201, r.text
    card = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Card"}, headers=alex
    ).json()
    r = client.post(
        f"{API}/items/{card['id']}/links",
        json={"kind": "relates", "item_id": secret["id"]},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    client.patch(f"{API}/items/{card['id']}", json={"priority": "high"}, headers=alex)
    sent = []
    automations.process(db, send=lambda url, payload, secret: sent.append(payload) or (True, "ok"))
    [payload] = sent
    assert payload["item"]["id"] == card["id"]
    assert secret["id"] not in json.dumps(payload)
    assert member["id"]
