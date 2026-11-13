"""Items: tasks (and, later, docs). The heart of Slate.

Rules held here (AGENTS.md rule 1): `create_item()` makes exactly one item; `parent_id` must
name an item that has no parent (depth <= 1). `split()` is the only way to make several
subtasks at once. Every body change writes a `body_versions` row. Status changes into a `done`
status stamp `completed_at`; leaving done clears it. Archive is soft. Hard delete is an undo:
only the creator, only within 10 minutes, and it still leaves an `item.deleted` event.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, type_coerce
from sqlalchemy.orm import Session, aliased

from slate.dates import is_date_only_input, parse_when
from slate.db import contains
from slate.ids import new_id
from slate.models import (
    Account,
    AgentThread,
    Attachment,
    BodyVersion,
    ChecklistItem,
    Comment,
    Event,
    Item,
    ItemAssignee,
    ItemKind,
    ItemTag,
    List,
    Priority,
    Section,
    Status,
    StatusKind,
    Tag,
    UTCDateTime,
    utcnow,
)
from slate.service import access, guests
from slate.service import links as links_svc
from slate.service import lists as lists_svc
from slate.service import tags as tags_svc
from slate.service import trash as trash_svc
from slate.service.core import Actor, Change, apply_change, event_payload
from slate.service.errors import ExternalRefTaken, Invalid, NotFound

UNDO_WINDOW = timedelta(minutes=10)
STALE_PUSHES = 3
STALE_UNTOUCHED = timedelta(days=30)
RECENT_EVENTS = 20

# Fields `create_item`/`update_item` accept, beyond the structural ones.
UPDATABLE = frozenset(
    {
        "title",
        "body_md",
        "body_note",
        "status",
        "priority",
        "start_at",
        "due_at",
        "due_date_only",
        "waiting_on_account_id",
        "acceptance_md",
        "position",
        "kind",
        "external_ref",
        "assignees",
        "tags",
        "estimate_minutes",
    }
)

MAX_ESTIMATE = 60 * 24 * 365


def _due_date_only(fields: Mapping[str, Any]) -> bool:
    """`due_date_only` for a write that sets `due_at`: a bare YYYY-MM-DD means date-only; an
    internal caller (recurrences) passes `due_date_only` itself."""
    if fields.get("due_at") is None:
        return False
    if fields.get("due_date_only") is not None:
        return bool(fields["due_date_only"])
    return is_date_only_input(fields["due_at"])


PRIORITY_RANK = {Priority.urgent: 0, Priority.high: 1, Priority.normal: 2, Priority.low: 3}


# --- Validation helpers --------------------------------------------------------------------


def _title(value: str | None) -> str:
    title = (value or "").strip()
    if not title:
        raise Invalid("title is required")
    if len(title) > 500:
        raise Invalid("title is longer than 500 characters")
    return title


def _priority(value: str | None) -> Priority | None:
    if value is None:
        return None
    try:
        return Priority(value)
    except ValueError:
        raise Invalid(f"priority must be one of {[p.value for p in Priority]} or null") from None


def _estimate(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_ESTIMATE:
        raise Invalid("estimate_minutes must be a whole number of minutes (0 to a year) or null")
    return value or None


def _external_ref(value: Any) -> str | None:
    """A blank reference is no reference (the unique index ignores nulls, not empty strings)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return str(value)


def _claim_ref(session: Session, ref: str | None, item_id: str | None = None) -> None:
    """Raise `ExternalRefTaken` if another item (archived and trashed ones too) carries `ref`.

    Called inside the change scope, so the write lock makes check-then-write race free; the
    unique index `ux_items_external_ref` is the backstop."""
    if ref is None:
        return
    from slate.service import access  # access imports models only; keep the import local

    with access.unrestricted(session):
        stmt = (
            select(Item.id, Item.trashed_at, Item.archived_at, Status.kind)
            .outerjoin(Status, Status.id == Item.status_id)
            .where(Item.external_ref == ref)
        )
        if item_id is not None:
            stmt = stmt.where(Item.id != item_id)
        row = session.execute(stmt.limit(1)).first()
    if row is None:
        return
    existing, trashed_at, archived_at, kind = row
    if trashed_at is not None:
        state = "trashed"
    elif archived_at is not None:
        state = "archived"
    elif kind in (StatusKind.done, StatusKind.cancelled):
        state = "closed"
    else:
        state = "open"
    raise ExternalRefTaken(ref, existing, state)


def _kind(value: str | None) -> ItemKind:
    try:
        return ItemKind(value or "task")
    except ValueError:
        raise Invalid(f"kind must be one of {[k.value for k in ItemKind]}") from None


def resolve_account(session: Session, ref: str) -> Account:
    """An account by id or handle. A restricted caller (a guest, a list-limited token) only
    resolves the participants of what it can see (`access.participant_ids`); anyone else
    answers exactly like an unknown handle, so a reference cannot enumerate accounts."""
    account = session.get(Account, ref) or session.scalar(
        select(Account).where(Account.handle == ref.strip().lower())
    )
    if account is not None:
        reach = access.participant_ids(session)
        if reach is not None and account.id not in reach:
            account = None
    if account is None:
        raise Invalid(f"no account '{ref}'")
    return account


# --- Reads ---------------------------------------------------------------------------------


def get_item(session: Session, item_id: str) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise NotFound(f"item {item_id} not found")
    return item


