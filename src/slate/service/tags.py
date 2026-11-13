"""Tags: lowercase, unique, cutting across every section and list.

One catalog for the whole workspace, so a restricted caller (a guest, a list-limited token)
lists only the tags on items and docs within its reach plus the ones it made, and only an
owner or admin (or an unrestricted caller) renames, recolours, merges or deletes a tag: those
touch every item carrying it, including items the caller cannot see. Creating a tag and
putting it on an item stay open to anyone who can edit the item."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import Doc, DocTag, Event, Item, ItemTag, Recurrence, Tag
from slate.service import access
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Invalid, NotFound


def normalize(name: str) -> str:
    """Tag names are stored trimmed, lowercase, with inner whitespace collapsed."""
    clean = " ".join((name or "").split()).lower()
    if not clean:
        raise Invalid("tag name is required")
    if len(clean) > 60:
        raise Invalid("tag name is longer than 60 characters")
    return clean


# --- Reads ---------------------------------------------------------------------------------


def list_tags(session: Session) -> list[Tag]:
    """Every tag; for a restricted caller, those on an item or doc it can see (the read
    filter decides) and those it created."""
    q = select(Tag).order_by(Tag.name)
    caller = access.current(session)
    if caller is not None and caller.lists is not None:
        reach = set(session.scalars(select(ItemTag.tag_id).distinct()))
        reach |= set(session.scalars(select(DocTag.tag_id).distinct()))
        with access.unrestricted(session):
            reach |= set(
                session.scalars(
                    select(Event.subject_id).where(
                        Event.type == "tag.created", Event.actor_id == caller.account_id
                    )
                )
            )
        q = q.where(Tag.id.in_(reach))
    return list(session.scalars(q))


def usage_counts(session: Session) -> dict[str, int]:
    """`{tag_id: number of non-archived items and docs carrying it}`."""
    counts = dict(
        session.execute(
            select(ItemTag.tag_id, func.count())
            .join(Item, Item.id == ItemTag.item_id)
            .where(Item.archived_at.is_(None))
            .group_by(ItemTag.tag_id)
        ).all()
    )
    for tag_id, n in session.execute(
        select(DocTag.tag_id, func.count())
        .join(Doc, Doc.id == DocTag.doc_id)
        .where(Doc.archived_at.is_(None))
        .group_by(DocTag.tag_id)
    ):
        counts[tag_id] = counts.get(tag_id, 0) + n
    return counts


def get_tag(session: Session, ref: str) -> Tag:
    """By id, or by name (any case)."""
    tag = session.get(Tag, ref) or session.scalar(select(Tag).where(Tag.name == normalize(ref)))
    if tag is None:
        raise NotFound(f"tag '{ref}' not found")
    return tag


# --- Writes --------------------------------------------------------------------------------


def create_tag(session: Session, actor: Actor, *, name: str, color: str | None = None) -> Tag:
    name = normalize(name)
    if session.scalar(select(Tag.id).where(Tag.name == name)):
        raise Conflict(f"tag '{name}' already exists")
    with apply_change(session, actor) as cx:
        return cx.create(Tag(name=name, color=color), "tag.created")


def ensure_tags(session: Session, actor: Actor, names: Iterable[str]) -> list[Tag]:
    """The tags with these names, creating any that do not exist yet (each a `tag.created`)."""
    wanted = list(dict.fromkeys(normalize(n) for n in names))
    found = {t.name: t for t in session.scalars(select(Tag).where(Tag.name.in_(wanted)))}
    missing = [n for n in wanted if n not in found]
    if missing:
        with apply_change(session, actor) as cx:
            for n in missing:
                found[n] = cx.create(Tag(name=n), "tag.created")
    return [found[n] for n in wanted]


def update_tag(
    session: Session, actor: Actor, ref: str, *, name: str | None = None, color: str | None = None
) -> Tag:
    """Rename (or recolor) a tag. Items keep it; only the name changes. Owners and admins."""
    access.require_admin(session, "rename or recolour a tag")
    tag = get_tag(session, ref)
    with apply_change(session, actor) as cx:
        if name is not None:
            new = normalize(name)
            if session.scalar(select(Tag.id).where(Tag.name == new, Tag.id != tag.id)):
                raise Conflict(f"tag '{new}' already exists")
            old = tag.name
            if cx.set(tag, "name", new, "tag.updated"):
                _rename_in_rules(cx, old, new)
        if color is not None:
            cx.set(tag, "color", color or None, "tag.updated")  # "" clears
    return tag


def _rename_in_rules(cx: Change, old: str, new: str | None) -> None:
    """Recurrence rules hold tag names (`tags_json`); keep them in step with a rename, merge or
    delete (`new=None` drops the name) so the next spawned instance carries the right tag."""
    for rule in cx.session.scalars(select(Recurrence)):
        names = list(rule.tags_json or [])
        if old not in names:
            continue
        after = sorted({n for n in names if n != old} | ({new} if new else set()))
        cx.set(rule, "tags_json", after, "recurrence.updated")


def _retag(cx: Change, source: Tag, into: Tag | None) -> None:
    """Move every item and doc from `source` to `into` (or just drop `source`), one event per
    item (`item.updated`) and per doc (`doc.updated`), field `tags`."""
    _retag_items(cx, source, into)
    _retag_docs(cx, source, into)


def _retag_docs(cx: Change, source: Tag, into: Tag | None) -> None:
    session = cx.session
    rows = list(session.scalars(select(DocTag).where(DocTag.tag_id == source.id)))
    if not rows:
        return
    doc_ids = [r.doc_id for r in rows]
    names: dict[str, list[str]] = {d: [] for d in doc_ids}
    for doc_id, name in session.execute(
        select(DocTag.doc_id, Tag.name)
        .join(Tag, Tag.id == DocTag.tag_id)
        .where(DocTag.doc_id.in_(doc_ids))
    ):
        names[doc_id].append(name)
    have_into = (
        set(
            session.scalars(
                select(DocTag.doc_id).where(DocTag.tag_id == into.id, DocTag.doc_id.in_(doc_ids))
            )
        )
        if into
        else set()
    )
    for row in rows:
        before = sorted(names[row.doc_id])
        after = {n for n in before if n != source.name}
        covers: list = [row]
        session.delete(row)
        if into is not None:
            after.add(into.name)
            if row.doc_id not in have_into:
                added = DocTag(doc_id=row.doc_id, tag_id=into.id)
                session.add(added)
                covers.append(added)
        cx.record(
            "doc.updated",
            subject_id=row.doc_id,
            field="tags",
            before=before,
            after=sorted(after),
            covers=covers,
        )


def _retag_items(cx: Change, source: Tag, into: Tag | None) -> None:
    session = cx.session
    rows = list(session.scalars(select(ItemTag).where(ItemTag.tag_id == source.id)))
    if not rows:
        return
    item_ids = [r.item_id for r in rows]
    names: dict[str, list[str]] = {i: [] for i in item_ids}
    for item_id, name in session.execute(
        select(ItemTag.item_id, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id.in_(item_ids))
    ):
        names[item_id].append(name)
    have_into = (
        set(
            session.scalars(
                select(ItemTag.item_id).where(
                    ItemTag.tag_id == into.id, ItemTag.item_id.in_(item_ids)
                )
            )
        )
        if into
        else set()
    )
    for row in rows:
        before = sorted(names[row.item_id])
        after = {n for n in before if n != source.name}
        covers: list = [row]
        session.delete(row)
        if into is not None:
            after.add(into.name)
            if row.item_id not in have_into:
                added = ItemTag(item_id=row.item_id, tag_id=into.id)
                session.add(added)
                covers.append(added)
        cx.record(
            "item.updated",
            item_id=row.item_id,
            field="tags",
            before=before,
            after=sorted(after),
            covers=covers,
        )


def merge_tag(session: Session, actor: Actor, ref: str, *, into: str) -> Tag:
    """Fold tag `ref` into tag `into`: every item, doc and rule carrying `ref` carries `into`
    instead, then `ref` is deleted. Returns the surviving tag. Owners and admins."""
    access.require_admin(session, "merge tags")
    source = get_tag(session, ref)
    target = get_tag(session, into)
    if source.id == target.id:
        raise Invalid("a tag cannot be merged into itself")
    with apply_change(session, actor) as cx:
        _retag(cx, source, target)
        _rename_in_rules(cx, source.name, target.name)
        session.flush()
        cx.delete(source, "tag.deleted")
    return target


def delete_tag(session: Session, actor: Actor, ref: str) -> None:
    """Remove a tag from every item, doc and rule (one event each), then delete it. Owners and
    admins."""
    access.require_admin(session, "delete a tag")
    tag = get_tag(session, ref)
    with apply_change(session, actor) as cx:
        _retag(cx, tag, None)
        _rename_in_rules(cx, tag.name, None)
        session.flush()
        cx.delete(tag, "tag.deleted")
