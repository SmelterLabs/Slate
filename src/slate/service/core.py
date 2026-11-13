"""The single change path. Every database write in Slate happens inside `apply_change()`.

    with apply_change(session, actor) as cx:
        account = cx.create(Account(...), "account.created")
        cx.set(item, "title", "New title", "item.updated")
        cx.delete(obj, "item.deleted")
        cx.record("item.commented", item_id=..., after={...}, covers=[comment])

Each helper makes the write *and* records the `events` row (actor, source, session_ref from the
`Actor`; type, field, before, after from the call). On leaving the block the session commits,
then every recorded event is published to the in-process bus. An exception rolls everything back
and publishes nothing.

Enforcement (AGENTS.md rule 3), hooked onto every SQLAlchemy `Session` by `slate.db`
(so the guard is live however the session was made or which module was imported first):

- `before_flush` raises `WriteOutsideChange` if anything is added, changed or deleted while no
  `apply_change()` block is open on that session, and `WriteWithoutEvent` if an object written
  inside the block was not passed through `create`/`set`/`delete`/`record(covers=...)`.
- `do_orm_execute` raises `WriteOutsideChange` for bulk INSERT/UPDATE/DELETE outside a block.
- Events can never be modified or deleted (also enforced by database triggers).
- `agent_rules.check()` runs before each create/set/delete/record (not `derive`): the
  workspace's agent deny-list refuses an agent actor whatever its token.

The exemptions are bookkeeping that is not a change to anything a person or an agent owns:
`Token.last_used_at`, updated when a token authenticates (`BOOKKEEPING`), and delivery
attempts (`BOOKKEEPING_MODELS`): the `deliveries` (webhooks) and `push_deliveries` (Web Push)
tables are themselves the delivery logs, so a retry is a row, never an event.
"""

from __future__ import annotations

import enum
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, object_session

from slate.connection_models import AgentConnection
from slate.ids import new_id
from slate.models import (
    ISO_FORMAT,
    AutomationRun,
    ChatWake,
    Delivery,
    DocLink,
    DocWake,
    EmailDelivery,
    EngineCursor,
    Event,
    Item,
    PushDelivery,
    PushSubscription,
    Source,
    Token,
    WorkLease,
)
from slate.service import access, agent_rules
from slate.service.bus import bus
from slate.service.item_cache import cache as item_views

_SCOPE_KEY = "slate.change"
_write_lock = threading.RLock()
# (waited, held) seconds of the last LOCK_SAMPLES outermost changes: write contention, for /stats.
LOCK_SAMPLES = 2000
_lock_times: deque[tuple[float, float]] = deque(maxlen=LOCK_SAMPLES)

# (model, attribute) pairs that may change outside apply_change() without an event.
BOOKKEEPING: frozenset[tuple[type, str]] = frozenset(
    {
        (Token, "last_used_at"),
        (AgentConnection, "last_seen_at"),
        (AgentConnection, "verified_at"),
        (PushSubscription, "last_ok_at"),
        (PushSubscription, "failures"),
        (ChatWake, "until"),
        (DocWake, "until"),
    }
)
# Tables whose rows are operational logs, not changes to anything a person or an agent owns.
# `doc_links` is an index of what a page's body embeds; the body's event is the record.
BOOKKEEPING_MODELS: frozenset[type] = frozenset(
    {Delivery, PushDelivery, EmailDelivery, AutomationRun, EngineCursor, WorkLease, DocLink}
)

# Columns never copied into an event payload.
SECRET_COLUMNS = frozenset({"token_hash", "secret_hash", "secret_enc", "p256dh", "auth"})


class WriteOutsideChange(RuntimeError):
    """A write was attempted outside an apply_change() block."""


class WriteWithoutEvent(RuntimeError):
    """A write inside apply_change() did not record an event."""


class ChangeRolledBack(RuntimeError):
    """The transaction was rolled back or committed under an open apply_change() block.

    Its earlier writes are gone (or already committed alone), so the change fails instead of
    committing the rest and publishing events for rows that do not exist.
    """


