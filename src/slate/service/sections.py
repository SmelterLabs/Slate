"""Sections: the top-level separations (for example Personal and Work). One per audience or
rhythm."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.ids import slugify
from slate.models import Audience, Section, utcnow
from slate.service.core import Actor, apply_change
from slate.service.errors import Conflict, Invalid, NotFound


def _audience(value: str) -> Audience:
    try:
        return Audience(value)
    except ValueError:
        raise Invalid(f"audience must be one of {[a.value for a in Audience]}") from None


def _name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise Invalid("name is required")
    return name


def _check_slug_free(session: Session, slug: str, exclude_id: str | None = None) -> None:
    q = select(Section.id).where(Section.slug == slug)
    if exclude_id:
        q = q.where(Section.id != exclude_id)
    if session.scalar(q):
        raise Conflict(f"a section with slug '{slug}' already exists")


# --- Reads ---------------------------------------------------------------------------------


def get_section(session: Session, section_id: str) -> Section:
    section = session.get(Section, section_id)
    if section is None:
        raise NotFound(f"section {section_id} not found")
    return section


def list_sections(session: Session, *, include_archived: bool = False) -> list[Section]:
    q = select(Section).order_by(Section.position, Section.name)
    if not include_archived:
        q = q.where(Section.archived_at.is_(None))
    return list(session.scalars(q))


# --- Writes --------------------------------------------------------------------------------


def create_section(
    session: Session, actor: Actor, *, name: str, audience: str, position: int | None = None
) -> Section:
    name = _name(name)
    slug = slugify(name)
    _check_slug_free(session, slug)
    if position is None:
        position = len(list_sections(session, include_archived=True))
    with apply_change(session, actor) as cx:
        return cx.create(
            Section(name=name, slug=slug, audience=_audience(audience), position=position),
            "section.created",
        )


def update_section(
    session: Session,
    actor: Actor,
    section_id: str,
    *,
    name: str | None = None,
    position: int | None = None,
    audience: str | None = None,
    archived: bool | None = None,
    whiteboard_url: str | None = None,
) -> Section:
    """Change name (slug follows), position, audience, archive or HTTPS whiteboard link.

    Unchanged fields record nothing.
    """
    section = get_section(session, section_id)
    with apply_change(session, actor) as cx:
        if whiteboard_url is not None:
            from slate.service.collaboration import whiteboard_url as validate_url

            cx.set(section, "whiteboard_url", validate_url(whiteboard_url), "section.updated")
        if name is not None:
            name = _name(name)
            slug = slugify(name)
            _check_slug_free(session, slug, exclude_id=section.id)
            cx.set(section, "name", name, "section.updated")
            cx.set(section, "slug", slug, "section.updated")
        if position is not None:
            cx.set(section, "position", position, "section.updated")
        if audience is not None:
            cx.set(section, "audience", _audience(audience), "section.updated")
        if archived is not None:
            stamp = utcnow() if archived else None
            if (section.archived_at is None) == archived:
                cx.set(
                    section,
                    "archived_at",
                    stamp,
                    "section.archived" if archived else "section.unarchived",
                )
    return section


def archive_section(session: Session, actor: Actor, section_id: str) -> Section:
    """Soft: hides the section, its lists and their items from default queries."""
    section = get_section(session, section_id)
    if section.archived_at is None:
        with apply_change(session, actor) as cx:
            cx.set(section, "archived_at", utcnow(), "section.archived")
    return section


def unarchive_section(session: Session, actor: Actor, section_id: str) -> Section:
    section = get_section(session, section_id)
    if section.archived_at is not None:
        with apply_change(session, actor) as cx:
            cx.set(section, "archived_at", None, "section.unarchived")
    return section
