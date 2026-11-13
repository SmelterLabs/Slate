"""Webhook subscriptions: create (secret shown once), update, disable/enable, scope, validation."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from slate import crypto
from slate.models import Event, Source, Webhook
from slate.service import accounts, webhooks
from slate.service.core import Actor
from slate.service.errors import Invalid, NotFound, Unavailable
from slate.settings import settings


@pytest.fixture
def people(db):
    alex, _, alex_tok = accounts.bootstrap(db, handle="alex", display_name="Alex")
    admin = Actor(account_id=alex.id, source=Source.api)
    agent = accounts.create_account(db, admin, handle="agent", display_name="Agent", kind="agent")
    agent2 = accounts.create_account(
        db, admin, handle="agent2", display_name="Agent 2", kind="agent"
    )
    _, agent_tok = accounts.create_token(db, admin, account_id=agent.id, scopes=["write"])
    _, agent2_tok = accounts.create_token(db, admin, account_id=agent2.id, scopes=["write"])
    return {
        "alex": (admin, accounts.resolve_token(db, alex_tok)),
        "agent": (
            Actor(account_id=agent.id, source=Source.api),
            accounts.resolve_token(db, agent_tok),
        ),
        "agent2": (
            Actor(account_id=agent2.id, source=Source.api),
            accounts.resolve_token(db, agent2_tok),
        ),
    }


def _create(db, who, **kw):
    actor, _ = who
    kw.setdefault("url", "https://hooks.example.com/slate")
    kw.setdefault("events", ["item.due"])
    return webhooks.create_webhook(db, actor, **kw)


def test_create_returns_plaintext_once_and_stores_it_encrypted(db, people):
    hook, secret = _create(db, people["agent"], filter={"assignee_me": True})
    assert secret.startswith("whsec_") and len(secret) > 30
    row = db.get(Webhook, hook.id)
    assert secret not in row.secret_enc
    assert crypto.decrypt(row.secret_enc, aad=row.id) == secret
    assert row.account_id == people["agent"][0].account_id
    assert row.filter_json == {"assignee_me": True, "due_lead_hours": 24}
    view = webhooks.view(row)
    assert "secret" not in json.dumps(view, default=str)


def test_secret_never_appears_in_any_event(db, people):
    hook, secret = _create(db, people["agent"])
    webhooks.update_webhook(
        db, people["agent"][0], hook.id, {"url": "https://b.example.com"}, by=people["agent"][1]
    )
    webhooks.update_webhook(
        db, people["agent"][0], hook.id, {"disabled": True}, by=people["agent"][1]
    )
    events = list(db.scalars(select(Event).where(Event.subject_id == hook.id)))
    assert [e.type for e in events] == ["webhook.created", "webhook.updated", "webhook.disabled"]
    dump = json.dumps([[e.before_json, e.after_json] for e in events])
    assert secret not in dump
    assert "secret_enc" not in dump and "v1:" not in dump


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/x",
        "http://127.0.0.1:9000/hook",
        "http://10.0.0.5/hook",
        "http://192.168.1.20/hook",
        "http://100.101.102.103/hook",  # tailnet (CGNAT range)
        "http://[fd7a:115c:a1e0::1]/hook",
        "http://localhost:8080/hook",
        "http://homeserver/hook",
        "http://pc.example.internal/hook",
    ],
)
def test_accepted_urls(db, people, url, monkeypatch):
    from slate.settings import settings

    monkeypatch.setattr(settings, "webhook_private_targets", True)  # a receiver on this network
    hook, _ = _create(db, people["agent"], url=url)
    assert hook.url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/x",  # plain http to a public name
        "http://8.8.8.8/x",
        "ftp://10.0.0.1/x",
        "https:///nohost",
        "not a url",
    ],
)
def test_refused_urls(db, people, url):
    with pytest.raises(Invalid):
        _create(db, people["agent"], url=url)


@pytest.mark.parametrize(
    ("events", "ok"),
    [
        (["*"], True),
        ("*", True),
        (["item.due", "item.assigned", "recurrence.spawned"], True),
        ([], False),
        (["Item.Due"], False),
        (["due"], False),
    ],
)
def test_event_types(db, people, events, ok):
    if ok:
        _create(db, people["agent"], events=events)
    else:
        with pytest.raises(Invalid):
            _create(db, people["agent"], events=events)


@pytest.mark.parametrize(
    "bad",
    [
        {"assignee_me": "yes"},
        {"list_ids": "abc"},
        {"tags": [1]},
        {"due_lead_hours": 0},
        {"due_lead_hours": 10_000},
        {"due_lead_hours": True},
        {"color": "red"},
    ],
)
def test_bad_filters(db, people, bad):
    with pytest.raises(Invalid):
        _create(db, people["agent"], filter=bad)


def test_tags_in_filter_are_normalized(db, people):
    hook, _ = _create(db, people["agent"], filter={"tags": ["H3", " AI "]})
    assert hook.filter_json["tags"] == ["h3", "ai"]


def test_scope_own_only_admin_sees_all(db, people):
    c, _ = _create(db, people["agent"])
    m, _ = _create(db, people["agent2"])
    agent_by, agent2_by, admin_by = (people[h][1] for h in ("agent", "agent2", "alex"))
    assert [h.id for h in webhooks.list_webhooks(db, agent_by)] == [c.id]
    assert {h.id for h in webhooks.list_webhooks(db, admin_by)} == {c.id, m.id}
    with pytest.raises(NotFound):
        webhooks.get_webhook(db, m.id, by=agent_by)
    with pytest.raises(NotFound):
        webhooks.update_webhook(db, people["agent"][0], m.id, {"events": ["*"]}, by=agent_by)
    with pytest.raises(NotFound):
        webhooks.list_deliveries(db, m.id, by=agent_by)
    # admin can manage someone else's
    webhooks.update_webhook(db, people["alex"][0], m.id, {"disabled": True}, by=admin_by)
    assert webhooks.get_webhook(db, m.id, by=agent2_by).disabled_at is not None


def test_disable_enable_and_update_through_changes(db, people):
    actor, by = people["agent"]
    hook, _ = _create(db, people["agent"])
    webhooks.update_webhook(db, actor, hook.id, {"disabled": True}, by=by)
    assert hook.disabled_at is not None
    webhooks.update_webhook(db, actor, hook.id, {"disabled": False}, by=by)
    assert hook.disabled_at is None
    webhooks.update_webhook(
        db, actor, hook.id, {"events": ["*"], "filter": {"due_lead_hours": 2}}, by=by
    )
    assert hook.events_json == ["*"]
    assert hook.filter_json == {"due_lead_hours": 2}
    with pytest.raises(Invalid):
        webhooks.update_webhook(db, actor, hook.id, {"secret": "x"}, by=by)


def test_no_secret_key_means_webhooks_refuse(db, people, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", None)
    with pytest.raises(Unavailable):
        _create(db, people["agent"])