@dataclass(frozen=True)
class Actor:
    """Who is making a change and through which door. Built once per request."""

    account_id: str
    source: Source
    session_ref: str | None = None


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime(ISO_FORMAT)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    return value


def snapshot(obj: Any) -> dict[str, Any]:
    """Column values of a mapped object, JSON-ready, secrets removed."""
    return {
        attr.key: jsonable(getattr(obj, attr.key))
        for attr in inspect(obj).mapper.column_attrs
        if attr.key not in SECRET_COLUMNS
    }


def event_payload(ev: Event) -> dict[str, Any]:
    """The shape published on the bus (and on SSE and webhooks)."""
    session = object_session(ev)
    before, after = access.event_snapshots(session, ev)
    # For a guest reader, a workspace agent's own events never load (access._guest_criteria);
    # this keeps one that is already in hand from naming it.
    hidden = session is not None and ev.actor_id in access.hidden_agents(session)[0]
    return {
        "id": ev.id,
        "type": ev.type,
        "item_id": ev.item_id,
        "subject_id": ev.subject_id,
        "actor_id": None if hidden else ev.actor_id,
        "source": jsonable(ev.source),
        "field": ev.field,
        "before": before,
        "after": after,
        "session_ref": None if hidden else ev.session_ref,
        "at": jsonable(ev.at),
    }


def _item_id_of(obj: Any) -> str | None:
    if isinstance(obj, Item):
        return obj.id
    return getattr(obj, "item_id", None)


@dataclass
class Change:
    session: Session
    actor: Actor
    events: list[Event] = field(default_factory=list)
    _covered: set[int] = field(default_factory=set)
    _bulk: bool = False
    # Set when something ends the transaction under the open block (see ChangeRolledBack).
    _ended: str | None = None
    _closing: bool = False

    def record(
        self,
        type: str,
        *,
        item_id: str | None = None,
        subject_id: str | None = None,
        field: str | None = None,
        before: Any = None,
        after: Any = None,
        covers: Iterable[Any] = (),
    ) -> Event:
        """Record one event. `covers` names the objects this event accounts for."""
        covers = list(covers)
        agent_rules.check(
            self.session, self.actor, type, field=field, obj=covers[0] if covers else None
        )
        for obj in covers:
            access.check_object(self.session, obj)
            self._covered.add(id(obj))
        if not covers:
            access.check_record(self.session, type, item_id)
        return self._append(
            type, item_id=item_id, subject_id=subject_id, field=field, before=before, after=after
        )

    def _append(
        self,
        type: str,
        *,
        item_id: str | None = None,
        subject_id: str | None = None,
        field: str | None = None,
        before: Any = None,
        after: Any = None,
    ) -> Event:
        ev = Event(
            item_id=item_id,
            subject_id=subject_id or item_id,
            actor_id=self.actor.account_id,
            source=self.actor.source,
            type=type,
            field=field,
            before_json=jsonable(before),
            after_json=jsonable(after),
            session_ref=self.actor.session_ref,
        )
        self.session.add(ev)
        self.events.append(ev)
        return ev

    def derive(
        self,
        obj: Any,
        type: str,
        *,
        field: str | None = None,
        before: Any = None,
        after: Any = None,
        **values: Any,
    ) -> Event:
        """Set `values` on `obj` and record `type`, as a consequence of this change rather than
        an edit the actor asked for (an item becoming unblocked because its blocker was done).

        No access check: the actor was allowed the change it follows from, and may not be
        allowed to edit `obj` itself (an agent limited to one list finishing a blocker of a
        task in another list)."""
        self._covered.add(id(obj))
        for key, value in values.items():
            setattr(obj, key, value)
        return self._append(
            type,
            item_id=_item_id_of(obj),
            subject_id=getattr(obj, "id", None),
            field=field,
            before=before,
            after=after,
        )

    def create(self, obj: Any, type: str, *, after: Any = None, covers: Iterable[Any] = ()) -> Any:
        """Insert `obj` and record `type` with its snapshot (or `after`) as the new value.

        `covers` names other objects added alongside `obj` that this event also accounts for
        (e.g. an item's first body version).
        """
        if hasattr(obj, "id") and obj.id is None:
            obj.id = new_id()
        agent_rules.check(self.session, self.actor, type, obj=obj)
        access.check_object(self.session, obj)
        self._covered.add(id(obj))
        for other in covers:
            self._covered.add(id(other))
        self.session.add(obj)
        self.session.flush()  # fill column defaults so the snapshot is complete
        self._append(
            type,
            item_id=_item_id_of(obj),
            subject_id=getattr(obj, "id", None),
            after=snapshot(obj) if after is None else after,
        )
        return obj

    def set(self, obj: Any, field: str, value: Any, type: str) -> bool:
        """Change one attribute and record before -> after. Returns False (no event) if equal."""
        before = getattr(obj, field)
        if before == value:
            return False
        agent_rules.check(self.session, self.actor, type, field=field, obj=obj)
        access.check_object(self.session, obj, field_name=field, value=value)
        self._covered.add(id(obj))
        setattr(obj, field, value)
        self._append(
            type,
            item_id=_item_id_of(obj),
            subject_id=getattr(obj, "id", None),
            field=field,
            before=before,
            after=value,
        )
        return True

    def delete(self, obj: Any, type: str) -> None:
        """Hard-delete `obj`, recording its last snapshot as `before`."""
        agent_rules.check(self.session, self.actor, type, obj=obj)
        access.check_object(self.session, obj)
        self._covered.add(id(obj))
        self._append(
            type,
            item_id=_item_id_of(obj),
            subject_id=getattr(obj, "id", None),
            before=snapshot(obj),
        )
        self.session.delete(obj)


