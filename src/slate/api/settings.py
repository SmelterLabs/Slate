"""Workspace settings: what agent accounts may never do (service/agent_rules.py).

GET /settings/agents   the deny-list, whether it is still the default, and the vocabulary (admin)
PUT /settings/agents   replace the deny-list (owner or admin person; never an agent)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import AdminAuth, DbSession
from slate.service import agent_rules

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
