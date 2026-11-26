"""Workspace AI settings, encrypted credentials, ChatGPT OAuth, live model discovery,
and multi-provider AI task creation dispatch."""

from __future__ import annotations

import base64
import json
import urllib.parse

import httpx
import pytest
from sqlalchemy import select

from conftest import API
from slate.models import Event, WorkspaceSetting
from slate.service import ai_settings, aiadd
from slate.settings import settings


def _make_id_token(email: str) -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).rstrip(b"=").decode()
    return f"{hdr}.{body}.sig"


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a1" * 32)


def test_ai_settings_admin_only_and_encrypts_api_key(
    client, alex, make_account, db, monkeypatch, secret_key
):
    monkeypatch.setattr(settings, "ai_provider", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_url", "")

    _, m_hdr = make_account("sam", ("write",), kind="person")
    _, a_hdr = make_account("my-agent", ("admin",), kind="agent")

    assert client.get(f"{API}/settings/ai", headers=m_hdr).status_code == 403
    assert (
        client.put(f"{API}/settings/ai", json={"enabled": True}, headers=m_hdr).status_code == 403
    )
    assert client.get(f"{API}/settings/ai", headers=a_hdr).status_code == 403
    assert (
        client.put(f"{API}/settings/ai", json={"enabled": True}, headers=a_hdr).status_code == 403
    )

    # Initially inactive.
    r = client.get(f"{API}/settings/ai", headers=alex)
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["source"] == "none"
    assert client.get(f"{API}/me", headers=alex).json()["ai_add"] is False

    # Save Anthropic key -> encrypted at rest, never exposed in response or event log.
    secret_val = "sk-ant-test-super-secret-value"
    r = client.put(
        f"{API}/settings/ai",
        json={
            "enabled": True,
            "provider": "anthropic",
            "model": "claude-sonnet-5-5",
            "api_key": secret_val,
        },
        headers=alex,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is True
    assert body["source"] == "db"
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-sonnet-5-5"
    assert body["has_api_key"] is True
    assert secret_val not in json.dumps(body)

    # /me now reports ai_add=True.
    assert client.get(f"{API}/me", headers=alex).json()["ai_add"] is True

    # Raw DB row has encrypted blob, never plaintext.
    db.expire_all()
    row = db.get(WorkspaceSetting, ai_settings.KEY)
    assert row is not None
    assert "api_key_enc" in row.value_json
    assert secret_val not in json.dumps(row.value_json)

    # Event log has settings.changed with no secret leak.
    ev = db.scalar(
        select(Event)
        .where(Event.type == "settings.changed", Event.field == ai_settings.KEY)
        .order_by(Event.id.desc())
    )
    assert ev is not None
    assert secret_val not in json.dumps({"before": ev.before_json, "after": ev.after_json})
    assert ev.after_json["has_api_key"] is True
    assert ev.after_json["active"] is True

    # Clearing API key deactivates cloud provider.
    r = client.put(f"{API}/settings/ai", json={"clear_api_key": True}, headers=alex)
    assert r.status_code == 200
    assert r.json()["has_api_key"] is False
    assert r.json()["active"] is False


def test_ai_settings_requires_secret_key_for_api_key(client, alex, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    r = client.put(
        f"{API}/settings/ai",
        json={"enabled": True, "provider": "openai", "api_key": "sk-test"},
        headers=alex,
    )
    assert r.status_code == 422
    assert "SLATE_SECRET_KEY" in r.json()["detail"]


def test_chatgpt_oauth_flow_and_refresh(client, alex, db, monkeypatch, secret_key):
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == ai_settings.OPENAI_TOKEN_URL
        form = urllib.parse.parse_qs(req.content.decode())
        grant = form["grant_type"][0]
        if grant == "authorization_code":
            assert form["code"][0] == "auth-code-123"
            assert form["code_verifier"][0]
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-access-1",
                    "refresh_token": "tok-refresh-1",
                    "expires_in": 3600,
                    "client_id": "issued-client-99",
                    "id_token": _make_id_token("alex@example.com"),
                },
            )
        if grant == "refresh_token":
            assert form["refresh_token"][0] == "tok-refresh-1"
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-access-2",
                    "refresh_token": "tok-refresh-2",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(400)

    monkeypatch.setattr(ai_settings, "transport", httpx.MockTransport(handler))

    start = client.post(f"{API}/settings/ai/oauth/start", headers=alex)
    assert start.status_code == 200
    sdata = start.json()
    assert sdata["authorize_url"].startswith(ai_settings.OPENAI_AUTH_URL)
    assert "code_challenge_method=S256" in sdata["authorize_url"]
    state = sdata["state"]

    cb_url = f"http://localhost:1455/auth/callback?code=auth-code-123&state={state}"
    done = client.post(
        f"{API}/settings/ai/oauth/complete",
        json={"callback": cb_url, "state": state},
        headers=alex,
    )
    assert done.status_code == 200
    body = done.json()
    assert body["provider"] == "chatgpt-oauth"
    assert body["active"] is True
    assert body["oauth_connected"] is True
    assert body["oauth_account"] == "alex@example.com"
    assert "tok-access-1" not in json.dumps(body)
    assert "tok-refresh-1" not in json.dumps(body)

    # Refresh works when expired.
    db.expire_all()
    resolved = ai_settings.resolve(db)
    assert resolved.oauth is not None
    expired = {**resolved.oauth, "expires_at": 1}
    refreshed = ai_settings.refresh_chatgpt_token(expired)
    assert refreshed["access_token"] == "tok-access-2"
    assert refreshed["refresh_token"] == "tok-refresh-2"

    # Disconnect removes OAuth state.
    disc = client.post(f"{API}/settings/ai/oauth/disconnect", headers=alex)
    assert disc.status_code == 200
    assert disc.json()["oauth_connected"] is False
    assert disc.json()["active"] is False


def test_discover_models_across_providers(client, alex, monkeypatch, secret_key):
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "api.anthropic.com/v1/models" in url:
            return httpx.Response(
                200,
                json={"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"}]},
            )
        if "api.openai.com/v1/models" in url:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-5.4-mini"},
                        {"id": "text-embedding-3-small"},
                        {"id": "o3-mini"},
                    ]
                },
            )
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-flash",
                            "displayName": "Gemini 2.5 Flash",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/embedding-001",
                            "displayName": "Embedding",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        if "11434/api/tags" in url:
            return httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
        if "11435/models" in url:
            return httpx.Response(200, json={"models": [{"id": "sonnet", "label": "Sonnet (5.5)"}]})
        return httpx.Response(404)

    monkeypatch.setattr(ai_settings, "transport", httpx.MockTransport(handler))

    r1 = client.post(
        f"{API}/settings/ai/models",
        json={"provider": "anthropic", "api_key": "sk-ant"},
        headers=alex,
    )
    assert r1.json()["models"] == [{"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"}]

    r2 = client.post(
        f"{API}/settings/ai/models",
        json={"provider": "openai", "api_key": "sk-oai"},
        headers=alex,
    )
    ids2 = [m["id"] for m in r2.json()["models"]]
    assert "gpt-5.4-mini" in ids2
    assert "o3-mini" in ids2
    assert "text-embedding-3-small" not in ids2

    r3 = client.post(
        f"{API}/settings/ai/models",
        json={"provider": "gemini", "api_key": "gem-key"},
        headers=alex,
    )
    assert r3.json()["models"] == [{"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"}]

    r4 = client.post(
        f"{API}/settings/ai/models",
        json={"provider": "ollama", "url": "http://localhost:11434"},
        headers=alex,
    )
    assert r4.json()["models"] == [{"id": "llama3.2:latest", "label": "llama3.2:latest"}]

    r5 = client.post(
        f"{API}/settings/ai/models",
        json={"provider": "claude-cli", "url": "http://localhost:11435"},
        headers=alex,
    )
    assert r5.json()["models"] == [{"id": "sonnet", "label": "Sonnet (5.5)"}]