def views(session: Session, items: Sequence[Item]) -> list[dict[str, Any]]:
    """Items as plain dicts with status, assignees, tags, subtask and comment counts batched."""
    if not items:
        return []
    ids = [i.id for i in items]
    statuses = {
        s.id: s
        for s in session.scalars(
            select(Status).where(Status.id.in_({i.status_id for i in items if i.status_id}))
        )
    }
    assignees: dict[str, list[Account]] = defaultdict(list)
    for item_id, account in session.execute(
        select(ItemAssignee.item_id, Account)
        .join(Account, Account.id == ItemAssignee.account_id)
        .where(ItemAssignee.item_id.in_(ids))
        .order_by(Account.handle)
    ):
        assignees[item_id].append(account)
    tag_names: dict[str, list[str]] = defaultdict(list)
    for item_id, name in session.execute(
        select(ItemTag.item_id, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id.in_(ids))
        .order_by(Tag.name)
    ):
        tag_names[item_id].append(name)
    subtasks = dict(
        session.execute(
            select(Item.parent_id, func.count())
            .where(Item.parent_id.in_(ids), Item.archived_at.is_(None))
            .group_by(Item.parent_id)
        ).all()
    )
    links = links_svc.links_of(session, ids)
    threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in session.scalars(
        select(AgentThread)
        .where(AgentThread.item_id.in_(ids))
        .order_by(AgentThread.updated_at, AgentThread.account_id)
    ):
        threads[t.item_id].append({"account_id": t.account_id, "ref": t.ref, "url": t.url})
    comments = dict(
        session.execute(
            select(Comment.item_id, func.count())
            .where(Comment.item_id.in_(ids))
            .group_by(Comment.item_id)
        ).all()
    )
    asks: dict[str, list[str]] = {}
    for item_id, assignee_id in session.execute(
        select(Comment.item_id, Comment.assignee_id)
        .where(
            Comment.item_id.in_(ids),
            Comment.assignee_id.is_not(None),
            Comment.resolved_at.is_(None),
        )
        .order_by(Comment.created_at, Comment.id)
    ):
        asks.setdefault(item_id, []).append(assignee_id)

    checklist = {
        item_id: (done, total)
        for item_id, done, total in session.execute(
            select(ChecklistItem.item_id, func.count(ChecklistItem.done_at), func.count())
            .where(ChecklistItem.item_id.in_(ids))
            .group_by(ChecklistItem.item_id)
        )
    }
    attachments = dict(
        session.execute(
            select(Attachment.item_id, func.count())
            .where(Attachment.item_id.in_(ids))
            .group_by(Attachment.item_id)
        ).all()
    )

    # A project's guest sees no trace of a workspace agent (service/guests.py): not its badge,
    # its session link, a wait on it or its name as the card's maker.
    hide = guests.workspace_agent_ids(session) if guests.viewer_is_guest(session) else set()
    out = []
    for item in items:
        st = statuses.get(item.status_id)
        out.append(
            {
                "id": item.id,
                "list_id": item.list_id,
                "parent_id": item.parent_id,
                "kind": item.kind.value,
                "title": item.title,
                "body_md": item.body_md,
                "status": (
                    {"id": st.id, "name": st.name, "kind": st.kind.value, "color": st.color}
                    if st
                    else None
                ),
                "priority": item.priority.value if item.priority else None,
                "start_at": item.start_at,
                "due_at": item.due_at,
                "due_date_only": item.due_date_only,
                "completed_at": item.completed_at,
                "waiting_on_account_id": item.waiting_on_account_id,
                "acceptance_md": item.acceptance_md,
                "recurrence_id": item.recurrence_id,
                "occurrence_at": item.occurrence_at,
                "external_ref": item.external_ref,
                "position": item.position,
                "created_by": item.created_by,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "archived_at": item.archived_at,
                "trashed_at": item.trashed_at,
                "purge_at": trash_svc.purge_due_at(item),
                "assignees": [
                    {"id": a.id, "handle": a.handle, "display_name": a.display_name, "kind": a.kind}
                    for a in assignees[item.id]
                ],
                "tags": tag_names[item.id],
                "subtask_count": subtasks.get(item.id, 0),
                "estimate_minutes": item.estimate_minutes,
                "blocked_by": links[item.id]["blocked_by"],
                "blocking": links[item.id]["blocking"],
                "related": links[item.id]["related"],
                "duplicate_of": links[item.id]["duplicate_of"],
                "duplicated_by": links[item.id]["duplicated_by"],
                "blocked": bool(links[item.id]["open_blockers"]),
                "comment_count": comments.get(item.id, 0),
                "open_asks": asks.get(item.id, []),
                "checklist_done": checklist.get(item.id, (0, 0))[0],
                "checklist_total": checklist.get(item.id, (0, 0))[1],
                "attachment_count": attachments.get(item.id, 0),
                "agent": (
                    {
                        "state": item.agent_state.value,
                        "by": item.agent_state_by,
                        "at": item.agent_state_at,
                        "note": item.agent_note,
                        "progress": item.agent_progress,
                        "url": item.agent_url,
                    }
                    if item.agent_state
                    else None
                ),
                "agent_threads": threads.get(item.id, []),
                "run_requested_at": item.run_requested_at,
            }
        )
        if hide:
            view = out[-1]
            if view["agent"] and view["agent"]["by"] in hide:
                view["agent"] = None
            view["agent_threads"] = [
                t for t in view["agent_threads"] if t["account_id"] not in hide
            ]
            if view["waiting_on_account_id"] in hide:
                view["waiting_on_account_id"] = None
            if view["created_by"] in hide:
                view["created_by"] = None
    return out


