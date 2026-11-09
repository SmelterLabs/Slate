"""/sections: create, update (incl. archive), list."""

from __future__ import annotations

from fastapi import APIRouter, status

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import SectionIn, SectionOut, SectionPatch
from slate.models import Section
from slate.service import permissions, sections

router = APIRouter(tags=["sections"])


@router.get("/sections", response_model=list[SectionOut])
def list_sections(_: ReadAuth, session: DbSession, include_archived: bool = False) -> list[Section]:
    return sections.list_sections(session, include_archived=include_archived)


@router.post("/sections", status_code=status.HTTP_201_CREATED, response_model=SectionOut)
def create_section(body: SectionIn, auth: WriteAuth, session: DbSession) -> Section:
    return sections.create_section(
        session, auth.actor, name=body.name, audience=body.audience, position=body.position
    )


@router.patch("/sections/{section_id}", response_model=SectionOut)
def update_section(
    section_id: str, body: SectionPatch, auth: WriteAuth, session: DbSession
) -> Section:
    """Rename, reorder, change audience, archive (`archived: true`) / restore a section, or
    make it private (owner/admin; `private: true`)."""
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
            return section
    return sections.update_section(session, auth.actor, section_id, **changes)
