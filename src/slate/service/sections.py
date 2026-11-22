"""Sections: the top-level separations (for example Personal and Work). One per audience or
rhythm. A section marked `project` exposes a computed `next_step`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slate.ids import slugify
from slate.models import (
    Account,
    Audience,
    Item,
    ItemAssignee,
    List,
    Section,
    Status,
    StatusKind,
    utcnow,
)
from slate.service import today as today_svc
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


def next_step_for(
    session: Session,
    section_ids: Sequence[str],
    *,
    now: datetime | None = None,
    lead_days: int = today_svc.DEFAULT_LEAD_DAYS,
) -> dict[str, dict[str, Any] | None]:
    """Compute each project section's `next_step`: among its open, non-later, non-parked,
    unblocked cards (not in a reference list) with an assignee and a `due_at`, the one with the
    earliest `due_at` (ties: priority, then position)."""
    out: dict[str, dict[str, Any] | None] = {sid: None for sid in section_ids}
    if not section_ids:
        return out
    from slate.service import access

    caller = access.current(session)
    now = now or utcnow()
    has_assignee = select(ItemAssignee.item_id).where(ItemAssignee.item_id == Item.id).exists()
    with access.unrestricted(session):
        rows = session.execute(
            select(List.section_id, Item)
            .join(List, List.id == Item.list_id)
            .join(Section, Section.id == List.section_id)
            .outerjoin(Status, Status.id == Item.status_id)
            .where(
                List.section_id.in_(list(section_ids)),
                Item.archived_at.is_(None),
                Item.parked_until.is_(None),
                Item.blocked_since.is_(None),
                Item.due_at.is_not(None),
                List.archived_at.is_(None),
                Section.archived_at.is_(None),
                *today_svc.non_reference_list_filter(),
                or_(
                    Status.kind.is_(None),
                    Status.kind.not_in([StatusKind.done, StatusKind.cancelled]),
                ),
                has_assignee,
            )
        ).all()

    # items imports this module through lists, so the rank table is read here, not at import.
    from slate.service.items import PRIORITY_RANK

    def _key(item: Item) -> tuple[Any, ...]:
        rank = PRIORITY_RANK[item.priority] if item.priority else len(PRIORITY_RANK)
        return (item.due_at, rank, item.position, item.id)

    best: dict[str, Item] = {}
    for sid, item in rows:
        if today_svc.is_later(item, now=now, lead_days=lead_days):
            continue
        cur = best.get(sid)
        if cur is None or _key(item) < _key(cur):
            best[sid] = item

    if caller is not None and caller.lists is not None:
        best = {sid: item for sid, item in best.items() if caller.list_level(item.list_id) > 0}

    if not best:
        return out
    winner_ids = [item.id for item in best.values()]
    assignees: dict[str, list[str]] = {wid: [] for wid in winner_ids}
    for item_id, account_id in session.execute(
        select(ItemAssignee.item_id, ItemAssignee.account_id)
        .join(Account, Account.id == ItemAssignee.account_id)
        .where(ItemAssignee.item_id.in_(winner_ids))
        .order_by(Account.handle)
    ):
        assignees[item_id].append(account_id)
    for sid, item in best.items():
        out[sid] = {
            "item_id": item.id,
            "title": item.title,
            "assignee_ids": assignees[item.id],
            "due_at": item.due_at,
        }
    return out


def section_views(
    session: Session,
    secs: Sequence[Section],
    *,
    now: datetime | None = None,
    lead_days: int = today_svc.DEFAULT_LEAD_DAYS,
) -> list[dict[str, Any]]:
    """Sections as dicts carrying `project` and computed `next_step`."""
    project_ids = [s.id for s in secs if s.project]
    steps = next_step_for(session, project_ids, now=now, lead_days=lead_days)
    return [
        {
            "id": s.id,
            "name": s.name,
            "slug": s.slug,
            "position": s.position,
            "audience": s.audience,
            "created_at": s.created_at,
            "archived_at": s.archived_at,
            "private": bool(s.private),
            "project": bool(s.project),
            "next_step": steps.get(s.id) if s.project else None,
        }
        for s in secs
    ]


def list_projects(
    session: Session,
    *,
    now: datetime | None = None,
    lead_days: int = today_svc.DEFAULT_LEAD_DAYS,
) -> list[dict[str, Any]]:
    """Every active project section with its `next_step`, the ones with none first."""
    secs = list(
        session.scalars(
            select(Section)
            .where(Section.archived_at.is_(None), Section.project.is_(True))
            .order_by(Section.position, Section.name)
        )
    )
    views = section_views(session, secs, now=now, lead_days=lead_days)
    views.sort(key=lambda v: (v["next_step"] is not None, v["position"], v["name"]))
    return views


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
    project: bool | None = None,
) -> Section:
    """Change name (slug follows), position, audience, archive, project flag or whiteboard link.

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
        if project is not None:
            cx.set(section, "project", bool(project), "section.updated")
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