def item_events(session: Session, item_id: str, *, limit: int | None = None) -> list[Event]:
    """The item's history in order. With `limit`, the most recent `limit` events."""
    get_item(session, item_id)
    q = select(Event).where(Event.item_id == item_id).order_by(Event.id.desc())
    if limit is not None:
        q = q.limit(limit)
    return list(reversed(list(session.scalars(q))))


def body_versions(session: Session, item_id: str) -> list[BodyVersion]:
    get_item(session, item_id)
    return list(
        session.scalars(
            select(BodyVersion)
            .where(BodyVersion.item_id == item_id)
            .order_by(BodyVersion.version_no)
        )
    )


def comments_of(session: Session, item_id: str) -> list[Comment]:
    return list(
        session.scalars(
            select(Comment).where(Comment.item_id == item_id).order_by(Comment.created_at)
        )
    )


def subtasks_of(session: Session, item_id: str, *, include_archived: bool = False) -> list[Item]:
    """Direct subtasks, in order. Their own subtasks are theirs (`descendants_of` for all)."""
    q = select(Item).where(Item.parent_id == item_id)
    if not include_archived:
        q = q.where(Item.archived_at.is_(None))
    return list(session.scalars(q.order_by(Item.position, Item.created_at)))


# Subtasks nest four deep under a top task. A top task has depth 0, its subtasks 1, down to
# 4. The `items_depth_*` triggers hold the same rule.
MAX_DEPTH = 4


def depth_of(session: Session, item: Item) -> int:
    """How many parents above `item`: 0 for a top task, up to MAX_DEPTH."""
    depth = 0
    seen = {item.id}
    cur = item
    while cur.parent_id is not None and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        cur = session.get(Item, cur.parent_id)
        if cur is None:
            break
        depth += 1
    return depth


def descendant_levels(session: Session, item_id: str) -> list[list[Item]]:
    """The subtasks under `item_id` level by level: direct subtasks first, then theirs."""
    levels: list[list[Item]] = []
    frontier = [item_id]
    while frontier:
        rows = list(session.scalars(select(Item).where(Item.parent_id.in_(frontier))))
        if rows:
            levels.append(rows)
        frontier = [r.id for r in rows]
    return levels


def descendants_of(session: Session, item_id: str, *, include_archived: bool = True) -> list[Item]:
    """Every subtask under `item_id` at any depth, parents before children."""
    out = [row for level in descendant_levels(session, item_id) for row in level]
    return out if include_archived else [r for r in out if r.archived_at is None]


def item_detail(session: Session, item_id: str) -> dict[str, Any]:
    """One item with subtasks, comments (with reactions), checklists, attachments, watchers and
    its last 20 events."""
    # These modules import this one; importing them at call time keeps the graph acyclic.
    from slate.service import attachments, checklists, comments, watchers

    item = get_item(session, item_id)
    detail = views(session, [item])[0]
    detail["subtasks"] = views(session, subtasks_of(session, item.id))
    detail["comments"] = comments.comment_views(session, comments_of(session, item.id))
    detail["checklists"] = checklists.checklists_of(session, item.id)
    detail["attachments"] = attachments.attachments_of(session, item.id)
    detail["watchers"] = [
        {"id": a.id, "handle": a.handle, "display_name": a.display_name, "kind": a.kind}
        for a in watchers.watchers_of(session, item.id)
    ]
    detail["events"] = [
        event_payload(e) for e in item_events(session, item.id, limit=RECENT_EVENTS)
    ]
    return detail


_ITEM_STATUS = aliased(Status, name="item_status")  # built once; aliasing is not free


