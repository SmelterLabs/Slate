"""Dependencies between items (ClickUp "waiting on / blocking", and "linked tasks").

A link is stored once: `from_item` **blocks** `to_item`, the two **relate** (symmetric), or
`from_item` **duplicates** `to_item` (ClickUp "duplicate of"; merging leaves one behind).
Each add/remove records one event on each side, so both items' histories and both boards hear
of it: `item.linked` / `item.unlinked` with `after`/`before` = `{kind, item_id}` where `kind`
is from that item's point of view (`blocks`, `blocked_by`, `relates`, `duplicate_of`,
`duplicated_by`).

An item is **blocked** while any item blocking it is not done/cancelled and not archived
(trashing archives). `blocked_item_ids()` is that rule as a query (the next-task call and
`GET /items?blocked` skip with it); the item view's `blocked` is the same rule, batch-loaded.

The blocked state is derived, never set by hand. `settle()` runs at the end of every change
(`core.apply_change`): when a change could have opened or closed a blocker (a status change,
archive, trash, restore, link added or removed, a list's statuses edited), it recomputes the
items involved and the items they block. An item that just became blocked gets `item.blocked`
(`after = {blocked_by: [open blocker ids]}`); one whose last open blocker just closed gets
`item.unblocked` (`before = {blocked_since}`, `after = {cleared_by, open}`: the blockers this
change closed or unlinked, and whether the item itself is still to do; only then do its
assignees get an `unblocked` notification). Both are recorded in the same change, with its
actor, and keep `items.blocked_since` in step (that column exists only to tell a transition
from no change).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session, aliased

from slate.models import Item, ItemLink, LinkKind, Status, StatusKind, utcnow
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Invalid

KINDS = ("blocks", "blocked_by", "relates", "duplicate_of", "duplicated_by")
# The link fields of an item view (and of `GET /items/{id}/links`).
LINK_KEYS = ("blocked_by", "blocking", "related", "duplicate_of", "duplicated_by", "blocked")
_MIRROR = {
    "blocks": "blocked_by",
    "blocked_by": "blocks",
    "relates": "relates",
    "duplicate_of": "duplicated_by",
    "duplicated_by": "duplicate_of",
}


def _get(session: Session, item_id: str) -> Item:
    from slate.service.items import get_item  # items imports this module

    return get_item(session, item_id)


def _normal(item_id: str, kind: str, other_id: str) -> tuple[str, str, LinkKind]:
    """(from, to, stored kind) for a link described from `item_id`'s side."""
    if kind not in KINDS:
        raise Invalid(f"link kind must be one of {list(KINDS)}")
    if item_id == other_id:
        raise Invalid("an item cannot be linked to itself")
    if kind == "blocks":
        return item_id, other_id, LinkKind.blocks
    if kind == "blocked_by":
        return other_id, item_id, LinkKind.blocks
    if kind == "duplicate_of":
        return item_id, other_id, LinkKind.duplicates
    if kind == "duplicated_by":
        return other_id, item_id, LinkKind.duplicates
    a, b = sorted((item_id, other_id))
    return a, b, LinkKind.relates


def _find(session: Session, a: str, b: str, kind: LinkKind) -> ItemLink | None:
    return session.scalar(
        select(ItemLink).where(
            ItemLink.from_item_id == a, ItemLink.to_item_id == b, ItemLink.kind == kind
        )
    )


def _creates_cycle(session: Session, blocker: str, blocked: str) -> bool:
    """Would `blocker blocks blocked` close a loop (`blocked` already blocks `blocker`)?"""
    seen, frontier = set(), [blocked]
    while frontier:
        current = frontier.pop()
        if current == blocker:
            return True
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(
            session.scalars(
                select(ItemLink.to_item_id).where(
                    ItemLink.from_item_id == current, ItemLink.kind == LinkKind.blocks
                )
            )
        )
    return False


# --- Reads ---------------------------------------------------------------------------------

# Built once: making an alias copies every column, which cost ~2 ms per call on every item view.
_BLOCKER = aliased(Item, name="blocker")
_BLOCKER_STATUS = aliased(Status, name="blocker_status")


