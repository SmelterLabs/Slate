"""Bootstrap, bearer auth, and the admin endpoints under /api/v1."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from slate.models import Event, Source

API = "/api/v1"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_token(client) -> str:
    r = client.post(f"{API}/bootstrap", json={"handle": "alex", "display_name": "Alex"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _make_agent(client, admin_token, handle="agent", scopes=("write",)) -> tuple[dict, dict]:
    acct = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "agent"},
        headers=_auth(admin_token),
    ).json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens",
        json={"scopes": list(scopes), "label": "test"},
        headers=_auth(admin_token),
    ).json()
    return acct, tok


# --- bootstrap -----------------------------------------------------------------------------


def test_bootstrap_returns_admin_token_once_then_410(client):
    r = client.post(f"{API}/bootstrap", json={"handle": "alex", "display_name": "Alex"})
    assert r.status_code == 201
    body = r.json()
    assert body["account"]["handle"] == "alex"
    assert body["account"]["kind"] == "person"
    assert body["scopes"] == ["admin"]
    assert body["token"].startswith("slate_")

    again = client.post(f"{API}/bootstrap", json={"handle": "x1", "display_name": "X"})
    assert again.status_code == 410


def test_bootstrap_validates_input(client):
    r = client.post(f"{API}/bootstrap", json={"handle": "Not Valid", "display_name": "X"})
    assert r.status_code == 422


# --- auth ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}, {"Authorization": ""}],
)
def test_missing_or_invalid_token_is_401(client, admin_token, headers):
    r = client.get(f"{API}/me", headers=headers)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_me_returns_account_and_scopes(client, admin_token):
    r = client.get(f"{API}/me", headers=_auth(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["handle"] == "alex"
    assert body["scopes"] == ["admin"]
    assert body["token_id"]


def test_insufficient_scope_is_403(client, admin_token):
    _, tok = _make_agent(client, admin_token, scopes=("write",))
    assert client.get(f"{API}/accounts", headers=_auth(tok["token"])).status_code == 403
    r = client.post(
        f"{API}/accounts",
        json={"handle": "agent2", "display_name": "Agent 2", "kind": "agent"},
        headers=_auth(tok["token"]),
    )
    assert r.status_code == 403


def test_read_scope_can_call_me(client, admin_token):
    _, tok = _make_agent(client, admin_token, scopes=("read",))
    assert client.get(f"{API}/me", headers=_auth(tok["token"])).status_code == 200


def test_revoked_token_is_401(client, admin_token):
    _, tok = _make_agent(client, admin_token)
    r = client.delete(f"{API}/tokens/{tok['id']}", headers=_auth(admin_token))
    assert r.status_code == 204
    assert client.get(f"{API}/me", headers=_auth(tok["token"])).status_code == 401


# --- accounts ------------------------------------------------------------------------------


def test_create_and_list_accounts(client, admin_token):
    r = client.post(
        f"{API}/accounts",
        json={"handle": "agent", "display_name": "Agent", "kind": "agent"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "agent"
    listed = client.get(f"{API}/accounts", headers=_auth(admin_token)).json()
    assert [a["handle"] for a in listed] == ["agent", "alex"]


def test_duplicate_handle_is_409_and_bad_kind_is_422(client, admin_token):
    body = {"handle": "agent", "display_name": "Agent", "kind": "agent"}
    assert client.post(f"{API}/accounts", json=body, headers=_auth(admin_token)).status_code == 201
    assert client.post(f"{API}/accounts", json=body, headers=_auth(admin_token)).status_code == 409
    bad = {**body, "handle": "other", "kind": "runtime-profile"}
    assert client.post(f"{API}/accounts", json=bad, headers=_auth(admin_token)).status_code == 422


def test_create_account_records_actor_source_and_session_ref(client, admin_token, db):
    r = client.post(
        f"{API}/accounts",
        json={"handle": "agent", "display_name": "Agent", "kind": "agent"},
        headers={**_auth(admin_token), "X-Slate-Session-Ref": "pr-2-test"},
    )
    me = client.get(f"{API}/me", headers=_auth(admin_token)).json()
    ev = db.scalar(select(Event).where(Event.subject_id == r.json()["id"]))
    assert ev.type == "account.created"
    assert ev.actor_id == me["account"]["id"]
    assert ev.source == Source.api
    assert ev.session_ref == "pr-2-test"


# --- tokens --------------------------------------------------------------------------------


def test_create_token_returns_plaintext_once(client, admin_token):
    acct, tok = _make_agent(client, admin_token, scopes=("read", "write"))
    assert tok["token"].startswith(f"slate_{tok['id']}_")
    assert tok["scopes"] == ["read", "write"]
    assert tok["account_id"] == acct["id"]
    assert tok["label"] == "test"
    # no endpoint ever shows it again
    me = client.get(f"{API}/me", headers=_auth(tok["token"])).json()
    assert tok["token"] not in str(me)


def test_create_token_errors(client, admin_token):
    acct, _ = _make_agent(client, admin_token)
    url = f"{API}/accounts/{acct['id']}/tokens"
    assert client.post(url, json={"scopes": ["god"]}, headers=_auth(admin_token)).status_code == 422
    missing = f"{API}/accounts/missing00000/tokens"
    r = client.post(missing, json={"scopes": ["read"]}, headers=_auth(admin_token))
    assert r.status_code == 404


def test_delete_own_token_without_admin(client, admin_token):
    _, tok = _make_agent(client, admin_token, scopes=("read",))
    r = client.delete(f"{API}/tokens/{tok['id']}", headers=_auth(tok["token"]))
    assert r.status_code == 204


def test_delete_someone_elses_token_is_403(client, admin_token):
    _, a = _make_agent(client, admin_token, handle="alpha")
    _, b = _make_agent(client, admin_token, handle="bravo")
    r = client.delete(f"{API}/tokens/{a['id']}", headers=_auth(b["token"]))
    assert r.status_code == 403


def test_delete_unknown_token_is_404(client, admin_token):
    r = client.delete(f"{API}/tokens/missing00000", headers=_auth(admin_token))
    assert r.status_code == 404


def test_endpoints_are_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in [
        "/bootstrap",
        "/me",
        "/accounts",
        "/accounts/{account_id}/tokens",
        "/tokens/{token_id}",
    ]:
        assert f"{API}{p}" in paths


# --- source: the board says `ui` (X-Slate-Source) -------------------------------------------


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"X-Slate-Client": "browser", "X-Slate-Source": "ui"}, Source.ui),
        ({}, Source.api),
        ({"X-Slate-Client": "browser"}, Source.api),
        ({"X-Slate-Source": "ui"}, Source.api),  # not the board: no browser header
        ({"X-Slate-Client": "browser", "X-Slate-Source": "mcp"}, Source.api),
        ({"X-Slate-Client": "browser", "X-Slate-Source": "system"}, Source.api),
        ({"X-Slate-Client": "browser", "X-Slate-Source": "UI"}, Source.api),
    ],
)
def test_rest_write_source_comes_from_the_board_headers(client, admin_token, db, extra, expected):
    r = client.post(
        f"{API}/accounts",
        json={"handle": "agent", "display_name": "Agent", "kind": "agent"},
        headers={**_auth(admin_token), **extra},
    )
    assert r.status_code == 201, r.text
    ev = db.scalar(select(Event).where(Event.subject_id == r.json()["id"]))
    assert ev.source == expected