def query_items(
    session: Session,
    *,
    list_id: str | None = None,
    section_id: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    tag: str | None = None,
    external_ref: str | None = None,
    due_before: str | datetime | None = None,
    due_after: str | datetime | None = None,
    q: str | None = None,
    kind: str | None = None,
    include_archived: bool = False,
    archived: bool | None = None,
    trashed: bool = False,
    closed: bool | None = None,
    order: str = "position",
    stale: bool = False,
    blocked: bool | None = None,
    now: datetime | None = None,
    changed_since: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    ids: Sequence[str] | None = None,
    only_ids: bool = False,
    open_comment_for: str | None = None,
) -> list[Item] | list[str]:
    """Items matching every given filter (AND). See TECHNICAL-GUIDE "Querying items".

    - `status`: a status id, or a status name (case-insensitive) in any list.
    - `assignee`: account handle or id. `tag`: tag name (any case) or id.
    - `external_ref`: exact match on the filer's own reference (unique; at most one item).
      Archived and trashed items follow the usual rules below.
    - `due_before`/`due_after`: exclusive bounds; accept ISO 8601 or date-only (17:00
      workspace time).
    - `q`: case-insensitive substring of title or body.
    - Archived items, and items in archived lists or sections, are hidden unless
      `include_archived`.
    - `stale`: not done/cancelled, and either its due date was moved >= 3 times (item.updated
      events on due_at with a previous value), or it sits in an `open`-kind status with no
      event for >= 30 days.
    - `blocked`: true = has an open blocker (dependency); false = has none.
    - `changed_since`: an event id; only items with an event after it (created, changed,
      commented, archived...). Every change is an event, so this is a complete delta.
    - `archived`: true = only archived items (not in the trash); false = only unarchived.
    - `trashed`: only items in the trash (archived or not otherwise). Without it, trashed items
      are never returned, even with `include_archived`.
    - `closed`: true = only done/cancelled; false = only open/active (or no status).
    - `order`: position (default), completed, archived, trashed, updated, created (newest first
      for all but position).
    - `limit`/`offset`: a page of the ordered result.
    - `ids`: only these items. `only_ids`: return the ids, not the rows (the cached render).
    - `open_comment_for`: a handle or id; only items with an open Ask (assigned comment)
      addressed to that account.
    Unknown handles, tags or status names match nothing rather than erroring.
    """
    now = now or utcnow()
    st = _ITEM_STATUS
    stmt = (
        select(Item)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(st, st.id == Item.status_id)
    )
    if ids is not None:
        stmt = stmt.where(Item.id.in_(ids))
    if list_id is not None:
        stmt = stmt.where(Item.list_id == list_id)
    if section_id is not None:
        stmt = stmt.where(List.section_id == section_id)
    if status is not None:
        stmt = stmt.where(or_(st.id == status, func.lower(st.name) == status.strip().lower()))
    if assignee is not None:
        account_ids = select(Account.id).where(
            or_(Account.id == assignee, Account.handle == assignee.strip().lower())
        )
        stmt = stmt.where(
            Item.id.in_(
                select(ItemAssignee.item_id).where(ItemAssignee.account_id.in_(account_ids))
            )
        )
    if open_comment_for is not None:
        asked = select(Account.id).where(
            or_(
                Account.id == open_comment_for,
                Account.handle == open_comment_for.strip().lower(),
            )
        )
        stmt = stmt.where(
            Item.id.in_(
                select(Comment.item_id).where(
                    Comment.assignee_id.in_(asked), Comment.resolved_at.is_(None)
                )
            )
        )
    if external_ref is not None:
        stmt = stmt.where(Item.external_ref == external_ref)
    if tag is not None:
        tag_ids = select(Tag.id).where(or_(Tag.id == tag, Tag.name == tag.strip().lower()))
        stmt = stmt.where(Item.id.in_(select(ItemTag.item_id).where(ItemTag.tag_id.in_(tag_ids))))
    if due_before is not None:
        stmt = stmt.where(Item.due_at < parse_when(due_before))
    if due_after is not None:
        stmt = stmt.where(Item.due_at > parse_when(due_after))
    if q:
        stmt = stmt.where(or_(contains(Item.title, q.strip()), contains(Item.body_md, q.strip())))
    if kind is not None:
        stmt = stmt.where(Item.kind == _kind(kind))
    if blocked is not None:
        blocked_ids = links_svc.blocked_item_ids()
        stmt = stmt.where(Item.id.in_(blocked_ids) if blocked else Item.id.not_in(blocked_ids))
    if trashed:
        stmt = stmt.where(Item.trashed_at.is_not(None))
    else:
        stmt = stmt.where(Item.trashed_at.is_(None))
        if archived is True:
            stmt = stmt.where(Item.archived_at.is_not(None))
        elif archived is False:
            stmt = stmt.where(Item.archived_at.is_(None))
        if not include_archived and archived is not True:
            stmt = stmt.where(
                Item.archived_at.is_(None),
                List.archived_at.is_(None),
                Section.archived_at.is_(None),
            )
    if closed is not None:
        done = st.kind.in_([StatusKind.done, StatusKind.cancelled])
        stmt = stmt.where(done if closed else or_(st.kind.is_(None), ~done))
    if stale:
        not_closed = or_(st.kind.is_(None), st.kind.not_in([StatusKind.done, StatusKind.cancelled]))
        pushes = (
            select(func.count())
            .where(
                Event.item_id == Item.id,
                Event.type == "item.updated",
                Event.field == "due_at",
                func.coalesce(func.json_type(Event.before_json), "null") != "null",
            )
            .scalar_subquery()
        )
        last_touch = type_coerce(
            func.coalesce(
                select(func.max(Event.at)).where(Event.item_id == Item.id).scalar_subquery(),
                Item.created_at,
            ),
            UTCDateTime,
        )
        untouched_open = and_(
            or_(st.kind.is_(None), st.kind == StatusKind.open),
            last_touch <= now - STALE_UNTOUCHED,
        )
        stmt = stmt.where(not_closed, or_(pushes >= STALE_PUSHES, untouched_open))
    if changed_since is not None and changed_since != "0":
        stmt = stmt.where(
            Item.id.in_(
                select(Event.item_id).where(Event.id > changed_since, Event.item_id.is_not(None))
            )
        )
    newest = {
        "completed": Item.completed_at,
        "archived": Item.archived_at,
        "trashed": Item.trashed_at,
        "updated": Item.updated_at,
        "created": Item.created_at,
    }
    if order == "position":
        stmt = stmt.order_by(Item.position, Item.created_at, Item.id)
    elif order in newest:
        stmt = stmt.order_by(newest[order].desc().nulls_last(), Item.id.desc())
    else:
        raise Invalid(f"order must be one of position, {', '.join(newest)}")
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    if only_ids:
        return list(session.scalars(stmt.with_only_columns(Item.id)))
    return list(session.scalars(stmt))


def priority_sort_key(view_or_item: Any) -> tuple:
    """Urgent first, then high, normal, low, none; then soonest due (no due date last)."""
    if isinstance(view_or_item, dict):
        priority, due = view_or_item["priority"], view_or_item["due_at"]
    else:
        priority, due = view_or_item.priority, view_or_item.due_at
    rank = PRIORITY_RANK[Priority(priority)] if priority else len(PRIORITY_RANK)
    return (rank, due or datetime.max.replace(tzinfo=UTC))


# --- Writes --------------------------------------------------------------------------------


