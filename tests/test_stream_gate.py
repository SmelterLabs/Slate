"""The live stream rechecks credentials on every event but recomputes access only when reach
may have changed, and one token cannot hold an unbounded number of streams."""

from __future__ import annotations

from sqlalchemy import select

from slate import db as slate_db
from slate.api import events as events_api
from slate.models import Token
from slate.service import access

API = "/api/v1"


def _gate(client, headers):
    me = client.get(f"{API}/me", headers=headers).json()
    with slate_db.SessionLocal() as s:
        token_id = s.scalar(
            select(Token.id).where(Token.account_id == me["account"]["id"]).limit(1)
        )
        gate = access.EventGate(me["account"]["id"], token_id)
        gate.load(s)
    events_api._fresh(gate)
    return gate, token_id


def _payload(kind="item.updated"):
    return {"id": "zzzzzzzzzzzz", "type": kind, "item_id": None, "subject_id": None}


def test_live_events_do_not_recompute_access_each_time(client, alex, monkeypatch, make_account):
    _, member = make_account("member", ("read",), kind="person")
    gate, token_id = _gate(client, member)
    calls = []
    real = access.compute
    monkeypatch.setattr(access, "compute", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(50):
        events_api._gate_check(gate, _payload())
    assert calls == []
    events_api._gate_check(gate, _payload("share.created"))  # reach may have changed
    assert len(calls) == 1


def test_a_revoked_token_is_cut_off_at_the_next_event(client, alex, make_account):
    _, member = make_account("member", ("read",), kind="person")
    gate, token_id = _gate(client, member)
    events_api._gate_check(gate, _payload())
    assert not gate.denied
    assert client.delete(f"{API}/tokens/{token_id}", headers=alex).status_code == 204
    events_api._gate_check(gate, _payload())
    assert gate.denied


def test_one_token_holds_a_bounded_number_of_streams(client, alex, monkeypatch):
    _, token_id = _gate(client, alex)
    monkeypatch.setitem(events_api._open_streams, token_id, events_api.MAX_STREAMS_PER_TOKEN)
    r = client.get(f"{API}/events/stream", headers=alex)
    assert r.status_code == 429 and "streams" in r.json()["detail"]
