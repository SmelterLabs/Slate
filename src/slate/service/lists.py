"""Lists and their statuses. A list's statuses are its board columns, in the owner's words.

A new list gets `To do -> In progress -> Done` unless statuses are supplied. `set_statuses()`
replaces the whole set in one change; items on a removed status move to a named fallback.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.ids import new_id, slugify
from slate.models import (
    Account,
    AccountKind,
    AgentState,
    Item,
    List,
    Section,
    Status,
    StatusKind,
    utcnow,
)
from slate.service import sections
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Invalid, NotFound

DEFAULT_STATUSES: tuple[dict[str, str], ...] = (
    {"name": "To do", "kind": "open"},
    {"name": "In progress", "kind": "active"},
    {"name": "Done", "kind": "done"},
)
CLOSED_KINDS = frozenset({StatusKind.done, StatusKind.cancelled})


def _name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise Invalid("name is required")
    return name


def _check_slug_free(
    session: Session, section_id: str, slug: str, exclude_id: str | None = None
) -> None:
    q = select(List.id).where(List.section_id == section_id, List.slug == slug)
    if exclude_id:
        q = q.where(List.id != exclude_id)
    if session.scalar(q):
        raise Conflict(f"this section already has a list with slug '{slug}'")


def _clean_statuses(specs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not specs:
        raise Invalid("a list needs at least one status")
    out, seen = [], set()
    for spec in specs:
        name = _name(spec.get("name", ""))
        if name.lower() in seen:
            raise Invalid(f"status '{name}' appears twice")
        seen.add(name.lower())
        try:
            kind = StatusKind(spec.get("kind") or "open")
        except ValueError:
            raise Invalid(f"status kind must be one of {[k.value for k in StatusKind]}") from None
        out.append({"id": spec.get("id"), "name": name, "kind": kind, "color": spec.get("color")})
    return out


def _status_view(st: Status) -> dict[str, Any]:
    return {"id": st.id, "name": st.name, "kind": st.kind.value, "color": st.color}


# --- Reads ---------------------------------------------------------------------------------


def get_list(session: Session, list_id: str) -> List:
    lst = session.get(List, list_id)
    if lst is None:
        raise NotFound(f"list {list_id} not found")
    return lst


def list_lists(
    session: Session, *, section_id: str | None = None, include_archived: bool = False
) -> list[List]:
    q = select(List).join(Section, Section.id == List.section_id)
    if section_id is not None:
        q = q.where(List.section_id == section_id)
    if not include_archived:
        q = q.where(List.archived_at.is_(None), Section.archived_at.is_(None))
    return list(session.scalars(q.order_by(Section.position, List.position, List.name)))


def statuses_of(session: Session, list_id: str) -> list[Status]:
    return list(
        session.scalars(select(Status).where(Status.list_id == list_id).order_by(Status.position))
    )


def resolve_status(session: Session, list_id: str, ref: str) -> Status:
    """A status of this list by id, or by name (case-insensitive)."""
    for st in statuses_of(session, list_id):
        if st.id == ref or st.name.lower() == ref.strip().lower():
            return st
    raise Invalid(f"list {list_id} has no status '{ref}'")


# --- Writes --------------------------------------------------------------------------------


def _set_completion(cx: Change, item: Item, status: Status) -> None:
    """Entering a done status stamps completed_at; leaving done clears it."""
    if status.kind == StatusKind.done:
        if item.completed_at is None:
            cx.set(item, "completed_at", utcnow(), "item.updated")
    elif item.completed_at is not None:
        cx.set(item, "completed_at", None, "item.updated")


def move_item_to_status(cx: Change, item: Item, status: Status) -> None:
    """Shared by items.update and set_statuses: status change plus completed_at bookkeeping."""
    if cx.set(item, "status_id", status.id, "item.status_changed"):
        _set_completion(cx, item, status)
        if (
            status.kind in (StatusKind.done, StatusKind.cancelled)
            and item.waiting_on_account_id is not None
        ):
            # A closed task waits on nobody; it would linger in Today's "waiting on me".
            cx.set(item, "waiting_on_account_id", None, "item.updated")
        if status.kind == StatusKind.done and item.recurrence_id:
            from slate.service import recurrences  # recurrences imports this module

            recurrences.on_item_done(cx, item)  # "recur when done" makes the next one
        _settle_agent_flag(cx, item, closed=status.kind in (StatusKind.done, StatusKind.cancelled))


def _settle_agent_flag(cx: Change, item: Item, *, closed: bool) -> None:
    """A person moving a card has seen the agent's answer: "Ready for review" clears. Closing
    the card (done or cancelled) clears a review or blocked flag whoever closes it, an agent
    included: a closed card needs nobody, and a flag left on it would show on the Agents page
    as work waiting on a person. A working flag stays (the run is live), and an agent's own
    move that does not close is left to its report."""
    state = item.agent_state
    if state is None or state == AgentState.working:
        return
    if state == AgentState.blocked and not closed:
        return
    mover = cx.session.get(Account, cx.actor.account_id)
    if mover is None or (mover.kind == AccountKind.agent and not closed):
        return
    from slate.service.reports import clear_flag  # reports imports items, which imports this

    clear_flag(cx, item)


LIST_KINDS = ("tasks", "drawer")


def require_tasks(lst: List) -> List:
    """Items live in task lists only; a drawer holds documents."""
    if lst.kind == "drawer":
        raise Invalid(f"'{lst.name}' is a drawer: it holds documents, not tasks")
    return lst


def _kind(value: Any) -> str:
    kind = str(value or "tasks")
    if kind not in LIST_KINDS:
        raise Invalid(f"kind must be one of {', '.join(LIST_KINDS)}")
    return kind


def create_list(
    session: Session,
    actor: Actor,
    *,
    section_id: str,
    name: str,
    position: int | None = None,
    statuses: Sequence[Mapping[str, Any]] | None = None,
    kind: str = "tasks",
) -> List:
    """`statuses`, or the default set. `kind = drawer` makes a list of documents (it still
    gets statuses, unused for now)."""
    kind = _kind(kind)
    section = sections.get_section(session, section_id)
    name = _name(name)
    slug = slugify(name)
    _check_slug_free(session, section.id, slug)
    specs = _clean_statuses(statuses if statuses is not None else DEFAULT_STATUSES)
    if position is None:
        position = len(list_lists(session, section_id=section.id, include_archived=True))
    with apply_change(session, actor) as cx:
        lst = cx.create(
            List(section_id=section.id, name=name, slug=slug, position=position, kind=kind),
            "list.created",
        )
        # The request's access snapshot predates this list. Authorised section editors
        # inherit reach immediately, including statuses and later operations in a batch.
        from dataclasses import replace

        from slate.service import access

        caller = access.current(session)
        if caller and caller.lists is not None:
            access.attach(
                session,
                replace(caller, lists={**caller.lists, lst.id: caller.section_level(section.id)}),
            )
        rows = [
            Status(
                id=new_id(),
                list_id=lst.id,
                name=s["name"],
                kind=s["kind"],
                color=s["color"],
                position=i,
            )
            for i, s in enumerate(specs)
        ]
        for row in rows:
            session.add(row)
        cx.record(
            "list.statuses_changed",
            subject_id=lst.id,
            field="statuses",
            before=[],
            after=[_status_view(r) for r in rows],
            covers=rows,
        )
        session.flush()
        cx.set(lst, "default_status_id", rows[0].id, "list.updated")
    return lst


def update_list(
    session: Session,
    actor: Actor,
    list_id: str,
    *,
    name: str | None = None,
    section_id: str | None = None,
    position: int | None = None,
    default_status: str | None = None,
    archived: bool | None = None,
    kind: str | None = None,
) -> List:
    """Change any of name (slug follows), section, position, default status, archived, kind,
    in one change. Moving to another section puts the list at its end unless `position` is
    given; the slug must be free there. Items, statuses and rules move with the list. A task
    list becomes a drawer only when it holds no tasks (archived ones included); a drawer
    becomes a task list at any time, its documents stay where they are."""
    lst = get_list(session, list_id)
    if kind is not None:
        kind = _kind(kind)
        if kind == "drawer" and lst.kind != "drawer":
            from slate.models import Item

            held = session.scalar(
                select(func.count()).select_from(Item).where(Item.list_id == lst.id)
            )
            if held:
                raise Invalid(
                    f"'{lst.name}' holds {held} task(s); move or purge them before making it a "
                    "drawer"
                )
    target = lst.section_id
    if section_id is not None and section_id != lst.section_id:
        target = sections.get_section(session, section_id).id
        if position is None:
            position = len(list_lists(session, section_id=target, include_archived=True))
    with apply_change(session, actor) as cx:
        if kind is not None and kind != lst.kind:
            cx.set(lst, "kind", kind, "list.updated")
        if target != lst.section_id:
            _check_slug_free(session, target, slugify(name) if name else lst.slug, lst.id)
            cx.set(lst, "section_id", target, "list.updated")
        if name is not None:
            name = _name(name)
            slug = slugify(name)
            _check_slug_free(session, target, slug, exclude_id=lst.id)
            cx.set(lst, "name", name, "list.updated")
            cx.set(lst, "slug", slug, "list.updated")
        if position is not None:
            cx.set(lst, "position", position, "list.updated")
        if default_status is not None:
            status = resolve_status(session, lst.id, default_status)
            cx.set(lst, "default_status_id", status.id, "list.updated")
        if archived is not None:
            stamp = utcnow() if archived else None
            if (lst.archived_at is None) == archived:
                cx.set(
                    lst, "archived_at", stamp, "list.archived" if archived else "list.unarchived"
                )
    return lst


def archive_list(session: Session, actor: Actor, list_id: str) -> List:
    """Soft: hides the list and its items from default queries."""
    lst = get_list(session, list_id)
    if lst.archived_at is None:
        with apply_change(session, actor) as cx:
            cx.set(lst, "archived_at", utcnow(), "list.archived")
    return lst


def unarchive_list(session: Session, actor: Actor, list_id: str) -> List:
    lst = get_list(session, list_id)
    if lst.archived_at is not None:
        with apply_change(session, actor) as cx:
            cx.set(lst, "archived_at", None, "list.unarchived")
    return lst


def set_statuses(
    session: Session,
    actor: Actor,
    list_id: str,
    statuses: Sequence[Mapping[str, Any]],
    *,
    fallback: str | None = None,
) -> list[Status]:
    """Replace the list's statuses with `statuses` (in order), in one change.

    Each spec is `{name, kind, color?, id?}`. A spec keeps an existing status when its `id`
    matches, or else when its name matches (case-insensitive); otherwise it is new. Existing
    statuses not kept are removed. Items on a removed status move to `fallback` (a name or id
    in the new set); if any item would be stranded and no fallback is given, nothing changes
    and `Invalid` is raised. The list's default status survives if kept, else becomes the first.
    """
    lst = get_list(session, list_id)
    specs = _clean_statuses(statuses)
    current = statuses_of(session, lst.id)
    by_id = {s.id: s for s in current}
    by_name = {s.name.lower(): s for s in current}

    kept: list[tuple[Status | None, dict[str, Any]]] = []
    for spec in specs:
        if spec["id"] is not None and spec["id"] not in by_id:
            raise Invalid(f"status {spec['id']} does not belong to list {lst.id}")
        match = by_id.get(spec["id"]) or by_name.get(spec["name"].lower())
        if match is not None and any(match is k for k, _ in kept):
            raise Invalid(f"status '{match.name}' is matched twice")
        kept.append((match, spec))
    kept_ids = {m.id for m, _ in kept if m is not None}
    removed = [s for s in current if s.id not in kept_ids]

    stranded = list(
        session.scalars(select(Item).where(Item.status_id.in_([s.id for s in removed])))
    )
    fallback_spec = None
    if fallback is not None:
        key = fallback.strip().lower()
        fallback_spec = next(
            (
                sp
                for m, sp in kept
                if (m is not None and m.id == fallback) or sp["name"].lower() == key
            ),
            None,
        )
        if fallback_spec is None:
            raise Invalid(f"fallback '{fallback}' is not one of the new statuses")
    if stranded and fallback_spec is None:
        names = sorted({s.name for s in removed if any(i.status_id == s.id for i in stranded)})
        raise Invalid(
            f"{len(stranded)} item(s) are on status(es) being removed ({', '.join(names)}); "
            "name a fallback status to move them to"
        )

    before = [_status_view(s) for s in current]
    with apply_change(session, actor) as cx:
        rows: list[Status] = []
        crossed: list[Status] = []  # kept statuses whose kind moved into or out of done
        for position, (match, spec) in enumerate(kept):
            row = match or Status(id=new_id(), list_id=lst.id)
            if match is not None and (match.kind == StatusKind.done) != (
                spec["kind"] == StatusKind.done
            ):
                crossed.append(match)
            row.name, row.kind, row.color, row.position = (
                spec["name"],
                spec["kind"],
                spec["color"],
                position,
            )
            if match is None:
                session.add(row)
            rows.append(row)
        cx.record(
            "list.statuses_changed",
            subject_id=lst.id,
            field="statuses",
            before=before,
            after=[_status_view(r) for r in rows],
            covers=[*rows, *removed],
        )
        session.flush()

        target = None
        if fallback_spec is not None:
            target = next(r for r, (_, sp) in zip(rows, kept, strict=True) if sp is fallback_spec)
        for item in stranded:
            move_item_to_status(cx, item, target)
        for status in crossed:
            for item in session.scalars(select(Item).where(Item.status_id == status.id)):
                _set_completion(cx, item, status)
        if lst.default_status_id not in {r.id for r in rows}:
            cx.set(lst, "default_status_id", rows[0].id, "list.updated")
        session.flush()
        for st in removed:
            session.delete(st)
    return rows
