"""Workspace AI settings: multi-provider configuration, encrypted credentials, live model
discovery, and Sign in with ChatGPT (OpenAI OAuth 2.0 + PKCE).

Stored in `workspace_settings` under `key = "ai.config"`. Plaintext API keys and OAuth tokens
are encrypted at rest with `slate.crypto` (`SLATE_SECRET_KEY`, aad `ai.config`), never returned
by `GET /settings/ai`, and never written to `settings.changed` event payloads.

When no `ai.config` row exists in the database, `resolve()` falls back to the process's
`SLATE_AI_*` environment settings so existing `.env` installs continue to work untouched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import shutil
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from sqlalchemy.orm import Session

from slate import crypto
from slate.models import AccountKind, WorkspaceSetting
from slate.service import access
from slate.service.core import Actor, apply_change
from slate.service.errors import Forbidden, Invalid
from slate.settings import settings

KEY = "ai.config"
PROVIDERS = (
    "claude-cli",
    "codex-cli",
    "chatgpt-oauth",
    "anthropic",
    "openai",
    "gemini",
    "ollama",
    "openai-compatible",
)
ProviderName = Literal[
    "claude-cli",
    "codex-cli",
    "chatgpt-oauth",
    "anthropic",
    "openai",
    "gemini",
    "ollama",
    "openai-compatible",
]

DEFAULT_MODELS: dict[str, str] = {
    "claude-cli": "claude-sonnet-5-5",
    "codex-cli": "",
    "chatgpt-oauth": "gpt-5.4",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.4",
    "gemini": "gemini-2.5-flash",
    "ollama": "gemma4:latest",
    "openai-compatible": "",
}

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com"
GEMINI_URL = "https://generativelanguage.googleapis.com"
OPENAI_AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
OPENAI_OAUTH_REDIRECT = "http://127.0.0.1:1455/callback"
OPENAI_OAUTH_SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"

# Tests swap in an `httpx.MockTransport`; None means real network.
transport: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class ResolvedAI:
    enabled: bool
    provider: ProviderName | None
    model: str
    url: str
    api_key: str
    oauth: dict[str, Any] | None
    source: Literal["db", "env", "none"]
    host_id: str = ""
    backup: ResolvedAI | None = None


def secret_key_ready() -> bool:
    try:
        crypto.require_key()
        return True
    except crypto.SecretKeyError:
        return False


def cli_detected() -> dict[str, bool]:
    return {
        "claude": shutil.which("claude") is not None,
        "codex": shutil.which("codex") is not None,
    }


def _env_provider() -> ProviderName | None:
    explicit = settings.ai_provider.strip().lower()
    if explicit in PROVIDERS:
        if explicit == "anthropic" and not settings.ai_api_key.strip():
            return None
        if explicit in ("ollama", "openai-compatible") and not settings.ai_url.strip():
            return None
        return explicit  # type: ignore[return-value]
    if settings.ai_api_key.strip():
        return "anthropic"
    if settings.ai_url.strip():
        return "ollama"
    return None


def _decrypt_optional(blob: Any, *, aad: str = KEY) -> str:
    if not isinstance(blob, str) or not blob:
        return ""
    try:
        return crypto.decrypt(blob, aad=aad)
    except crypto.SecretKeyError:
        return ""


def _decrypt_json(blob: Any) -> dict[str, Any] | None:
    raw = _decrypt_optional(blob)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def _backup_from_raw(raw: Any, oauth: dict[str, Any] | None) -> ResolvedAI | None:
    if not isinstance(raw, dict):
        return None
    prov = raw.get("provider")
    provider: ProviderName | None = prov if prov in PROVIDERS else None
    enabled = bool(raw.get("enabled", bool(provider)))
    model = str(raw.get("model") or "").strip()
    if not model and provider:
        model = DEFAULT_MODELS.get(provider, "")
    url = str(raw.get("url") or "").strip()
    api_key = _decrypt_optional(raw.get("api_key_enc"), aad=f"{KEY}:backup")
    return ResolvedAI(
        enabled=enabled,
        provider=provider,
        model=model,
        url=url,
        api_key=api_key,
        oauth=oauth if provider == "chatgpt-oauth" else None,
        source="db",
    )


def _from_raw(data: dict[str, Any]) -> ResolvedAI:
    prov = data.get("provider")
    provider: ProviderName | None = prov if prov in PROVIDERS else None
    enabled = bool(data.get("enabled", True))
    model = str(data.get("model") or "").strip()
    if not model and provider:
        model = DEFAULT_MODELS.get(provider, "")
    url = str(data.get("url") or "").strip()
    api_key = _decrypt_optional(data.get("api_key_enc"))
    oauth = _decrypt_json(data.get("oauth_enc"))
    host_id = str(data.get("host_id") or "")
    backup = _backup_from_raw(data.get("backup"), oauth)
    return ResolvedAI(
        enabled=enabled,
        provider=provider,
        model=model,
        url=url,
        api_key=api_key,
        oauth=oauth,
        source="db",
        host_id=host_id,
        backup=backup,
    )


def resolve(session: Session | None = None) -> ResolvedAI:
    """Return effective AI configuration from DB (if saved) else environment settings."""
    row = session.get(WorkspaceSetting, KEY) if session is not None else None
    if row is not None and isinstance(row.value_json, dict):
        return _from_raw(row.value_json)
    prov = _env_provider()
    if prov is None:
        return ResolvedAI(
            enabled=False,
            provider=None,
            model="",
            url="",
            api_key="",
            oauth=None,
            source="none",
        )
    model = settings.ai_model.strip() or DEFAULT_MODELS.get(prov, "")
    return ResolvedAI(
        enabled=True,
        provider=prov,
        model=model,
        url=settings.ai_url.strip(),
        api_key=settings.ai_api_key.strip(),
        oauth=None,
        source="env",
    )


def slot_active(cfg: ResolvedAI | None) -> bool:
    """Whether one provider slot is enabled and has the credentials/endpoint required to run."""
    if cfg is None or not cfg.enabled or cfg.provider is None:
        return False
    if cfg.provider in ("anthropic", "openai", "gemini"):
        return bool(cfg.api_key)
    if cfg.provider in ("ollama", "openai-compatible"):
        return bool(cfg.url)
    if cfg.provider == "chatgpt-oauth":
        return bool(cfg.oauth and (cfg.oauth.get("access_token") or cfg.oauth.get("refresh_token")))
    if cfg.provider == "claude-cli":
        return bool(cfg.url) or shutil.which("claude") is not None
    if cfg.provider == "codex-cli":
        return bool(cfg.url) or shutil.which("codex") is not None
    return False


def is_active(cfg: ResolvedAI) -> bool:
    """Whether AI add is enabled and has at least a primary or backup slot ready."""
    if not cfg.enabled:
        return False
    return slot_active(cfg) or slot_active(cfg.backup)


def _redacted_backup(cfg: ResolvedAI | None) -> dict[str, Any]:
    if cfg is None:
        return {
            "enabled": False,
            "active": False,
            "provider": None,
            "model": "",
            "url": "",
            "has_api_key": False,
        }
    return {
        "enabled": cfg.enabled,
        "active": slot_active(cfg),
        "provider": cfg.provider,
        "model": cfg.model,
        "url": cfg.url,
        "has_api_key": bool(cfg.api_key),
    }


def _redacted(cfg: ResolvedAI) -> dict[str, Any]:
    oauth = cfg.oauth or {}
    return {
        "enabled": cfg.enabled,
        "active": is_active(cfg),
        "provider": cfg.provider,
        "model": cfg.model,
        "url": cfg.url,
        "has_api_key": bool(cfg.api_key),
        "oauth_connected": bool(oauth.get("access_token") or oauth.get("refresh_token")),
        "oauth_account": oauth.get("email") or None,
        "backup": _redacted_backup(cfg.backup),
    }


def view(session: Session) -> dict[str, Any]:
    """Admin-safe view of AI settings (never exposes plaintext credentials)."""
    cfg = resolve(session)
    return {
        **_redacted(cfg),
        "source": cfg.source,
        "cli_detected": cli_detected(),
        "secret_key_ready": secret_key_ready(),
        "defaults": dict(DEFAULT_MODELS),
    }


def _require_admin_person(session: Session, actor: Actor) -> None:
    access.require_admin(session, "change AI settings")
    from slate.models import Account  # noqa: PLC0415

    acct = session.get(Account, actor.account_id)
    if acct is not None and acct.kind == AccountKind.agent:
        raise Forbidden("only a person with admin access may change AI settings")


def _load_raw(session: Session) -> dict[str, Any]:
    row = session.get(WorkspaceSetting, KEY)
    if row is not None and isinstance(row.value_json, dict):
        return dict(row.value_json)
    # Seed from env when transitioning from .env to DB-managed settings.
    env = resolve(None)
    raw: dict[str, Any] = {
        "enabled": env.enabled,
        "provider": env.provider,
        "model": env.model,
        "url": env.url,
        "host_id": str(uuid.uuid4()),
    }
    if env.api_key and secret_key_ready():
        raw["api_key_enc"] = crypto.encrypt(env.api_key, aad=KEY)
    return raw


def save(session: Session, actor: Actor, changes: dict[str, Any]) -> dict[str, Any]:
    """Update workspace AI settings, encrypting any API key with SLATE_SECRET_KEY."""
    _require_admin_person(session, actor)
    before = _redacted(resolve(session))
    raw = _load_raw(session)
    if not raw.get("host_id"):
        raw["host_id"] = str(uuid.uuid4())

    if "enabled" in changes and changes["enabled"] is not None:
        raw["enabled"] = bool(changes["enabled"])
    if "provider" in changes:
        prov = changes["provider"]
        if prov is not None and prov not in PROVIDERS:
            raise Invalid(f"provider must be one of {', '.join(PROVIDERS)}")
        raw["provider"] = prov
    if "model" in changes and changes["model"] is not None:
        raw["model"] = str(changes["model"]).strip()[:200]
    if "url" in changes and changes["url"] is not None:
        url = str(changes["url"]).strip()[:500]
        if url and not re.match(r"^https?://", url, re.I):
            raise Invalid("url must start with http:// or https://")
        raw["url"] = url
    if changes.get("clear_api_key"):
        raw.pop("api_key_enc", None)
    elif "api_key" in changes and changes["api_key"]:
        try:
            raw["api_key_enc"] = crypto.encrypt(str(changes["api_key"]).strip(), aad=KEY)
        except crypto.SecretKeyError as exc:
            raise Invalid(
                "SLATE_SECRET_KEY must be set on the server to save encrypted API keys in Settings"
            ) from exc

    if "backup" in changes:
        b_in = changes["backup"]
        if b_in is None:
            raw.pop("backup", None)
        elif isinstance(b_in, dict):
            b_raw = dict(raw.get("backup") or {}) if isinstance(raw.get("backup"), dict) else {}
            if "enabled" in b_in and b_in["enabled"] is not None:
                b_raw["enabled"] = bool(b_in["enabled"])
            if "provider" in b_in:
                b_prov = b_in["provider"]
                if b_prov is not None and b_prov not in PROVIDERS:
                    raise Invalid(f"backup provider must be one of {', '.join(PROVIDERS)}")
                b_raw["provider"] = b_prov
                if b_prov is None:
                    b_raw["enabled"] = False
            if "model" in b_in and b_in["model"] is not None:
                b_raw["model"] = str(b_in["model"]).strip()[:200]
            if "url" in b_in and b_in["url"] is not None:
                b_url = str(b_in["url"]).strip()[:500]
                if b_url and not re.match(r"^https?://", b_url, re.I):
                    raise Invalid("backup url must start with http:// or https://")
                b_raw["url"] = b_url
            if b_in.get("clear_api_key"):
                b_raw.pop("api_key_enc", None)
            elif "api_key" in b_in and b_in["api_key"]:
                try:
                    b_raw["api_key_enc"] = crypto.encrypt(
                        str(b_in["api_key"]).strip(), aad=f"{KEY}:backup"
                    )
                except crypto.SecretKeyError as exc:
                    raise Invalid(
                        "SLATE_SECRET_KEY must be set on the server to save encrypted API keys"
                    ) from exc
            raw["backup"] = b_raw

    with apply_change(session, actor) as cx:
        row = session.get(WorkspaceSetting, KEY)
        if row is None:
            row = WorkspaceSetting(key=KEY)
            session.add(row)
        row.value_json = raw
        after = _redacted(_from_raw(raw))
        cx.record("settings.changed", field=KEY, before=before, after=after, covers=[row])
    return view(session)


# --- Sign in with ChatGPT (OpenAI OAuth 2.0 + PKCE dynamic_agent_client) --------------------

_PENDING_LOCK = threading.Lock()
_PENDING_OAUTH: dict[str, tuple[str, str, float]] = {}  # state -> (verifier, host_id, created_at)
_OAUTH_TTL = 900.0
_MAX_PENDING = 32


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _jwt_email(id_token: str | None) -> str | None:
    if not id_token or id_token.count(".") < 2:
        return None
    try:
        part = id_token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        email = payload.get("email")
        return str(email) if isinstance(email, str) and email else None
    except Exception:  # noqa: BLE001
        return None


def oauth_start(session: Session, actor: Actor) -> dict[str, str]:
    """Start Sign in with ChatGPT (OAuth 2.0 PKCE) and return the authorize URL."""
    _require_admin_person(session, actor)
    if not secret_key_ready():
        raise Invalid("SLATE_SECRET_KEY must be set on the server to store OAuth credentials")
    raw = _load_raw(session)
    host_id = str(raw.get("host_id") or uuid.uuid4())
    existing_oauth = _decrypt_json(raw.get("oauth_enc")) or {}
    client_id = str(existing_oauth.get("client_id") or "dynamic_agent_client")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    now = time.monotonic()
    with _PENDING_LOCK:
        for k in [k for k, v in _PENDING_OAUTH.items() if now - v[2] > _OAUTH_TTL]:
            _PENDING_OAUTH.pop(k, None)
        while len(_PENDING_OAUTH) >= _MAX_PENDING:
            oldest = min(_PENDING_OAUTH, key=lambda k: _PENDING_OAUTH[k][2])
            _PENDING_OAUTH.pop(oldest, None)
        _PENDING_OAUTH[state] = (verifier, host_id, now)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": OPENAI_OAUTH_REDIRECT,
        "scope": OPENAI_OAUTH_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": f"{OPENAI_URL}/v1",
        "agent_name_hint": "Slate",
        "ext_agent_host_id": host_id,
    }
    authorize_url = f"{OPENAI_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {
        "authorize_url": authorize_url,
        "state": state,
        "redirect_uri": OPENAI_OAUTH_REDIRECT,
    }


def _extract_code_and_state(raw_input: str, fallback_state: str | None) -> tuple[str, str]:
    text = raw_input.strip()
    if not text:
        raise Invalid("paste the redirect URL or authorization code from the browser")
    if text.startswith(("http://", "https://", "?")):
        parsed = urllib.parse.urlparse(text)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("error"):
            desc = (qs.get("error_description") or qs["error"])[0]
            raise Invalid(f"ChatGPT sign-in was refused: {desc}")
        code = (qs.get("code") or [""])[0].strip()
        state = (qs.get("state") or [fallback_state or ""])[0].strip()
    else:
        code = text
        state = (fallback_state or "").strip()
    if not code:
        raise Invalid("could not find an authorization code in the pasted URL")
    if not state:
        raise Invalid("missing OAuth state; start sign-in again")
    return code, state


def oauth_complete(
    session: Session, actor: Actor, *, callback: str, state: str | None = None
) -> dict[str, Any]:
    """Complete Sign in with ChatGPT by exchanging the pasted code/URL for tokens."""
    _require_admin_person(session, actor)
    if not secret_key_ready():
        raise Invalid("SLATE_SECRET_KEY must be set on the server to store OAuth credentials")
    code, st = _extract_code_and_state(callback, state)
    with _PENDING_LOCK:
        entry = _PENDING_OAUTH.pop(st, None)
    if entry is None or time.monotonic() - entry[2] > _OAUTH_TTL:
        raise Invalid("sign-in session expired or unknown; click Sign in with ChatGPT again")
    verifier, host_id, _ = entry

    raw = _load_raw(session)
    existing_oauth = _decrypt_json(raw.get("oauth_enc")) or {}
    client_id = str(existing_oauth.get("client_id") or "dynamic_agent_client")

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OPENAI_OAUTH_REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": f"{OPENAI_URL}/v1",
    }
    try:
        with httpx.Client(transport=transport, timeout=20.0, follow_redirects=False) as client:
            r = client.post(OPENAI_TOKEN_URL, data=form)
            r.raise_for_status()
            tok = r.json()
    except Exception as exc:  # noqa: BLE001
        raise Invalid(f"could not complete ChatGPT sign-in: {exc}") from exc

    access_token = str(tok.get("access_token") or "").strip()
    if not access_token:
        raise Invalid("OpenAI token response did not include an access token")
    issued_client_id = str(tok.get("client_id") or client_id)
    expires_in = int(tok.get("expires_in") or 3600)
    oauth_blob = {
        "client_id": issued_client_id,
        "access_token": access_token,
        "refresh_token": str(tok.get("refresh_token") or existing_oauth.get("refresh_token") or ""),
        "expires_at": int(time.time()) + max(60, expires_in - 60),
        "email": _jwt_email(tok.get("id_token")) or existing_oauth.get("email"),
    }

    before = _redacted(resolve(session))
    raw["host_id"] = host_id
    raw["enabled"] = True
    raw["provider"] = "chatgpt-oauth"
    if not raw.get("model"):
        raw["model"] = DEFAULT_MODELS["chatgpt-oauth"]
    raw["oauth_enc"] = crypto.encrypt(json.dumps(oauth_blob), aad=KEY)

    with apply_change(session, actor) as cx:
        row = session.get(WorkspaceSetting, KEY)
        if row is None:
            row = WorkspaceSetting(key=KEY)
            session.add(row)
        row.value_json = raw
        after = _redacted(_from_raw(raw))
        cx.record("settings.changed", field=KEY, before=before, after=after, covers=[row])
    return view(session)


def oauth_disconnect(session: Session, actor: Actor) -> dict[str, Any]:
    """Remove stored ChatGPT OAuth credentials."""
    _require_admin_person(session, actor)
    before = _redacted(resolve(session))
    raw = _load_raw(session)
    raw.pop("oauth_enc", None)
    with apply_change(session, actor) as cx:
        row = session.get(WorkspaceSetting, KEY)
        if row is None:
            row = WorkspaceSetting(key=KEY)
            session.add(row)
        row.value_json = raw
        after = _redacted(_from_raw(raw))
        cx.record("settings.changed", field=KEY, before=before, after=after, covers=[row])
    return view(session)


def refresh_chatgpt_token(oauth: dict[str, Any]) -> dict[str, Any]:
    """Refresh an expired ChatGPT OAuth token in memory (caller persists if desired)."""
    refresh_token = str(oauth.get("refresh_token") or "").strip()
    if not refresh_token:
        return oauth
    expires_at = int(oauth.get("expires_at") or 0)
    if oauth.get("access_token") and time.time() < expires_at:
        return oauth
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": str(oauth.get("client_id") or "dynamic_agent_client"),
        "resource": f"{OPENAI_URL}/v1",
    }
    with httpx.Client(transport=transport, timeout=20.0, follow_redirects=False) as client:
        r = client.post(OPENAI_TOKEN_URL, data=form)
        r.raise_for_status()
        tok = r.json()
    return {
        **oauth,
        "access_token": str(tok.get("access_token") or oauth.get("access_token") or ""),
        "refresh_token": str(tok.get("refresh_token") or refresh_token),
        "expires_at": int(time.time()) + max(60, int(tok.get("expires_in") or 3600) - 60),
    }


# --- Live model discovery -------------------------------------------------------------------


def _read_claude_cli_models() -> list[dict[str, str]]:
    """Read real versioned Claude models from ~/.claude.json (picker cache + model catalog)."""
    cfg = Path.home() / ".claude.json"
    if not cfg.is_file():
        return []
    try:
        raw = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    # 1. Exact versioned models referenced in ~/.claude.json (newest first per family).
    found: dict[str, tuple[int, int, int, str]] = {}
    fam_rank = {"sonnet": 0, "opus": 1, "haiku": 2}
    for m in re.finditer(
        r"\b(claude-(sonnet|opus|haiku)-(\d+)(?:-(\d+))?(?:-\d{8})?)\b", raw, re.I
    ):
        mid = m.group(1).lower()
        # Skip dated snapshots when the clean alias exists; keep clean versioned IDs first.
        if re.search(r"-\d{8}$", mid):
            continue
        fam = m.group(2).lower()
        major = int(m.group(3))
        minor = int(m.group(4) or 0)
        ver_label = f"{major}.{minor}" if m.group(4) else str(major)
        label = f"Claude {fam.title()} {ver_label} ({mid})"
        found[mid] = (major, minor, -fam_rank[fam], label)

    for mid, info in sorted(found.items(), key=lambda kv: kv[1][:3], reverse=True):
        seen.add(mid)
        out.append({"id": mid, "label": info[3]})

    # 2. Additional picker options from Claude's own `additionalModelOptionsCache`.
    try:
        data = json.loads(raw)
        for opt in data.get("additionalModelOptionsCache") or []:
            if not isinstance(opt, dict):
                continue
            val = str(opt.get("value") or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            desc = str(opt.get("description") or "").split("·")[0].strip()
            lbl = desc or str(opt.get("label") or val).strip()
            out.append({"id": val, "label": f"{lbl} ({val})"})
    except ValueError:
        pass

    return out


def _read_codex_cli_models() -> list[dict[str, str]]:
    cache = Path.home() / ".codex" / "models_cache.json"
    if not cache.is_file():
        return [{"id": "", "label": "Default (codex)"}]
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        out = [{"id": "", "label": "Default (codex)"}]
        seen: set[str] = set()
        for m in data.get("models") or []:
            slug = str(m.get("slug") or "").strip()
            if not slug or slug in seen or m.get("visibility") == "hide":
                continue
            seen.add(slug)
            out.append({"id": slug, "label": str(m.get("display_name") or slug).strip()})
        return out
    except Exception:  # noqa: BLE001
        return [{"id": "", "label": "Default (codex)"}]


def discover_models(
    session: Session,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    url: str | None = None,
    slot: Literal["primary", "backup"] = "primary",
) -> dict[str, Any]:
    """Query the selected provider for its live model list."""
    saved = resolve(session)
    cfg = saved.backup if slot == "backup" and saved.backup is not None else saved
    prov = (provider or cfg.provider or "").strip()
    key = (api_key or "").strip() or (cfg.api_key if prov == cfg.provider else "")
    base = (url or "").strip() or (cfg.url if prov == cfg.provider else "")

    if prov not in PROVIDERS:
        return {"models": [], "error": "Select a provider first."}

    try:
        if prov == "claude-cli":
            if base:
                with httpx.Client(transport=transport, timeout=8.0) as client:
                    r = client.get(base.rstrip("/") + "/models", params={"cli": "claude"})
                    r.raise_for_status()
                    return {"models": r.json().get("models") or _read_claude_cli_models()}
            if shutil.which("claude") is None:
                return {
                    "models": _read_claude_cli_models(),
                    "error": "claude CLI not found on server PATH (set Bridge URL for Docker).",
                }
            return {"models": _read_claude_cli_models()}

        if prov == "codex-cli":
            if base:
                with httpx.Client(transport=transport, timeout=8.0) as client:
                    r = client.get(base.rstrip("/") + "/models", params={"cli": "codex"})
                    r.raise_for_status()
                    return {"models": r.json().get("models") or _read_codex_cli_models()}
            if shutil.which("codex") is None:
                return {
                    "models": _read_codex_cli_models(),
                    "error": "codex binary not found on server PATH (set Bridge URL for Docker).",
                }
            return {"models": _read_codex_cli_models()}

        if prov == "anthropic":
            if not key:
                return {"models": [], "error": "Enter an Anthropic API key to load models."}
            endpoint = (base or ANTHROPIC_URL).rstrip("/") + "/v1/models?limit=50"
            with httpx.Client(transport=transport, timeout=10.0) as client:
                r = client.get(
                    endpoint, headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
                )
                r.raise_for_status()
                items = r.json().get("data") or []
            return {
                "models": [
                    {"id": m["id"], "label": m.get("display_name") or m["id"]}
                    for m in items
                    if isinstance(m, dict) and m.get("id")
                ]
            }

        if prov in ("openai", "chatgpt-oauth"):
            token = key
            if prov == "chatgpt-oauth":
                oauth = saved.oauth
                if not oauth:
                    return {"models": [], "error": "Sign in with ChatGPT first to load models."}
                oauth = refresh_chatgpt_token(oauth)
                token = str(oauth.get("access_token") or "")
            if not token:
                return {"models": [], "error": "Enter an OpenAI API key to load models."}
            endpoint = (base or OPENAI_URL).rstrip("/") + "/v1/models"
            with httpx.Client(transport=transport, timeout=10.0) as client:
                r = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                items = r.json().get("data") or []
            filtered = [
                m
                for m in items
                if isinstance(m, dict)
                and isinstance(m.get("id"), str)
                and re.match(r"^(gpt-|o[1345]-|o[1345]$|chatgpt-)", m["id"], re.I)
                and not re.search(
                    r"audio|realtime|transcribe|tts|image|search|embed", m["id"], re.I
                )
            ]
            filtered.sort(key=lambda m: int(m.get("created") or 0), reverse=True)
            return {"models": [{"id": m["id"], "label": m["id"]} for m in filtered[:40]]}

        if prov == "gemini":
            if not key:
                return {"models": [], "error": "Enter a Google Gemini API key to load models."}
            endpoint = (base or GEMINI_URL).rstrip("/") + "/v1beta/models"
            with httpx.Client(transport=transport, timeout=10.0) as client:
                r = client.get(endpoint, params={"key": key, "pageSize": 50})
                r.raise_for_status()
                items = r.json().get("models") or []
            models = []
            for m in items:
                if not isinstance(m, dict):
                    continue
                methods = m.get("supportedGenerationMethods") or []
                if "generateContent" not in methods:
                    continue
                mid = str(m.get("name") or "").removeprefix("models/")
                if mid:
                    models.append({"id": mid, "label": str(m.get("displayName") or mid)})
            return {"models": models[:40]}

        if prov == "ollama":
            if not base:
                return {"models": [], "error": "Enter the Ollama URL first."}
            with httpx.Client(transport=transport, timeout=8.0) as client:
                r = client.get(base.rstrip("/") + "/api/tags")
                r.raise_for_status()
                items = r.json().get("models") or []
            return {
                "models": [
                    {"id": m["name"], "label": m["name"]}
                    for m in items
                    if isinstance(m, dict) and m.get("name")
                ]
            }

        if prov == "openai-compatible":
            if not base:
                return {"models": [], "error": "Enter the server base URL first."}
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            endpoint = base.rstrip("/")
            if not endpoint.endswith("/v1"):
                endpoint += "/v1"
            with httpx.Client(transport=transport, timeout=8.0) as client:
                r = client.get(endpoint + "/models", headers=headers)
                r.raise_for_status()
                items = r.json().get("data") or []
            return {
                "models": [
                    {"id": m["id"], "label": m["id"]}
                    for m in items
                    if isinstance(m, dict) and m.get("id")
                ][:50]
            }
    except Exception as exc:  # noqa: BLE001
        return {"models": [], "error": str(exc)}
    return {"models": []}