@contextmanager
def apply_change(session: Session, actor: Actor) -> Iterator[Change]:
    """Open (or join) the change scope on `session`. The outermost block commits and publishes.

    Nesting is allowed so service functions can call each other; the inner block joins the
    outer one and must use the same actor.
    """
    current: Change | None = session.info.get(_SCOPE_KEY)
    if current is not None:
        if current.actor != actor:
            raise RuntimeError("nested apply_change() with a different actor")
        yield current
        return

    change = Change(session=session, actor=actor)
    # Held from the first write to the last publish: event ids are then assigned, committed and
    # published in one order, so `id > cursor` is a complete catch-up (slate.api.events). SQLite
    # serialises writers anyway; this only moves the queue from the database to here.
    asked = time.perf_counter()
    with _write_lock:
        got = time.perf_counter()
        session.info[_SCOPE_KEY] = change
        try:
            yield change
            if change._ended:
                raise ChangeRolledBack(
                    f"the transaction was {change._ended} inside apply_change(); "
                    "nothing of this change was kept"
                )
            session.flush()
            if change.events:
                from slate.service import links  # links imports this module

                # Blocked/unblocked follow from what just changed, also for items the caller
                # cannot see (a list-limited agent finishing a blocker of another list's task).
                with access.unrestricted(session):
                    links.settle(change)
                    session.flush()
            if change._bulk and not change.events:
                raise WriteWithoutEvent("bulk write inside apply_change() recorded no event")
            change._closing = True
            session.commit()
        except BaseException:
            change._closing = True
            session.rollback()
            raise
        finally:
            session.info.pop(_SCOPE_KEY, None)

        if change.events:
            # Before publishing: a board told about a change must never read the old view.
            item_views.invalidate(session, change.events)

        # The bus carries the whole event; each listener's gate decides what its reader sees.
        with access.unrestricted(session):
            for ev in change.events:
                bus.publish(event_payload(ev))
        _lock_times.append((got - asked, time.perf_counter() - got))


def _ended_under_change(session: Session, how: str) -> None:
    change: Change | None = session.info.get(_SCOPE_KEY)
    if change is not None and not change._closing and change._ended is None:
        change._ended = how


@event.listens_for(Session, "after_soft_rollback")
def _rolled_back(session: Session, _previous: Any) -> None:  # noqa: ANN401
    _ended_under_change(session, "rolled back")


@event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    _ended_under_change(session, "committed")