def _add_version(
    session: Session, actor: Actor, item: Item, body: str, note: str | None
) -> BodyVersion:
    """Build the next body version (caller adds it to the session and covers it)."""
    last = session.scalar(
        select(func.max(BodyVersion.version_no)).where(BodyVersion.item_id == item.id)
    )
    version = BodyVersion(
        item_id=item.id,
        version_no=(last or 0) + 1,
        body_md=body,
        author_id=actor.account_id,
        note=note,
    )
    return version


def _set_assignees(cx: Change, item: Item, refs: Iterable[str]) -> None:
    session = cx.session
    wanted = {a.id: a for a in (resolve_account(session, r) for r in refs)}
    rows = list(session.scalars(select(ItemAssignee).where(ItemAssignee.item_id == item.id)))
    current = {r.account_id for r in rows}
    if current == set(wanted):
        return
    handles = dict(
        session.execute(
            select(Account.id, Account.handle).where(Account.id.in_(current | set(wanted)))
        ).all()
    )
    removed = [r for r in rows if r.account_id not in wanted]
    for a in wanted:
        if a not in current:  # a guest cannot hand work to a workspace agent (service/guests.py)
            guests.refuse(session, cx.actor.account_id, wanted[a])
    added = [ItemAssignee(item_id=item.id, account_id=a) for a in wanted if a not in current]
    for r in removed:
        session.delete(r)
    for r in added:
        session.add(r)
    from slate.service.watchers import auto_watch  # watchers imports this module

    auto_watch(cx, item.id, [r.account_id for r in added], "assigned")
    cx.record(
        "item.assigned",
        item_id=item.id,
        field="assignees",
        before=sorted(handles[a] for a in current),
        after=sorted(handles[a] for a in wanted),
        covers=[*removed, *added],
    )


def _set_tags(cx: Change, item: Item, names: Iterable[str]) -> None:
    session = cx.session
    wanted = {t.id: t.name for t in tags_svc.ensure_tags(session, cx.actor, names)}
    rows = list(session.scalars(select(ItemTag).where(ItemTag.item_id == item.id)))
    current = {r.tag_id for r in rows}
    if current == set(wanted):
        return
    current_names = dict(session.execute(select(Tag.id, Tag.name).where(Tag.id.in_(current))).all())
    removed = [r for r in rows if r.tag_id not in wanted]
    added = [ItemTag(item_id=item.id, tag_id=t) for t in wanted if t not in current]
    for r in removed:
        session.delete(r)
    for r in added:
        session.add(r)
    cx.record(
        "item.updated",
        item_id=item.id,
        field="tags",
        before=sorted(current_names.values()),
        after=sorted(wanted.values()),
        covers=[*removed, *added],
    )


def _new_item(
    session: Session, actor: Actor, *, list_id: str, parent: Item | None, fields: Mapping[str, Any]
) -> Item:
    """Build (do not add) one item from validated create fields."""
    unknown = set(fields) - UPDATABLE
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    lst = lists_svc.require_tasks(lists_svc.get_list(session, list_id))
    status_ref = fields.get("status")
    if status_ref is not None:
        status = lists_svc.resolve_status(session, lst.id, status_ref)
    elif lst.default_status_id is not None:
        status = session.get(Status, lst.default_status_id)
    else:
        status = None
    waiting = fields.get("waiting_on_account_id")
    position = fields.get("position")
    if position is None:
        last = session.scalar(select(func.max(Item.position)).where(Item.list_id == lst.id))
        position = (last or 0) + 1
    item = Item(
        id=new_id(),
        list_id=lst.id,
        parent_id=parent.id if parent else None,
        kind=_kind(fields.get("kind")),
        title=_title(fields.get("title")),
        body_md=fields.get("body_md") or "",
        status_id=status.id if status else None,
        priority=_priority(fields.get("priority")),
        start_at=parse_when(fields.get("start_at")),
        due_at=parse_when(fields.get("due_at")),
        due_date_only=_due_date_only(fields),
        completed_at=utcnow() if status and status.kind == StatusKind.done else None,
        waiting_on_account_id=resolve_account(session, waiting).id if waiting else None,
        acceptance_md=fields.get("acceptance_md"),
        external_ref=_external_ref(fields.get("external_ref")),
        estimate_minutes=_estimate(fields.get("estimate_minutes")),
        position=position,
        created_by=actor.account_id,
    )
    return item


def _take_pasted(cx: Change, item: Item, body: str) -> str:
    """`body` with pasted base64 media saved as attachments of `item` (attachments module)."""
    from slate.service.attachments import attach_pasted, split_pasted  # it imports this module

    body, pasted = split_pasted(body)
    attach_pasted(cx, item, pasted)
    return body


def _insert(cx: Change, item: Item, fields: Mapping[str, Any]) -> Item:
    """Insert a built item; a non-empty body's first version rides on `item.created`."""
    session = cx.session
    _claim_ref(session, item.external_ref)
    pasted: list = []
    if item.body_md:
        from slate.service.attachments import split_pasted  # attachments imports this module

        item.body_md, pasted = split_pasted(item.body_md)
    if item.body_md:
        version = _add_version(session, cx.actor, item, item.body_md, fields.get("body_note"))
        cx.create(item, "item.created", covers=[version])
        session.add(version)
    else:
        cx.create(item, "item.created")
    from slate.service.watchers import auto_watch  # watchers imports this module

    auto_watch(cx, item.id, [cx.actor.account_id], "created")
    if pasted:
        from slate.service.attachments import attach_pasted

        attach_pasted(cx, item, pasted)
    if fields.get("assignees"):
        _set_assignees(cx, item, fields["assignees"])
    if fields.get("tags"):
        _set_tags(cx, item, fields["tags"])
    return item


