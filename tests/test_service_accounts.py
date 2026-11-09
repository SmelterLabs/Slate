"""Accounts and tokens in the service layer."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from slate.models import Account, Event, Source, Token
from slate.service import accounts
from slate.service.core import Actor
from slate.service.errors import Conflict, Forbidden, Gone, Invalid, NotFound


@pytest.fixture
def boot(db):
    account, token, plaintext = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return account, token, plaintext


@pytest.fixture
def actor(boot) -> Actor:
    return Actor(account_id=boot[0].id, source=Source.api)


def test_bootstrap_creates_admin_person_and_token_events(db, boot):
    account, token, plaintext = boot
    assert account.kind == "person"
    assert token.scopes == "admin"
    assert plaintext.startswith(f"slate_{token.id}_")
    types = [e.type for e in db.scalars(select(Event).order_by(Event.id))]
    assert types == [
        "account.created",
        "token.created",
        "view.created",
        "view.created",
    ]
    assert all(e.actor_id == account.id for e in db.scalars(select(Event)))


def test_bootstrap_only_works_once(db, boot):
    with pytest.raises(Gone):
        accounts.bootstrap(db, handle="other", display_name="Other")


def test_plaintext_token_is_never_stored(db, boot):
    _, _, plaintext = boot
    secret = plaintext.split("_", 2)[2]
    stored = db.scalar(select(Token))
    assert stored.token_hash == accounts.hash_token(plaintext)
    assert secret not in stored.token_hash
    # nowhere in the database: scan every text column of every row in every table
    from sqlalchemy import text

    from slate.db import Base

    for table in Base.metadata.sorted_tables:
        for row in db.execute(text(f"SELECT * FROM {table.name}")):
            assert not any(isinstance(v, str) and secret in v for v in row), table.name


def test_create_account_and_duplicate_handle(db, actor):
    agent = accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    assert agent.kind == "agent"
    ev = db.scalar(select(Event).where(Event.subject_id == agent.id))
    assert ev.type == "account.created" and ev.actor_id == actor.account_id
    with pytest.raises(Conflict):
        accounts.create_account(db, actor, handle="agent", display_name="Again", kind="agent")


@pytest.mark.parametrize(
    ("handle", "name", "kind"),
    [("Agent", "C", "agent"), ("c", "C", "agent"), ("runtime:agent", "C", "agent"),
     ("agent", "  ", "agent"), ("agent", "C", "profile")],
)  # fmt: skip
def test_create_account_validates(db, actor, handle, name, kind):
    with pytest.raises(Invalid):
        accounts.create_account(db, actor, handle=handle, display_name=name, kind=kind)


def test_create_token_scopes_and_errors(db, actor):
    agent = accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    token, plaintext = accounts.create_token(
        db, actor, account_id=agent.id, scopes=["write", "read"], label="laptop"
    )
    assert token.scopes == "read write"
    assert accounts.resolve_token(db, plaintext).account.id == agent.id
    with pytest.raises(Invalid):
        accounts.create_token(db, actor, account_id=agent.id, scopes=["root"])
    with pytest.raises(Invalid):
        accounts.create_token(db, actor, account_id=agent.id, scopes=[])
    with pytest.raises(NotFound):
        accounts.create_token(db, actor, account_id="nope", scopes=["read"])


def test_resolve_token_rejects_bad_input(db, boot):
    _, token, plaintext = boot
    assert accounts.resolve_token(db, "") is None
    assert accounts.resolve_token(db, "garbage") is None
    assert (
        accounts.resolve_token(db, plaintext[:-1] + ("A" if plaintext[-1] != "A" else "B")) is None
    )
    assert accounts.resolve_token(db, f"slate_{'0' * 12}_{'x' * 40}") is None


def test_resolve_token_updates_last_used_at_without_an_event(db, boot):
    _, token, plaintext = boot
    events_before = len(list(db.scalars(select(Event))))
    p = accounts.resolve_token(db, plaintext)
    assert p.scopes == frozenset({"admin"}) and p.token_id == token.id
    db.expire_all()
    first = db.get(Token, token.id).last_used_at
    assert first is not None
    accounts.resolve_token(db, plaintext)  # within the resolution window: no new write
    db.expire_all()
    assert db.get(Token, token.id).last_used_at == first
    assert len(list(db.scalars(select(Event)))) == events_before


def test_last_used_at_refreshes_after_the_window(db, boot, monkeypatch):
    _, token, plaintext = boot
    accounts.resolve_token(db, plaintext)
    first = db.get(Token, token.id).last_used_at
    later = first + accounts.LAST_USED_RESOLUTION + timedelta(seconds=1)
    monkeypatch.setattr(accounts, "utcnow", lambda: later)
    accounts.resolve_token(db, plaintext)
    db.expire_all()
    assert db.get(Token, token.id).last_used_at == later


def test_revoke_token_by_admin_owner_and_stranger(db, boot, actor):
    admin_principal = accounts.resolve_token(db, boot[2])
    agent = accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    t1, p1 = accounts.create_token(db, actor, account_id=agent.id, scopes=["write"])
    t2, p2 = accounts.create_token(db, actor, account_id=agent.id, scopes=["write"])
    agent2 = accounts.create_account(
        db, actor, handle="agent2", display_name="Agent 2", kind="agent"
    )
    _, pb = accounts.create_token(db, actor, account_id=agent2.id, scopes=["write"])

    agent2_p = accounts.resolve_token(db, pb)
    agent2_actor = Actor(account_id=agent2.id, source=Source.api)
    with pytest.raises(Forbidden):
        accounts.revoke_token(db, agent2_actor, token_id=t1.id, by=agent2_p)

    agent_p = accounts.resolve_token(db, p1)
    agent_actor = Actor(account_id=agent.id, source=Source.mcp, session_ref="s")
    accounts.revoke_token(db, agent_actor, token_id=t1.id, by=agent_p)
    assert accounts.resolve_token(db, p1) is None

    accounts.revoke_token(db, actor, token_id=t2.id, by=admin_principal)
    assert accounts.resolve_token(db, p2) is None

    revokes = list(db.scalars(select(Event).where(Event.type == "token.revoked")))
    assert {(e.subject_id, e.actor_id, e.source) for e in revokes} == {
        (t1.id, agent.id, Source.mcp),
        (t2.id, actor.account_id, Source.api),
    }
    assert all(e.before_json is None and e.after_json.endswith("Z") for e in revokes)

    # idempotent: revoking again writes nothing
    accounts.revoke_token(db, actor, token_id=t2.id, by=admin_principal)
    assert len(list(db.scalars(select(Event).where(Event.type == "token.revoked")))) == 2
    with pytest.raises(NotFound):
        accounts.revoke_token(db, actor, token_id="missing00000", by=admin_principal)


def test_disabled_account_tokens_stop_working(db, boot, actor):
    agent = accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    _, plaintext = accounts.create_token(db, actor, account_id=agent.id, scopes=["read"])
    from slate.models import utcnow
    from slate.service.core import apply_change

    with apply_change(db, actor) as cx:
        cx.set(agent, "disabled_at", utcnow(), "account.disabled")
    assert accounts.resolve_token(db, plaintext) is None
    with pytest.raises(Invalid):
        accounts.create_token(db, actor, account_id=agent.id, scopes=["read"])


def test_scope_ranking():
    assert accounts.has_scope({"admin"}, "write")
    assert accounts.has_scope({"write"}, "read")
    assert not accounts.has_scope({"read"}, "write")
    assert not accounts.has_scope({"write"}, "admin")
    assert not accounts.has_scope(set(), "read")


def test_list_and_get_accounts(db, boot, actor):
    accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    assert [a.handle for a in accounts.list_accounts(db)] == ["agent", "alex"]
    assert accounts.get_account(db, boot[0].id).handle == "alex"
    with pytest.raises(NotFound):
        accounts.get_account(db, "missing00000")
    assert accounts.account_count(db) == 2
    assert isinstance(accounts.list_accounts(db)[0], Account)
