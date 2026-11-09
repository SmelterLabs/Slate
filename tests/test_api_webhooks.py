"""REST /api/v1/webhooks: create (secret once), list own, patch, deliveries page, test send."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from conftest import API
from slate import notify
from slate.settings import settings

HOOK = {"url": "https://rx.example.com/slate", "events": ["item.due", "item.assigned"]}


@pytest.fixture
def receiver(monkeypatch):
    seen: list[httpx.Request] = []
    status = {"code": 200}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"])

    monkeypatch.setattr(notify, "transport", httpx.MockTransport(handle))
    return seen, status


def test_create_shows_secret_once_and_never_again(client, alex, make_account):
    _, agent = make_account("agent")
    r = client.post(
        f"{API}/webhooks", json={**HOOK, "filter": {"assignee_me": True}}, headers=agent
    )
    assert r.status_code == 201, r.text
    created = r.json()
    secret = created["secret"]
    assert secret.startswith("whsec_")
    assert created["filter"] == {"assignee_me": True, "due_lead_hours": 24}
    hook_id = created["id"]

    responses = [
        client.get(f"{API}/webhooks", headers=agent),
        client.get(f"{API}/webhooks/{hook_id}", headers=agent),
        client.patch(f"{API}/webhooks/{hook_id}", json={"events": "*"}, headers=agent),
        client.get(f"{API}/webhooks/{hook_id}/deliveries", headers=agent),
        client.get(f"{API}/events?since=0&limit=1000", headers=alex),
        client.get(f"{API}/webhooks", headers=alex),
    ]
    for resp in responses:
        assert resp.status_code == 200, resp.text
        assert secret not in resp.text
        assert "secret" not in resp.text.replace('"type":"webhook.', "")
    assert responses[2].json()["events"] == ["*"]


def test_scope_write_needed_and_own_only(client, alex, make_account):
    _, reader = make_account("reader", scopes=("read",))
    _, agent = make_account("agent")
    _, agent2 = make_account("agent2")
    assert client.get(f"{API}/webhooks", headers=reader).status_code == 403
    assert client.post(f"{API}/webhooks", json=HOOK, headers=reader).status_code == 403
    hook_id = client.post(f"{API}/webhooks", json=HOOK, headers=agent).json()["id"]
    assert client.get(f"{API}/webhooks", headers=agent2).json() == []
    assert client.get(f"{API}/webhooks/{hook_id}", headers=agent2).status_code == 404
    r = client.patch(f"{API}/webhooks/{hook_id}", json={"disabled": True}, headers=agent2)
    assert r.status_code == 404
    assert [h["id"] for h in client.get(f"{API}/webhooks", headers=alex).json()] == [hook_id]


def test_validation_errors_are_422(client, make_account):
    _, agent = make_account("agent")
    bad = [
        {**HOOK, "url": "http://public.example.com/x"},
        {**HOOK, "events": []},
        {**HOOK, "events": ["nope"]},
        {**HOOK, "filter": {"due_lead_hours": 0}},
    ]
    for body in bad:
        assert client.post(f"{API}/webhooks", json=body, headers=agent).status_code == 422, body


def test_patch_disable_enable(client, make_account, monkeypatch):
    from slate.settings import settings

    monkeypatch.setattr(settings, "webhook_private_targets", True)
    _, agent = make_account("agent")
    hook_id = client.post(f"{API}/webhooks", json=HOOK, headers=agent).json()["id"]
    r = client.patch(f"{API}/webhooks/{hook_id}", json={"disabled": True}, headers=agent)
    assert r.json()["disabled_at"] is not None
    r = client.patch(
        f"{API}/webhooks/{hook_id}",
        json={"disabled": False, "url": "http://10.0.0.9:9000/h", "filter": {"tags": ["H3"]}},
        headers=agent,
    )
    out = r.json()
    assert out["disabled_at"] is None
    assert out["url"] == "http://10.0.0.9:9000/h"
    assert out["filter"] == {"tags": ["h3"], "due_lead_hours": 24}


def test_test_send_is_signed_and_logged(client, make_account, receiver):
    seen, status = receiver
    _, agent = make_account("agent")
    created = client.post(f"{API}/webhooks", json=HOOK, headers=agent).json()
    r = client.post(f"{API}/webhooks/{created['id']}/test", headers=agent)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["state"], out["status_code"], out["attempt"]) == ("ok", 200, 1)
    [req] = seen
    assert req.headers["X-Slate-Event"] == "webhook.test"
    assert req.headers["X-Slate-Delivery"] == out["id"]
    want = hmac.new(created["secret"].encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Slate-Signature"] == f"sha256={want}"
    body = json.loads(req.content)
    assert body["type"] == "webhook.test" and body["item"] is None
    assert body["actor"]["handle"] == "agent"

    status["code"] = 500  # a failing test send is reported, not retried
    out = client.post(f"{API}/webhooks/{created['id']}/test", headers=agent).json()
    assert (out["state"], out["status_code"]) == ("failed", 500)
    log = client.get(f"{API}/webhooks/{created['id']}/deliveries", headers=agent).json()
    assert len(log["deliveries"]) == 2 and log["next_offset"] is None


def test_deliveries_are_paged(client, make_account, receiver):
    _, agent = make_account("agent")
    hook_id = client.post(f"{API}/webhooks", json=HOOK, headers=agent).json()["id"]
    for _ in range(5):
        client.post(f"{API}/webhooks/{hook_id}/test", headers=agent)
    url = f"{API}/webhooks/{hook_id}/deliveries"
    page1 = client.get(url, params={"limit": 2}, headers=agent).json()
    assert len(page1["deliveries"]) == 2 and page1["next_offset"] == 2
    page3 = client.get(url, params={"limit": 2, "offset": 4}, headers=agent).json()
    assert len(page3["deliveries"]) == 1 and page3["next_offset"] is None
    ids = {d["id"] for d in page1["deliveries"]} | {d["id"] for d in page3["deliveries"]}
    assert len(ids) == 3


def test_testing_a_disabled_hook_is_refused(client, make_account, receiver):
    _, agent = make_account("agent")
    hook_id = client.post(f"{API}/webhooks", json=HOOK, headers=agent).json()["id"]
    client.patch(f"{API}/webhooks/{hook_id}", json={"disabled": True}, headers=agent)
    assert client.post(f"{API}/webhooks/{hook_id}/test", headers=agent).status_code == 422
    assert receiver[0] == []


def test_without_secret_key_webhooks_are_503(client, make_account, monkeypatch):
    _, agent = make_account("agent")
    monkeypatch.setattr(settings, "secret_key", None)
    r = client.post(f"{API}/webhooks", json=HOOK, headers=agent)
    assert r.status_code == 503
    assert "SLATE_SECRET_KEY" in r.json()["detail"]


def test_openapi_lists_the_webhook_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    present = {(verb, path) for path, ops in paths.items() for verb in ops}
    assert present >= {
        ("get", "/api/v1/webhooks"),
        ("post", "/api/v1/webhooks"),
        ("patch", "/api/v1/webhooks/{webhook_id}"),
        ("get", "/api/v1/webhooks/{webhook_id}/deliveries"),
        ("post", "/api/v1/webhooks/{webhook_id}/test"),
    }
