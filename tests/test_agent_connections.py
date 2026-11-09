"""Connection consent, secret handling and exact credential ownership."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from slate.db import SessionLocal
from slate.models import Event, Token, utcnow
from slate.service import access

API = "/api/v1/agent-connections"


@pytest.fixture(autouse=True)
def reset_attempts():
    from slate.api.connections import exchange_attempts, issue_attempts

    for attempts in (exchange_attempts, issue_attempts):
        attempts.clients.clear()
        attempts.all.clear()


@pytest.fixture
def setup_connection(client, alex, todo, make_account):
    agent, headers = make_account()
    body = {"account_id": agent["id"], "label": "Laptop", "list_ids": [todo["id"]]}
    return agent, headers, body


def issue(client, alex, body):
    r = client.post(API + "/pairings", headers=alex, json=body)
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def exchange(client, code, **extra):
    return client.post(
        API + "/exchange",
        json={
            "code": code,
            "device_name": "test device",
            "adapter": "dummy",
            "version": "1",
            "capabilities": ["task"],
            **extra,
        },
    )


def test_pair_exchange_scope_replay_and_revoke(client, alex, setup_connection, db):
    agent, other, body = setup_connection
    paired = issue(client, alex, body)
    assert paired["connection"]["state"] == "waiting"
    result = exchange(client, paired["code"])
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["account_handle"] == agent["handle"]
    assert data["connection"]["state"] == "connected"
    assert result.headers["cache-control"] == "no-store"
    own = {"Authorization": "Bearer " + data["token"]}
    cid = data["connection"]["id"]
    token = db.scalar(select(Token).where(Token.id == data["token"].split("_")[1]))
    assert token.scopes == "write"
    assert token.limit_json == {"list_ids": body["list_ids"], "level": "edit"}
    assert exchange(client, paired["code"]).status_code == 410
    assert exchange(client, "wrong").status_code == 410
    assert (
        client.post(
            API + f"/{cid}/heartbeat", headers=other, json={"check": "connected"}
        ).status_code
        == 403
    )
    assert client.delete(API + f"/{cid}", headers=other).status_code == 403
    assert (
        client.post(API + f"/{cid}/heartbeat", headers=own, json={"check": "connected"}).status_code
        == 200
    )
    assert client.delete(API + f"/{cid}", headers=own).status_code == 204
    assert client.get("/api/v1/me", headers=own).status_code == 401
    view = client.get(API, params={"account_id": agent["id"]}, headers=alex).json()
    assert view["connections"][0]["state"] == "revoked"
    dump = json.dumps(
        [
            {"before": e.before_json, "after": e.after_json, "session_ref": e.session_ref}
            for e in db.scalars(select(Event))
        ]
    )
    for secret in (paired["code"], data["token"], token.token_hash):
        assert secret not in dump
    assert "test device" not in dump


def test_expired_disabled_and_revoked_issuer(client, alex, setup_connection, db):
    from freezegun import freeze_time

    agent, _, body = setup_connection
    paired = issue(client, alex, {**body, "ttl_seconds": 60})
    with freeze_time(utcnow() + timedelta(seconds=61)):
        assert exchange(client, paired["code"]).status_code == 410
    paired = issue(client, alex, body)
    owner = client.get("/api/v1/me", headers=alex).json()["account"]
    # Issue using an independent manager credential, then revoke it.
    new = client.post(
        f"/api/v1/accounts/{owner['id']}/tokens", headers=alex, json={"scopes": ["admin"]}
    ).json()
    manager = {"Authorization": "Bearer " + new["token"]}
    p2 = issue(client, manager, body)
    client.delete("/api/v1/tokens/" + new["id"], headers=alex)
    assert exchange(client, p2["code"]).status_code == 410
    client.patch("/api/v1/accounts/" + agent["id"], headers=alex, json={"disabled": True})
    assert exchange(client, paired["code"]).status_code == 410


def test_explicit_scope_and_manager_required(client, alex, setup_connection):
    agent, other, body = setup_connection
    for bad in (
        {**body, "list_ids": []},
        {**body, "ttl_seconds": 901},
        {**body, "list_ids": ["missing"]},
    ):
        assert client.post(API + "/pairings", headers=alex, json=bad).status_code == 422
    assert client.post(API + "/pairings", headers=other, json=body).status_code == 403
    owner = client.get("/api/v1/me", headers=alex).json()["account"]
    limited = client.post(
        f"/api/v1/accounts/{owner['id']}/tokens",
        headers=alex,
        json={"scopes": ["admin"], "limit": {"list_ids": body["list_ids"]}},
    ).json()
    assert (
        client.post(
            API + "/pairings", headers={"Authorization": "Bearer " + limited["token"]}, json=body
        ).status_code
        == 403
    )
    assert (
        client.post(
            API + "/pairings", headers=alex, json={**body, "account_id": owner["id"]}
        ).status_code
        == 422
    )


def test_status_truth_capability_no_logs_and_stale(client, alex, setup_connection, db):
    from freezegun import freeze_time

    agent, _, body = setup_connection
    data = exchange(client, issue(client, alex, body)["code"]).json()
    own = {"Authorization": "Bearer " + data["token"]}
    url = API + "/" + data["connection"]["id"] + "/heartbeat"
    assert client.post(url, headers=own, json={"check": "ready"}).status_code == 422
    assert (
        client.post(
            url, headers=own, json={"check": "connected", "capabilities": ["task", "probe"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, headers=own, json={"check": "connected", "detail": "DO NOT LOG THIS"}
        ).status_code
        == 422
    )
    count = len(list(db.scalars(select(Event))))
    r = client.post(url, headers=own, json={"check": "connected"})
    assert r.status_code == 200, r.text
    assert len(list(db.scalars(select(Event)))) == count
    assert client.post(url, headers=own, json={"check": "needs_setup"}).json()["state"] == "problem"
    with freeze_time(utcnow() + timedelta(seconds=121)):
        view = client.get(API, params={"account_id": agent["id"]}, headers=alex).json()
        assert view["connections"][0]["state"] == "offline"
    dump = json.dumps([e.after_json for e in db.scalars(select(Event))])
    assert "DO NOT LOG THIS" not in dump


def test_successful_probe_is_recent_not_presence(client, alex, setup_connection):
    from freezegun import freeze_time

    agent, _, body = setup_connection
    data = exchange(
        client, issue(client, alex, body)["code"], capabilities=["task", "probe"]
    ).json()
    own = {"Authorization": "Bearer " + data["token"]}
    url = API + "/" + data["connection"]["id"] + "/heartbeat"
    assert client.post(url, headers=own, json={"check": "probe_passed"}).json()["state"] == "ready"
    with freeze_time(utcnow() + timedelta(seconds=121)):
        assert (
            client.post(url, headers=own, json={"check": "connected"}).json()["state"]
            == "connected"
        )


def test_private_connections_events_and_reach(client, alex, setup_connection, make_account, db):
    agent, _, body = setup_connection
    outsider, outside_headers = make_account("outside")
    paired = issue(client, alex, body)
    assert (
        client.get(API, headers=outside_headers, params={"account_id": agent["id"]}).status_code
        == 403
    )
    # Even an ordinary unrestricted member must not get private connection events.
    events = client.get("/api/v1/events", headers=outside_headers).json()
    assert "agent.connection_" not in json.dumps(events)
    ev = db.scalar(select(Event).where(Event.type == "agent.connection_created"))
    from slate.service.core import event_payload

    gate = access.EventGate(outsider["id"], outside_headers["Authorization"].split("_")[1])
    gate.load(db)
    assert not gate.allows(db, event_payload(ev))
    data = exchange(client, paired["code"]).json()
    own = {"Authorization": "Bearer " + data["token"]}
    section = client.post(
        "/api/v1/sections", headers=alex, json={"name": "Other", "audience": "work"}
    ).json()
    otherlist = client.post(
        "/api/v1/lists", headers=alex, json={"name": "Private", "section_id": section["id"]}
    ).json()
    assert client.get("/api/v1/lists/" + otherlist["id"], headers=own).status_code == 404
    assert client.get(API, headers=own, params={"account_id": agent["id"]}).status_code == 200


def test_concurrent_exchange_only_one_token(client, alex, setup_connection):
    from slate.service import connections
    from slate.service.errors import Gone

    _, _, body = setup_connection
    paired = issue(client, alex, body)

    def use(_):
        with SessionLocal() as session:
            try:
                connections.exchange(
                    session,
                    code=paired["code"],
                    device_name="test",
                    adapter="dummy",
                    capabilities=["task"],
                    version="1",
                )
                return "won"
            except Gone:
                return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(use, range(2))) == ["lost", "won"]


def test_anonymous_exchange_rate_limit_and_validation_never_echo_code(client):
    secret = "DO-NOT-ECHO-THIS-CODE"
    malformed = client.post(API + "/exchange", json={"code": secret, "extra": secret})
    assert malformed.status_code == 422
    assert secret not in malformed.text
    statuses = [exchange(client, "bad").status_code for _ in range(10)]
    assert statuses[-1] == 429
    assert exchange(client, "bad").headers["Retry-After"] == "60"


def test_admin_scope_is_not_account_role(client, alex, todo, make_account):
    agent, _ = make_account("target")
    member, member_admin = make_account("member", scopes=("admin",))
    body = {"account_id": agent["id"], "label": "a", "list_ids": [todo["id"]]}
    assert client.post(API + "/pairings", headers=member_admin, json=body).status_code == 403
    issue(client, alex, body)
    assert (
        client.get(API, headers=member_admin, params={"account_id": agent["id"]}).status_code == 403
    )
    owner = client.get("/api/v1/me", headers=alex).json()["account"]
    token = client.post(
        f"/api/v1/accounts/{owner['id']}/tokens", headers=alex, json={"scopes": ["write"]}
    ).json()["token"]
    limited_role = {"Authorization": "Bearer " + token}
    assert "agent.connection_" not in client.get("/api/v1/events", headers=limited_role).text


def test_pairings_are_distinct_and_pending_can_be_cancelled(client, alex, setup_connection):
    _, _, body = setup_connection
    p1, p2 = issue(client, alex, body), issue(client, alex, body)
    a, b = exchange(client, p1["code"]).json(), exchange(client, p2["code"]).json()
    assert a["token"] != b["token"]
    assert (
        client.post(
            API + "/" + a["connection"]["id"] + "/heartbeat",
            headers={"Authorization": "Bearer " + b["token"]},
            json={"check": "connected"},
        ).status_code
        == 403
    )
    p3 = issue(client, alex, body)
    assert client.delete(API + "/" + p3["connection"]["id"], headers=alex).status_code == 204
    assert exchange(client, p3["code"]).status_code == 410


def test_two_guests_two_private_projects_and_share_recheck(client, alex, make_account, db):
    from slate.connection_models import AgentConnection
    from slate.service.core import snapshot

    projects = []
    for handle in ("first", "second"):
        account, headers = make_account(handle)
        assert (
            client.put(
                f"/api/v1/accounts/{account['id']}/role", headers=alex, json={"role": "guest"}
            ).status_code
            == 200
        )
        section = client.post(
            "/api/v1/sections", headers=alex, json={"name": handle, "audience": "work"}
        ).json()
        client.patch("/api/v1/sections/" + section["id"], headers=alex, json={"private": True})
        tasklist = client.post(
            "/api/v1/lists", headers=alex, json={"section_id": section["id"], "name": handle}
        ).json()
        share = client.put(
            "/api/v1/shares",
            headers=alex,
            json={
                "account_id": account["id"],
                "scope_kind": "list",
                "scope_id": tasklist["id"],
                "level": "edit",
            },
        ).json()
        projects.append((account, headers, tasklist, share))
    first, second = projects
    paired = issue(
        client,
        alex,
        {"account_id": first[0]["id"], "label": "private", "list_ids": [first[2]["id"]]},
    )
    row = db.get(AgentConnection, paired["connection"]["id"])
    assert "secret_hash" not in snapshot(row)
    assert (
        client.post(
            API + "/pairings",
            headers=alex,
            json={"account_id": first[0]["id"], "label": "bad", "list_ids": [second[2]["id"]]},
        ).status_code
        == 422
    )
    for account, headers, _tasklist, _share in projects:
        response = client.get(API, headers=headers, params={"account_id": first[0]["id"]})
        assert response.status_code == (200 if account == first[0] else 403)
    assert "agent.connection_" not in client.get("/api/v1/events", headers=second[1]).text
    assert client.delete("/api/v1/shares/" + first[3]["id"], headers=alex).status_code == 204
    assert exchange(client, paired["code"]).status_code == 410


def test_connection_lifecycle_events_have_exact_actor_and_no_session_input(
    client, alex, setup_connection, db
):
    agent, _, body = setup_connection
    issuer_id = client.get("/api/v1/me", headers=alex).json()["account"]["id"]
    paired = issue(client, {**alex, "X-Slate-Session-Ref": "USER-SUPPLIED-SECRET"}, body)
    data = exchange(client, paired["code"]).json()
    headers = {
        "Authorization": "Bearer " + data["token"],
        "X-Slate-Session-Ref": "USER-SUPPLIED-SECRET",
    }
    cid = data["connection"]["id"]
    assert (
        client.post(
            API + f"/{cid}/heartbeat", headers=headers, json={"check": "needs_setup"}
        ).status_code
        == 200
    )
    assert client.delete(API + f"/{cid}", headers=headers).status_code == 204
    events = list(
        db.scalars(
            select(Event).where(Event.type.startswith("agent.connection_")).order_by(Event.id)
        )
    )
    assert [event.type for event in events] == [
        "agent.connection_created",
        "agent.connection_connected",
        "agent.connection_checked",
        "agent.connection_revoked",
    ]
    assert [event.actor_id for event in events] == [issuer_id, issuer_id, agent["id"], agent["id"]]
    assert all(event.source.value == "api" and event.session_ref is None for event in events)
    assert all(event.subject_id == agent["id"] for event in events)
    assert all(
        set(event.after_json) == {"connection_id", "account_id", "state"} for event in events
    )


def test_stale_authenticated_principal_cannot_heartbeat_after_token_revocation(
    client, alex, setup_connection, db
):
    from slate.service import accounts, connections
    from slate.service.core import Actor
    from slate.service.errors import Forbidden

    _, _, body = setup_connection
    data = exchange(client, issue(client, alex, body)["code"]).json()
    stale = accounts.resolve_token(db, data["token"], touch=False)
    assert client.delete("/api/v1/tokens/" + stale.token_id, headers=alex).status_code == 204
    with pytest.raises(Forbidden):
        connections.heartbeat(
            db,
            Actor(stale.account.id, "api"),
            by=stale,
            connection_id=data["connection"]["id"],
            check="connected",
        )