def in_change(session: Session) -> bool:
    """Is an apply_change() block open on `session`? (Bookkeeping then rides on its commit.)"""
    return session.info.get(_SCOPE_KEY) is not None


def write_lock_stats() -> dict[str, float | int]:
    """How long changes waited for the write lock and held it (ms), over the last samples."""
    samples = list(_lock_times)
    if not samples:
        return {"samples": 0}
    waits = sorted(w for w, _ in samples)
    helds = sorted(h for _, h in samples)

    def pct(xs: list[float], q: float) -> float:
        return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 2)

    return {
        "samples": len(samples),
        "wait_p50_ms": pct(waits, 0.5),
        "wait_p95_ms": pct(waits, 0.95),
        "wait_max_ms": pct(waits, 1.0),
        "held_p50_ms": pct(helds, 0.5),
        "held_p95_ms": pct(helds, 0.95),
        "held_max_ms": pct(helds, 1.0),
    }


# --- Guards --------------------------------------------------------------------------------


def _changed_attrs(obj: Any) -> set[str]:
    state = inspect(obj)
    return {a.key for a in state.attrs if a.history.has_changes()}


def _is_bookkeeping(obj: Any) -> bool:
    if type(obj) in BOOKKEEPING_MODELS:
        return True
    changed = _changed_attrs(obj)
    return bool(changed) and all((type(obj), key) in BOOKKEEPING for key in changed)


def guard_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    _check_flush(session)
    session.info[_FLUSH_KEY] = True  # checked object by object; guard_cursor lets it by


def _check_flush(session: Session) -> None:
    change: Change | None = session.info.get(_SCOPE_KEY)
    dirty = [o for o in session.dirty if session.is_modified(o)]

    for obj in [*dirty, *session.deleted]:
        if isinstance(obj, Event):
            raise WriteOutsideChange("events are append-only")

    if change is None:
        offenders = [o for o in [*session.new, *session.deleted] if not _is_bookkeeping(o)]
        offenders += [o for o in dirty if not _is_bookkeeping(o)]
        if offenders:
            names = sorted({type(o).__name__ for o in offenders})
            raise WriteOutsideChange(
                f"write to {', '.join(names)} outside apply_change(); "
                "every mutation goes through slate.service"
            )
        return

    uncovered = [
        o
        for o in [*session.new, *dirty, *session.deleted]
        if not isinstance(o, Event) and id(o) not in change._covered and not _is_bookkeeping(o)
    ]
    if uncovered:
        names = sorted({type(o).__name__ for o in uncovered})
        raise WriteWithoutEvent(f"write to {', '.join(names)} recorded no event")


_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")
# Set while a flush that guard_flush checked runs (SQLAlchemy's own `_flushing` is also set by
# `bulk_insert_mappings`, which no guard sees).
_FLUSH_KEY = "slate.flush_checked"


@event.listens_for(Session, "after_flush_postexec")
def _flush_done(session: Session, _flush_context: Any) -> None:  # noqa: ANN401
    session.info.pop(_FLUSH_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _flush_aborted(session: Session, _previous: Any) -> None:  # noqa: ANN401
    session.info.pop(_FLUSH_KEY, None)


def guard_cursor(session: Session, statement: str) -> None:
    """The last line of the guard: every SQL write a session sends outside its flush.

    The flush and ORM statements are checked above with their objects; this catches what
    reaches the database another way (`text()`, `session.connection().execute`,
    `exec_driver_sql`, `bulk_insert_mappings`): refused outside a change, and inside one it
    must be covered by an event like any bulk write.
    """
    if session.info.get(_FLUSH_KEY) or not statement.lstrip()[:7].upper().startswith(_WRITE_VERBS):
        return
    change: Change | None = session.info.get(_SCOPE_KEY)
    if change is None:
        raise WriteOutsideChange("raw SQL write outside apply_change()")
    change._bulk = True


def guard_bulk(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    change: Change | None = state.session.info.get(_SCOPE_KEY)
    if change is None:
        raise WriteOutsideChange("bulk write outside apply_change()")
    change._bulk = True