def links_of(session: Session, item_ids: Sequence[str]) -> dict[str, dict[str, list[str]]]:
    """`{item_id: {blocked_by, blocking, related, duplicate_of, duplicated_by, open_blockers}}`,
    batch-loaded.

    `open_blockers` lists the `blocked_by` items that are still open (not done, cancelled or
    archived): the item is blocked while it is non-empty.
    """
    out: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {
            "blocked_by": [],
            "blocking": [],
            "related": [],
            "duplicate_of": [],
            "duplicated_by": [],
            "open_blockers": [],
        }
    )
    if not item_ids:
        return out
    ids = list(item_ids)
    rows = list(
        session.execute(
            select(ItemLink.from_item_id, ItemLink.to_item_id, ItemLink.kind)
            .where(or_(ItemLink.from_item_id.in_(ids), ItemLink.to_item_id.in_(ids)))
            .order_by(ItemLink.created_at)
        )
    )
    blocker_ids = {a for a, _, k in rows if k == LinkKind.blocks}
    blocker, st = _BLOCKER, _BLOCKER_STATUS
    open_ids = (
        set(
            session.scalars(
                select(blocker.id)
                .outerjoin(st, st.id == blocker.status_id)
                .where(
                    blocker.id.in_(blocker_ids),
                    blocker.archived_at.is_(None),
                    or_(
                        st.kind.is_(None),
                        st.kind.not_in([StatusKind.done, StatusKind.cancelled]),
                    ),
                )
            )
        )
        if blocker_ids
        else set()
    )
    wanted = set(ids)
    for a, b, kind in rows:
        if kind == LinkKind.blocks:
            if b in wanted:
                out[b]["blocked_by"].append(a)
                if a in open_ids:
                    out[b]["open_blockers"].append(a)
            if a in wanted:
                out[a]["blocking"].append(b)
        elif kind == LinkKind.duplicates:
            if a in wanted:
                out[a]["duplicate_of"].append(b)
            if b in wanted:
                out[b]["duplicated_by"].append(a)
        else:
            if a in wanted:
                out[a]["related"].append(b)
            if b in wanted:
                out[b]["related"].append(a)
    return out


def blocked_item_ids() -> Select:
    """A subquery of ids of items that have at least one open blocker (for `GET /items?blocked`)."""
    blocker, st = _BLOCKER, _BLOCKER_STATUS
    return (
        select(ItemLink.to_item_id)
        .join(blocker, blocker.id == ItemLink.from_item_id)
        .outerjoin(st, st.id == blocker.status_id)
        .where(
            and_(
                ItemLink.kind == LinkKind.blocks,
                blocker.archived_at.is_(None),
                or_(st.kind.is_(None), st.kind.not_in([StatusKind.done, StatusKind.cancelled])),
            )
        )
    )


# --- Blocked / unblocked -------------------------------------------------------------------

# Events after which the item's own blockers, or the items it blocks, may have opened or closed.
SETTLE_TYPES = frozenset(
    {
        "item.status_changed",
        "item.archived",
        "item.unarchived",
        "item.trashed",
        "item.restored",
        "item.linked",
        "item.unlinked",
        "item.deleted",
        "item.purged",
    }
)
_GONE = frozenset({"item.deleted", "item.purged"})


def _open_expr():  # noqa: ANN202 - a SQL boolean over _BLOCKER / _BLOCKER_STATUS
    blocker, st = _BLOCKER, _BLOCKER_STATUS
    return and_(
        blocker.archived_at.is_(None),
        or_(st.kind.is_(None), st.kind.not_in([StatusKind.done, StatusKind.cancelled])),
    )


