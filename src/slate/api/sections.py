"""/sections and /projects: create, update (incl. archive and project flag), list."""

from __future__ import annotations

from fastapi import APIRouter, status

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import SectionIn, SectionOut, SectionPatch
from slate.service import permissions, sections, today
from slate.service.core import apply_change

router = APIRouter(tags=["sections"])


@router.get("/sections", response_model=list[SectionOut])
def list_sections(auth: ReadAuth, session: DbSession, include_archived: bool = False) -> list[dict]:
    found = sections.list_sections(session, include_archived=include_archived)
    return sections.section_views(session, found, lead_days=today.lead_days_for(auth.account))


@router.get("/projects", response_model=list[SectionOut])
def list_projects(auth: ReadAuth, session: DbSession) -> list[dict]:
    """Every active project section with its computed `next_step`, the ones with none first."""
    return sections.list_projects(session, lead_days=today.lead_days_for(auth.account))


@router.post("/sections", status_code=status.HTTP_201_CREATED, response_model=SectionOut)
def create_section(body: SectionIn, auth: WriteAuth, session: DbSession) -> dict:
    section = sections.create_section(session, auth.actor, **body.model_dump())
    return sections.section_views(session, [section], lead_days=today.lead_days_for(auth.account))[
        0
    ]


@router.patch("/sections/{section_id}", response_model=SectionOut)
def update_section(
    section_id: str, body: SectionPatch, auth: WriteAuth, session: DbSession
) -> dict:
    """Rename, reorder, change audience or colour, toggle `project`, archive or restore a section,
    or make it private (owner/admin)."""
    changes = body.model_dump(exclude_unset=True)
    private = changes.pop("private", None)
    if "color" in changes and changes["color"] is None:
        changes["color"] = ""
    with apply_change(session, auth.actor):
        if private is not None:
            section = permissions.set_private(
                session,
                auth.actor,
                by=auth.principal,
                scope_kind="section",
                scope_id=section_id,
                private=private,
            )
        if changes or private is None:
            section = sections.update_section(session, auth.actor, section_id, **changes)
    return sections.section_views(session, [section], lead_days=today.lead_days_for(auth.account))[
        0
    ]