def test_ai_add_dispatches_openai_gemini_and_cli_bridge(
    client, alex, todo, monkeypatch, secret_key
):
    list_id = todo["id"]

    # 1. OpenAI chat completions JSON mode
    def oai_handler(req: httpx.Request) -> httpx.Response:
        assert "/v1/chat/completions" in str(req.url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"title": "Buy milk", "list_id": list_id})}}
                ]
            },
        )

    monkeypatch.setattr(aiadd, "transport", httpx.MockTransport(oai_handler))
    client.put(
        f"{API}/settings/ai",
        json={
            "enabled": True,
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "api_key": "sk-oai-test",
        },
        headers=alex,
    )
    r = client.post(f"{API}/items/ai", json={"text": "buy milk"}, headers=alex)
    assert r.status_code == 201
    assert r.headers.get("X-Slate-AI-Fallback") is None
    assert r.json()["title"] == "Buy milk"

    # Test connection endpoint also succeeds against the mocked provider.
    t = client.post(f"{API}/settings/ai/test", json={}, headers=alex)
    assert t.status_code == 200
    assert t.json()["ok"] is True

    # 2. Gemini generateContent
    def gem_handler(req: httpx.Request) -> httpx.Response:
        assert "generativelanguage.googleapis.com" in str(req.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": json.dumps({"title": "Call dentist", "list_id": list_id})}
                            ]
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(aiadd, "transport", httpx.MockTransport(gem_handler))
    client.put(
        f"{API}/settings/ai",
        json={
            "enabled": True,
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_key": "gem-key",
        },
        headers=alex,
    )
    r = client.post(f"{API}/items/ai", json={"text": "call dentist"}, headers=alex)
    assert r.status_code == 201
    assert r.headers.get("X-Slate-AI-Fallback") is None
    assert r.json()["title"] == "Call dentist"

    # 3. Claude CLI via host bridge
    def bridge_handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "http://host.docker.internal:11435/complete"
        payload = json.loads(req.content.decode())
        assert payload["cli"] == "claude"
        assert payload["model"] == "sonnet"
        return httpx.Response(
            200,
            json={"text": json.dumps({"title": "Renew passport", "list_id": list_id})},
        )

    monkeypatch.setattr(aiadd, "transport", httpx.MockTransport(bridge_handler))
    client.put(
        f"{API}/settings/ai",
        json={
            "enabled": True,
            "provider": "claude-cli",
            "model": "sonnet",
            "url": "http://host.docker.internal:11435",
        },
        headers=alex,
    )
    r = client.post(f"{API}/items/ai", json={"text": "renew passport"}, headers=alex)
    assert r.status_code == 201
    assert r.headers.get("X-Slate-AI-Fallback") is None
    assert r.json()["title"] == "Renew passport"