def create_item(
    session: Session,
    actor: Actor,
    *,
    list_id: str | None = None,
    parent_id: str | None = None,
    **fields: Any,
) -> Item:
    """Create exactly one item. Never creates children (AGENTS.md rule 1).

    With `parent_id` the item is a subtask: the parent must exist and sit less than MAX_DEPTH
    levels down (subtasks nest four deep), and the subtask lives in the parent's list
    (`list_id` may be omitted; if given it must match).
    """
    parent = None
    if parent_id is not None:
        parent = session.get(Item, parent_id)
        if parent is None:
            raise Invalid(f"parent item {parent_id} does not exist")
        if depth_of(session, parent) >= MAX_DEPTH:
            raise Invalid(
                f"subtasks nest {MAX_DEPTH} deep at most: the parent is already "
                f"{MAX_DEPTH} levels down (depth exceeds {MAX_DEPTH})"
            )
        if list_id is not None and list_id != parent.list_id:
            raise Invalid("a subtask lives in its parent's list")
        list_id = parent.list_id
    if list_id is None:
        raise Invalid("list_id is required (or parent_id)")
    item = _new_item(session, actor, list_id=list_id, parent=parent, fields=fields)
    with apply_change(session, actor) as cx:
        return _insert(cx, item, fields)


def split(
    session: Session, actor: Actor, item_id: str, children: Sequence[Mapping[str, Any]]
) -> list[Item]:
    """Create several subtasks under `item_id` in one change. The only bulk-child path."""
    parent = get_item(session, item_id)
    if depth_of(session, parent) >= MAX_DEPTH:
        raise Invalid(
            f"cannot split a task {MAX_DEPTH} levels down: subtasks nest {MAX_DEPTH} deep at most"
        )
    if not children:
        raise Invalid("children is empty: split needs at least one child")
    built = [
        (_new_item(session, actor, list_id=parent.list_id, parent=parent, fields=c), c)
        for c in children
    ]
    with apply_change(session, actor) as cx:
        made = []
        for offset, (item, fields) in enumerate(built):
            if fields.get("position") is None:
                item.position = item.position + offset
            made.append(_insert(cx, item, fields))
        cx.record("item.split", item_id=parent.id, after=[c.id for c in made])
    return made


def update_item(session: Session, actor: Actor, item_id: str, changes: Mapping[str, Any]) -> Item:
    """Change any subset of UPDATABLE fields. Unchanged values record nothing.

    `status` is a status id or name in the item's list. `assignees` (handles or ids) and `tags`
    (names; missing tags are created) replace the whole set. `body_md` writes a body version
    (with optional `body_note`). Dates accept ISO 8601 or date-only.
    """
    unknown = set(changes) - UPDATABLE
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    item = get_item(session, item_id)

    # Validate everything before writing anything.
    values: dict[str, Any] = {}
    if "title" in changes:
        values["title"] = _title(changes["title"])
    if "priority" in changes:
        values["priority"] = _priority(changes["priority"])
    if "kind" in changes:
        values["kind"] = _kind(changes["kind"])
    for key in ("start_at", "due_at"):
        if key in changes:
            values[key] = parse_when(changes[key])
    if "due_at" in changes:
        values["due_date_only"] = _due_date_only(changes)
    if "waiting_on_account_id" in changes:
        ref = changes["waiting_on_account_id"]
        values["waiting_on_account_id"] = resolve_account(session, ref).id if ref else None
    if "acceptance_md" in changes:
        values["acceptance_md"] = changes["acceptance_md"]
    if "external_ref" in changes:
        values["external_ref"] = _external_ref(changes["external_ref"])
    if "estimate_minutes" in changes:
        values["estimate_minutes"] = _estimate(changes["estimate_minutes"])
    if "position" in changes:
        if changes["position"] is None:
            raise Invalid("position cannot be null")
        values["position"] = int(changes["position"])
    status = None
    if changes.get("status") is not None:
        status = lists_svc.resolve_status(session, item.list_id, changes["status"])
    elif "status" in changes:
        raise Invalid("status cannot be null")
    assignee_refs = changes.get("assignees")
    if assignee_refs is not None:
        for ref in assignee_refs:
            resolve_account(session, ref)
    tag_names = changes.get("tags")
    if tag_names is not None:
        tag_names = [tags_svc.normalize(n) for n in tag_names]

    with apply_change(session, actor) as cx:
        if "external_ref" in values:
            _claim_ref(session, values["external_ref"], item.id)
        for key, value in values.items():
            cx.set(item, key, value, "item.updated")
        if "body_md" in changes:
            new_body = _take_pasted(cx, item, changes["body_md"] or "")
            if new_body != item.body_md:
                before = item.body_md
                version = _add_version(session, actor, item, new_body, changes.get("body_note"))
                item.body_md = new_body
                session.add(version)
                cx.record(
                    "item.updated",
                    item_id=item.id,
                    field="body_md",
                    before=before,
                    after=new_body,
                    covers=[item, version],
                )
        if status is not None:
            lists_svc.move_item_to_status(cx, item, status)
        if assignee_refs is not None:
            _set_assignees(cx, item, assignee_refs)
        if tag_names is not None:
            _set_tags(cx, item, tag_names)
    return item


MAX_BULK = 500


