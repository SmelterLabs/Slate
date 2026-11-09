"""Managing agent accounts and tokens: list tokens, rename/disable, usage, rate-limit headers."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from conftest import API
from slate.models import Event
from slate.ratelimit import limiter
from slate.settings import settings


@pytest.fixture(autouse=True)
def _clean():
    limiter.reset()
    yield
    limiter.reset()


def _me(client, h) -> dict:
    return client.get(f"{API}/me", headers=h).json()


def test_list_tokens_shows_metadata_never_secrets(client, alex, make_account):
    agent, ch = make_account("agent", ("read",))
    client.get(f"{API}/me", headers=ch)  # stamps last_used_at
    r = client.get(f"{API}/accounts/{agent['id']}/tokens", headers=alex)
    assert r.status_code == 200, r.text
    [tok] = r.json()
    assert tok["scopes"] == ["read"] and tok["account_id"] == agent["id"]
    assert tok["last_used_at"] is not None and tok["revoked_at"] is None
    assert "token" not in tok and "token_hash" not in tok


def test_list_tokens_hides_revoked_unless_asked(client, alex, make_account):
    agent, ch = make_account("agent")
    tid = _me(client, ch)["token_id"]
    assert client.delete(f"{API}/tokens/{tid}", headers=alex).status_code == 204
    assert client.get(f"{API}/accounts/{agent['id']}/tokens", headers=alex).json() == []
    r = client.get(f"{API}/accounts/{agent['id']}/tokens?include_revoked=true", headers=alex)
    assert [t["id"] for t in r.json()] == [tid] and r.json()[0]["revoked_at"]


def test_an_agent_can_list_its_own_tokens_but_not_others(client, alex, make_account):
    agent, ch = make_account("agent")
    agent2, _ = make_account("agent2")
    assert client.get(f"{API}/accounts/{agent['id']}/tokens", headers=ch).status_code == 200
    assert client.get(f"{API}/accounts/{agent2['id']}/tokens", headers=ch).status_code == 403
    assert client.get(f"{API}/accounts/{agent2['id']}/usage", headers=ch).status_code == 403


def test_disable_stops_tokens_records_events_and_enable_restores(client, alex, make_account, db):
    agent, ch = make_account("agent")
    r = client.patch(f"{API}/accounts/{agent['id']}", json={"disabled": True}, headers=alex)
    assert r.status_code == 200 and r.json()["disabled_at"]
    assert client.get(f"{API}/me", headers=ch).status_code == 401
    assert client.get(f"{API}/agents", headers=alex).json() == []

    r = client.patch(
        f"{API}/accounts/{agent['id']}",
        json={"disabled": False, "display_name": "Agent the agent"},
        headers=alex,
    )
    assert r.json()["disabled_at"] is None and r.json()["display_name"] == "Agent the agent"
    assert client.get(f"{API}/me", headers=ch).status_code == 200
    types = db.scalars(
        select(Event.type).where(Event.subject_id == agent["id"]).order_by(Event.id)
    ).all()
    assert types == [
        "account.created",
        "account.disabled",
        "account.updated",
        "account.enabled",
    ]


def test_cannot_disable_yourself_and_writers_cannot_patch(client, alex, make_account):
    me = _me(client, alex)["account"]
    r = client.patch(f"{API}/accounts/{me['id']}", json={"disabled": True}, headers=alex)
    assert r.status_code == 422
    agent, ch = make_account("agent")
    r = client.patch(f"{API}/accounts/{agent['id']}", json={"display_name": "X"}, headers=ch)
    assert r.status_code == 403
    r = client.patch(f"{API}/accounts/{agent['id']}", json={"kind": "person"}, headers=alex)
    assert r.status_code == 422


def test_usage_counts_events_per_day_by_source_and_requests_per_token(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    for i in range(3):
        client.post(f"{API}/items", json={"list_id": todo["id"], "title": f"t{i}"}, headers=ch)
    client.get(f"{API}/me", headers=ch)
    r = client.get(f"{API}/accounts/{agent['id']}/usage?days=7", headers=alex)
    assert r.status_code == 200, r.text
    u = r.json()
    assert len(u["days"]) == 7 and u["days"][-1]["events"] == 3
    assert u["days"][-1]["by_source"] == {"api": 3}
    assert u["events_in_window"] == 3 and u["events_all_time"] == 3
    assert u["top_types"] == [{"type": "item.created", "count": 3}]
    [tok] = u["tokens"]
    assert tok["requests"] == {"read": 1, "write": 3}
    assert u["throttled"] == 0 and u["rate_limit"] == {"reads": 0, "writes": 0}


def test_every_authed_response_carries_rate_limit_headers(client, alex, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_reads_per_minute", 5)
    first = client.get(f"{API}/me", headers=alex)
    second = client.get(f"{API}/me", headers=alex)
    assert first.headers["x-ratelimit-limit"] == "5"
    assert int(first.headers["x-ratelimit-remaining"]) == 4
    assert int(second.headers["x-ratelimit-remaining"]) == 3
    # A 304 is answered before auth runs: it is free, so it spends nothing.
    etag = second.headers["etag"]
    r = client.get(f"{API}/me", headers={**alex, "If-None-Match": etag})
    assert r.status_code == 304
    third = client.get(f"{API}/sections", headers=alex)
    assert int(third.headers["x-ratelimit-remaining"]) == 2


def test_no_rate_limit_headers_when_limits_are_off(client, alex):
    r = client.get(f"{API}/me", headers=alex)
    assert "x-ratelimit-limit" not in r.headers