def test_backup_provider_failover_and_claude_picker_models(
    client, alex, todo, db, tmp_path, monkeypatch, secret_key
):
    list_id = todo["id"]

    # 1. Claude CLI picker reads real versioned model IDs + additionalModelOptionsCache
    fake_claude = tmp_path / ".claude.json"
    fake_claude.write_text(
        json.dumps(
            {
                "models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
                "additionalModelOptionsCache": [
                    {
                        "value": "claude-fable-5-2[1m]",
                        "label": "Fable",
                        "description": "Fable 5.2 · Extended",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_settings.Path, "home", classmethod(lambda cls: tmp_path))
    models = ai_settings._read_claude_cli_models()
    ids = [m["id"] for m in models]
    assert ids[:3] == ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
    assert "claude-fable-5-2[1m]" in ids

    # 2. Primary fails -> automatic failover to backup provider without X-Slate-AI-Fallback
    calls: list[str] = []

    def failover_handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        calls.append(url)
        if "api.anthropic.com" in url:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        if "11434/api/chat" in url:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": json.dumps({"title": "Backup parsed task", "list_id": list_id})
                    }
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(aiadd, "transport", httpx.MockTransport(failover_handler))
    res = client.put(
        f"{API}/settings/ai",
        json={
            "enabled": True,
            "provider": "anthropic",
            "model": "claude-sonnet-5-5",
            "api_key": "sk-ant-primary",
            "backup": {
                "enabled": True,
                "provider": "ollama",
                "model": "gemma4:latest",
                "url": "http://localhost:11434",
            },
        },
        headers=alex,
    )
    assert res.status_code == 200
    assert res.json()["backup"]["active"] is True
    assert res.json()["backup"]["provider"] == "ollama"

    r = client.post(f"{API}/items/ai", json={"text": "test backup failover"}, headers=alex)
    assert r.status_code == 201
    assert r.headers.get("X-Slate-AI-Fallback") is None
    assert r.json()["title"] == "Backup parsed task"
    assert any("api.anthropic.com" in u for u in calls)
    assert any("11434/api/chat" in u for u in calls)

    # Test backup connection explicitly with slot="backup"
    t = client.post(f"{API}/settings/ai/test", json={"slot": "backup"}, headers=alex)
    assert t.status_code == 200
    assert t.json()["ok"] is True
    assert t.json()["provider"] == "ollama"

    # status_reason describes active primary + backup
    v = client.get(f"{API}/settings/ai", headers=alex).json()
    assert "anthropic" in v["status_reason"]
    assert "ollama" in v["status_reason"]
    # _read_codex_cli_models never returns a fake "Default (codex)" entry when missing
    assert ai_settings._read_codex_cli_models() == []