def bulk_update(
    session: Session,
    actor: Actor,
    ids: Sequence[str],
    changes: Mapping[str, Any],
) -> list[Item]:
    """Apply one set of changes to many items, all or nothing.

    `changes` takes every field `update_item` takes, plus `add_tags` and `remove_tags` (by name),
    `archive` (true/false), `list_id` (move; the item keeps a status of the same kind in the
    target list, or that list's default status; its subtasks move with it, and a subtask cannot
    move without its parent) and `status_kind` (each item gets the first
    status of that kind in its own list, so one call works across lists; refused if a list has
    none). `trash` (true: move to the trash with subtasks; false: restore) must be sent alone.
    Validated fully before anything is written.
    """
    if not ids:
        raise Invalid("ids is empty")
    if len(ids) > MAX_BULK:
        raise Invalid(f"bulk updates are capped at {MAX_BULK} items; got {len(ids)}")
    if len(set(ids)) != len(ids):
        raise Invalid("ids contains duplicates")
    extra = {"add_tags", "remove_tags", "archive", "list_id", "status_kind", "trash"}
    unknown = set(changes) - UPDATABLE - extra
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    want_kind = None
    if "status_kind" in changes:
        if "status" in changes:
            raise Invalid("send `status` or `status_kind`, not both")
        try:
            want_kind = StatusKind(changes["status_kind"])
        except ValueError:
            kinds = ", ".join(k.value for k in StatusKind)
            raise Invalid(f"status_kind must be one of {kinds}") from None
    if "tags" in changes and ("add_tags" in changes or "remove_tags" in changes):
        raise Invalid("send `tags` (replace) or add_tags/remove_tags, not both")

    found = {i.id: i for i in session.scalars(select(Item).where(Item.id.in_(ids)))}
    missing = [i for i in ids if i not in found]
    if missing:
        raise Invalid(f"no such item(s): {', '.join(missing)}")
    items = [found[i] for i in ids]

    if "trash" in changes:
        if len(changes) > 1:
            raise Invalid("send `trash` on its own, not with other changes")
        if changes["trash"] is True:
            return trash_svc.trash_items(session, actor, items)
        if changes["trash"] is False:
            return trash_svc.restore_items(session, actor, items)
        raise Invalid("trash must be true or false")

    target = None
    if "list_id" in changes and changes["list_id"]:
        target = lists_svc.require_tasks(lists_svc.get_list(session, changes["list_id"]))
    per_item: list[dict[str, Any]] = []
    no_kind: dict[str, str] = {}
    tags_now: dict[str, set[str]] = {}
    if "add_tags" in changes or "remove_tags" in changes:
        for names in ("add_tags", "remove_tags"):
            if not isinstance(changes.get(names) or [], list):
                raise Invalid(f"{names} must be a list of tag names")
        tags_now = {i.id: set() for i in items}
        rows = session.execute(
            select(ItemTag.item_id, Tag.name)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id.in_(list(tags_now)))
        )
        for item_id, name in rows:
            tags_now[item_id].add(name)
    for item in items:
        one = {k: v for k, v in changes.items() if k in UPDATABLE}
        if target is not None and target.id != item.list_id:
            if item.parent_id and item.parent_id not in found:
                raise Invalid(
                    f"{item.id} is a subtask; it lives in its parent's list, so move the parent"
                )
            one["list_id"] = target.id
            if "status" not in one:
                mapped = _status_in(session, item, target)
                if mapped is not None:
                    one["status"] = mapped
        if want_kind is not None:
            list_id = one.get("list_id", item.list_id)
            pick = next(
                (st for st in lists_svc.statuses_of(session, list_id) if st.kind == want_kind), None
            )
            if pick is None:
                no_kind.setdefault(list_id, lists_svc.get_list(session, list_id).name)
            else:
                one["status"] = pick.id
        if "add_tags" in changes or "remove_tags" in changes:
            have = set(tags_now[item.id])
            have |= {tags_svc.normalize(n) for n in changes.get("add_tags") or []}
            have -= {tags_svc.normalize(n) for n in changes.get("remove_tags") or []}
            one["tags"] = sorted(have)
        per_item.append(one)
    if no_kind:
        names = ", ".join(sorted(no_kind.values()))
        raise Invalid(f"no {want_kind.value} status in {names}; nothing was changed")

    if changes.get("archive") is False and any(i.trashed_at for i in items):
        raise Invalid("some of these items are in the trash; restore them instead")
    # Validate every item's changes before writing any of them.
    for item, one in zip(items, per_item, strict=True):
        _validate_changes(session, item, one, target_list=target)

    # A task's subtasks live in its list, so they move with it, at every depth.
    riders: list[tuple[Item, dict[str, Any]]] = []
    if target is not None:
        for item, one in zip(items, per_item, strict=True):
            if "list_id" not in one:
                continue
            for sub in descendants_of(session, item.id):
                if sub.id in found or sub.list_id == target.id:
                    continue
                ride: dict[str, Any] = {"list_id": target.id}
                mapped = _status_in(session, sub, target)
                if mapped is not None:
                    ride["status"] = mapped
                riders.append((sub, ride))
    with apply_change(session, actor) as cx:
        for item, one in [*zip(items, per_item, strict=True), *riders]:
            _apply_changes(cx, item, one)
        if "archive" in changes:  # the same reach as archive_item: every subtask goes too
            _set_archived(cx, items, utcnow() if changes["archive"] else None)
    return items


def _status_in(session: Session, item: Item, target: Any) -> str | None:
    """The status `item` takes in `target`: the first of the same kind, else its default."""
    if not item.status_id:
        return None
    current = session.get(Status, item.status_id)
    same_kind = [
        st
        for st in lists_svc.statuses_of(session, target.id)
        if current and st.kind == current.kind
    ]
    if same_kind:
        return same_kind[0].id
    return target.default_status_id


