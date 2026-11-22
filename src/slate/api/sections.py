"""/sections and /projects: create, update (incl. archive and project flag), list."""

from __future__ import annotations

from fastapi import APIRouter, status

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import SectionIn, SectionOut, SectionPatch
from slate.service import permissions, sections, today

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
    section = sections.create_section(
        session, auth.actor, name=body.name, audience=body.audience, position=body.position
    )
    return sections.section_views(session, [section], lead_days=today.lead_days_for(auth.account))[
        0
    ]


@router.patch("/sections/{section_id}", response_model=SectionOut)
def update_section(
    section_id: str, body: SectionPatch, auth: WriteAuth, session: DbSession
) -> dict:
    """Rename, reorder, change audience, toggle `project`, archive (`archived: true`) / restore
    a section, or make it private (owner/admin; `private: true`)."""
    changes = body.model_dump(exclude_unset=True)
    private = changes.pop("private", None)
    if private is not None:
        section = permissions.set_private(
            session,
            auth.actor,
            by=auth.principal,
            scope_kind="section",
            scope_id=section_id,
            private=private,
        )
        if not changes:
            return sections.section_views(
                session, [section], lead_days=today.lead_days_for(auth.account)
            )[0]
    section = sections.update_section(session, auth.actor, section_id, **changes)
    return sections.section_views(session, [section], lead_days=today.lead_days_for(auth.account))[
        0
    ]
