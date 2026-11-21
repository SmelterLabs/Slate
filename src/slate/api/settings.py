"""Workspace settings for agent restrictions and default filing."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import AdminAuth, DbSession
from slate.service import agent_rules, lists

router = APIRouter(tags=["settings"])

ActionKey = Literal[tuple(agent_rules.ACTIONS)]  # type: ignore[valid-type]


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


@router.get("/settings/inbox", response_model=InboxListSetting)
def get_inbox_list(_: AdminAuth, session: DbSession) -> InboxListSetting:
    """Read the workspace's default filing list."""
    return InboxListSetting(inbox_list_id=lists.inbox_list_id(session))


@router.put("/settings/inbox", response_model=InboxListSetting)
def set_inbox_list(body: InboxListSetting, auth: AdminAuth, session: DbSession) -> InboxListSetting:
    """Set or clear the workspace's default filing list (admin)."""
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