def _validate_changes(
    session: Session, item: Item, changes: Mapping[str, Any], *, target_list: Any
) -> None:
    """Raise before any write if `changes` would not apply to `item` (as update_item would)."""
    if "title" in changes:
        _title(changes["title"])
    if "priority" in changes:
        _priority(changes["priority"])
    if "kind" in changes:
        _kind(changes["kind"])
    for key in ("start_at", "due_at"):
        if key in changes:
            parse_when(changes[key])
    if "position" in changes and changes["position"] is None:
        raise Invalid("position cannot be null")
    if "status" in changes and changes["status"] is None:
        raise Invalid("status cannot be null")
    for key in ("assignees", "tags"):
        if not isinstance(changes.get(key) or [], list):
            raise Invalid(f"{key} must be a list")
    if changes.get("status") is not None:
        lists_svc.resolve_status(
            session, target_list.id if target_list else item.list_id, changes["status"]
        )
    for ref in changes.get("assignees") or []:
        resolve_account(session, ref)
    if "waiting_on_account_id" in changes and changes["waiting_on_account_id"]:
        resolve_account(session, changes["waiting_on_account_id"])
    if "estimate_minutes" in changes:
        _estimate(changes["estimate_minutes"])


def _apply_changes(cx: Change, item: Item, changes: Mapping[str, Any]) -> None:
    """Write already-validated changes inside an open change scope. Mirrors update_item."""
    session = cx.session
    if "list_id" in changes and changes["list_id"] != item.list_id:
        cx.set(item, "list_id", changes["list_id"], "item.moved")
    simple = {
        "title": ("title", _title),
        "priority": ("priority", _priority),
        "kind": ("kind", _kind),
        "start_at": ("start_at", parse_when),
        "due_at": ("due_at", parse_when),
        "acceptance_md": ("acceptance_md", lambda v: v),
        "external_ref": ("external_ref", _external_ref),
        "position": ("position", int),
        "estimate_minutes": ("estimate_minutes", _estimate),
    }
    if "external_ref" in changes:
        _claim_ref(session, _external_ref(changes["external_ref"]), item.id)
    for key, (field, conv) in simple.items():
        if key in changes:
            cx.set(item, field, conv(changes[key]), "item.updated")
    if "due_at" in changes:
        cx.set(item, "due_date_only", _due_date_only(changes), "item.updated")
    if "waiting_on_account_id" in changes:
        ref = changes["waiting_on_account_id"]
        waiting = resolve_account(session, ref).id if ref else None
        cx.set(item, "waiting_on_account_id", waiting, "item.updated")
    if "body_md" in changes and (changes["body_md"] or "") != item.body_md:
        new_body = _take_pasted(cx, item, changes["body_md"] or "")
        version = _add_version(session, cx.actor, item, new_body, changes.get("body_note"))
        before, item.body_md = item.body_md, new_body
        session.add(version)
        cx.record(
            "item.updated",
            item_id=item.id,
            field="body_md",
            before=before,
            after=new_body,
            covers=[item, version],
        )
    if changes.get("status") is not None:
        lists_svc.move_item_to_status(
            cx, item, lists_svc.resolve_status(session, item.list_id, changes["status"])
        )
    if "assignees" in changes:
        _set_assignees(cx, item, changes["assignees"])
    if "tags" in changes:
        _set_tags(cx, item, changes["tags"])


def archive_item(session: Session, actor: Actor, item_id: str) -> Item:
    """Soft: hide the item (and its subtasks) from boards and default queries."""
    item = get_item(session, item_id)
    with apply_change(session, actor) as cx:
        _set_archived(cx, [item], utcnow())
    return item


def _set_archived(cx: Change, items: Sequence[Item], when: datetime | None) -> None:
    """Archive (`when`) or unarchive (None) `items` and every subtask under them, at any
    depth. Trashed subtasks stay as the trash left them."""
    seen: set[str] = set()
    for item in items:
        for target in [item, *descendants_of(cx.session, item.id)]:
            if target.id in seen:
                continue
            seen.add(target.id)
            if when is not None and target.archived_at is None:
                cx.set(target, "archived_at", when, "item.archived")
            elif when is None and target.archived_at is not None and target.trashed_at is None:
                cx.set(target, "archived_at", None, "item.unarchived")


def unarchive_item(session: Session, actor: Actor, item_id: str) -> Item:
    """Bring the item (and its archived subtasks) back."""
    item = get_item(session, item_id)
    if item.trashed_at is not None:
        raise Invalid("this item is in the trash; restore it instead")
    with apply_change(session, actor) as cx:
        _set_archived(cx, [item], None)
    return item


def delete_item(session: Session, actor: Actor, item_id: str) -> None:
    """Hard delete as an undo: only by its creator within 10 minutes of creation.

    Anything else must be archived. Its comments, tags, assignees and body versions go with it;
    its events stay, plus an `item.deleted` event holding its last state.
    """
    item = get_item(session, item_id)
    if item.created_by != actor.account_id:
        raise Invalid("only the account that created an item can delete it; archive it instead")
    if utcnow() - item.created_at > UNDO_WINDOW:
        raise Invalid("delete is an undo for the first 10 minutes only; archive it instead")
    if subtasks_of(session, item.id, include_archived=True):
        raise Invalid("this item has subtasks; delete them first or archive the item")
    with apply_change(session, actor) as cx:
        cx.delete(item, "item.deleted")