def settle(cx: Change) -> None:
    """Record `item.blocked` / `item.unblocked` for what this change opened or closed.

    Called by `apply_change()` before commit; the events it adds are not looked at again. Costs
    nothing for changes that touch no status, archive, trash or link, and one indexed query for
    one that does but blocks nothing.
    """
    session = cx.session
    touched: set[str] = set()
    unlinked: dict[str, set[str]] = defaultdict(set)  # item -> blockers unlinked from it
    lists: set[str] = set()
    gone = False
    for ev in cx.events:
        if ev.type in SETTLE_TYPES and ev.item_id:
            touched.add(ev.item_id)
            gone = gone or ev.type in _GONE
            before = ev.before_json if isinstance(ev.before_json, dict) else {}
            if ev.type == "item.unlinked" and before.get("kind") == "blocked_by":
                unlinked[ev.item_id].add(before.get("item_id"))
        elif ev.type == "list.statuses_changed" and ev.subject_id:
            lists.add(ev.subject_id)  # a status may have moved into or out of done
    if lists:
        touched.update(session.scalars(select(Item.id).where(Item.list_id.in_(lists))))
    if not touched:
        return
    candidates = set(touched)
    candidates.update(
        session.scalars(
            select(ItemLink.to_item_id).where(
                ItemLink.kind == LinkKind.blocks, ItemLink.from_item_id.in_(touched)
            )
        )
    )
    if gone:  # a blocker deleted for good takes its links with it: catch what it left
        candidates.update(
            session.scalars(
                select(Item.id).where(
                    Item.blocked_since.is_not(None), Item.id.not_in(blocked_item_ids())
                )
            )
        )
    blocker, st = _BLOCKER, _BLOCKER_STATUS
    blockers: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for a, b, is_open in session.execute(
        select(ItemLink.from_item_id, ItemLink.to_item_id, _open_expr())
        .join(blocker, blocker.id == ItemLink.from_item_id)
        .outerjoin(st, st.id == blocker.status_id)
        .where(ItemLink.kind == LinkKind.blocks, ItemLink.to_item_id.in_(candidates))
        .order_by(ItemLink.created_at)
    ):
        blockers[b].append((a, bool(is_open)))
    now = utcnow()
    closed = {StatusKind.done, StatusKind.cancelled}
    for item in session.scalars(select(Item).where(Item.id.in_(candidates)).order_by(Item.id)):
        open_ids = [a for a, is_open in blockers[item.id] if is_open]
        if open_ids and item.blocked_since is None:
            cx.derive(
                item,
                "item.blocked",
                field="blocked",
                after={"blocked_by": open_ids},
                blocked_since=now,
            )
        elif not open_ids and item.blocked_since is not None:
            cleared = {a for a, _ in blockers[item.id] if a in touched} | unlinked[item.id]
            cx.derive(
                item,
                "item.unblocked",
                field="blocked",
                before={"blocked_since": item.blocked_since},
                after={
                    "cleared_by": sorted(c for c in cleared if c),
                    # Still to do (not closed, archived or trashed): only then is it news.
                    "open": item.archived_at is None
                    and (
                        item.status_id is None
                        or session.get(Status, item.status_id).kind not in closed
                    ),
                },
                blocked_since=None,
            )


# --- Writes --------------------------------------------------------------------------------


def add_link(session: Session, actor: Actor, item_id: str, *, kind: str, other_id: str) -> Item:
    """Link `item_id` to `other_id`; `kind` is from `item_id`'s side: blocks, blocked_by, relates.

    Adding an existing link is a no-op. A `blocks` link that would make a cycle is refused.
    """
    item = _get(session, item_id)
    _get(session, other_id)
    a, b, stored = _normal(item_id, kind, other_id)
    if _find(session, a, b, stored) is not None:
        return item
    if stored == LinkKind.blocks and _creates_cycle(session, a, b):
        raise Invalid("that dependency would make a loop (each would wait on the other)")
    with apply_change(session, actor) as cx:
        link = ItemLink(from_item_id=a, to_item_id=b, kind=stored, created_by=actor.account_id)
        session.add(link)
        cx.record(
            "item.linked",
            item_id=item_id,
            field="links",
            after={"kind": kind, "item_id": other_id},
            covers=[link],
        )
        cx.record(
            "item.linked",
            item_id=other_id,
            field="links",
            after={"kind": _MIRROR[kind], "item_id": item_id},
        )
    return item


def remove_link(session: Session, actor: Actor, item_id: str, *, kind: str, other_id: str) -> Item:
    """Remove one link (described from `item_id`'s side). Removing a missing link is a no-op."""
    item = _get(session, item_id)
    a, b, stored = _normal(item_id, kind, other_id)
    link = _find(session, a, b, stored)
    if link is None:
        return item
    with apply_change(session, actor) as cx:
        cx.session.delete(link)
        cx.record(
            "item.unlinked",
            item_id=item_id,
            field="links",
            before={"kind": kind, "item_id": other_id},
            covers=[link],
        )
        cx.record(
            "item.unlinked",
            item_id=other_id,
            field="links",
            before={"kind": _MIRROR[kind], "item_id": item_id},
        )
    return item
