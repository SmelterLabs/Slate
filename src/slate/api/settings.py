"""Workspace settings for agent restrictions, default filing, and AI task creation."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from slate.api.auth import AdminAuth, DbSession
from slate.db import release
from slate.service import agent_rules, ai_settings, aiadd, lists

router = APIRouter(tags=["settings"])

ActionKey = Literal[tuple(agent_rules.ACTIONS)]  # type: ignore[valid-type]
AiProviderKey = Literal[tuple(ai_settings.PROVIDERS)]  # type: ignore[valid-type]


class AgentAction(BaseModel):
    key: ActionKey
    label: str = Field(description="What it covers, read after 'Agents may not …'")


class AgentRulesOut(BaseModel):
    denied: list[ActionKey] = Field(description="What agent accounts may not do, whatever token")
    default: bool = Field(description="True until someone saves a list: the default applies")
    actions: list[AgentAction] = Field(description="Every key the list can name")


class AgentRulesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    denied: list[ActionKey] = Field(
        description="The full list to keep (replaces it); [] lets agents do everything"
    )


class InboxListSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inbox_list_id: str | None = Field(
        description="Inbox list id, or null to require a list when filing"
    )


class AiSettingsOut(BaseModel):
    enabled: bool
    active: bool = Field(description="True when enabled and the provider has credentials/endpoint")
    provider: AiProviderKey | None
    model: str
    url: str
    has_api_key: bool
    oauth_connected: bool
    oauth_account: str | None
    source: Literal["db", "env", "none"]
    cli_detected: dict[str, bool]
    secret_key_ready: bool
    defaults: dict[str, str]


class AiSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    provider: AiProviderKey | None = None
    model: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool | None = None


class AiModelsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiProviderKey | None = None
    url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class AiModelOption(BaseModel):
    id: str
    label: str


class AiModelsOut(BaseModel):
    models: list[AiModelOption]
    error: str | None = None


class AiTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiProviderKey | None = None
    model: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class AiTestOut(BaseModel):
    ok: bool
    provider: str | None
    model: str
    latency_ms: int
    detail: str


class OAuthStartOut(BaseModel):
    authorize_url: str
    state: str
    redirect_uri: str


class OAuthCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback: str = Field(min_length=1, max_length=4000)
    state: str | None = Field(default=None, max_length=200)


@router.get("/settings/inbox", response_model=InboxListSetting)
def get_inbox_list(_: AdminAuth, session: DbSession) -> InboxListSetting:
    """Read the workspace's default filing list."""
    return InboxListSetting(inbox_list_id=lists.inbox_list_id(session))


@router.put("/settings/inbox", response_model=InboxListSetting)
def set_inbox_list(body: InboxListSetting, auth: AdminAuth, session: DbSession) -> InboxListSetting:
    """Set or clear the workspace's default filing list (admin). Choose an active work list
    you can file in."""
    lists.set_inbox_list(session, auth.actor, body.inbox_list_id)
    return body


@router.get("/settings/agents", response_model=AgentRulesOut)
def get_agent_rules(_: AdminAuth, session: DbSession) -> Any:
    """What agent accounts may never do in this workspace, whatever their token or role. With
    nothing saved, agents may not delete for good, archive sections or lists, share, or issue
    tokens. People, owners and admins included, are never limited by this."""
    return agent_rules.view(session)


@router.put("/settings/agents", response_model=AgentRulesOut)
def set_agent_rules(body: AgentRulesIn, auth: AdminAuth, session: DbSession) -> Any:
    """Replace what agent accounts may never do (`denied`, a list of action keys). Owner or
    admin people only: an agent may not change this, even on an admin token. Recorded as a
    `settings.changed` event."""
    return agent_rules.set_denied(session, auth.actor, list(body.denied))


@router.get("/settings/ai", response_model=AiSettingsOut)
def get_ai_settings(_: AdminAuth, session: DbSession) -> Any:
    """Read workspace AI task creation settings (credentials are never returned)."""
    return ai_settings.view(session)


@router.put("/settings/ai", response_model=AiSettingsOut)
def set_ai_settings(body: AiSettingsIn, auth: AdminAuth, session: DbSession) -> Any:
    """Save workspace AI provider, model, URL, and encrypted API key (admin people only)."""
    return ai_settings.save(session, auth.actor, body.model_dump(exclude_unset=True))


@router.post("/settings/ai/models", response_model=AiModelsOut)
async def list_ai_models(body: AiModelsIn, _: AdminAuth, session: DbSession) -> Any:
    """Probe the selected AI provider and return its live available models."""
    release(session)
    return await run_in_threadpool(
        ai_settings.discover_models,
        session,
        provider=body.provider,
        api_key=body.api_key,
        url=body.url,
    )


@router.post("/settings/ai/test", response_model=AiTestOut)
async def test_ai_settings(body: AiTestIn, _: AdminAuth, session: DbSession) -> Any:
    """Run a quick live round-trip against the configured or draft AI provider."""
    release(session)
    return await run_in_threadpool(
        aiadd.test_connection,
        session,
        provider_name=body.provider,
        model=body.model,
        url=body.url,
        api_key=body.api_key,
    )


@router.post("/settings/ai/oauth/start", response_model=OAuthStartOut)
def start_ai_oauth(auth: AdminAuth, session: DbSession) -> Any:
    """Start Sign in with ChatGPT (OpenAI OAuth 2.0 + PKCE) and return the authorize URL."""
    return ai_settings.oauth_start(session, auth.actor)


@router.post("/settings/ai/oauth/complete", response_model=AiSettingsOut)
async def complete_ai_oauth(body: OAuthCompleteIn, auth: AdminAuth, session: DbSession) -> Any:
    """Complete Sign in with ChatGPT by exchanging the pasted callback URL or code."""
    return await run_in_threadpool(
        ai_settings.oauth_complete, session, auth.actor, callback=body.callback, state=body.state
    )


@router.post("/settings/ai/oauth/disconnect", response_model=AiSettingsOut)
def disconnect_ai_oauth(auth: AdminAuth, session: DbSession) -> Any:
    """Disconnect and delete stored ChatGPT OAuth credentials."""
    return ai_settings.oauth_disconnect(session, auth.actor)
