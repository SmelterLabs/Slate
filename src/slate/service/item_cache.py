"""Item views kept as ready JSON, so the board's `GET /items` does not rebuild 20,000 of them.

`items.views()` costs ten batch queries and a dict per item, and the board's first load asks
for every item (about 2 s for 20,000 on one core). Each item's `ItemOut` JSON is cached here
by id; `render()` asks SQLite only for the matching ids, builds the views it is missing and
joins the bytes.

Correct only because every change is an event (rule 3): `core.apply_change` calls
`invalidate()` with the committed events, inside its write lock, before publishing them.

- An event about an item drops that item, the items its view names (parent, links) and the
  cached items whose views name it: subtask counts and blocked flags read the item's row.
- Events that cannot touch an item view (views, favorites, tokens, webhooks...) drop nothing.
- Anything else (a list's statuses, a tag rename, an account's name, links, an event
  type this module does not know) drops everything. New event types are safe by default.

Races: every invalidation takes a sequence number. `render()` takes the current number
before it reads, and stores a view only when neither its item nor the whole cache was
invalidated after that, so a view read before a commit is never stored after it. A change
made by another process (the `slate` CLI, a restored backup) has no in-process event;
`begin()` compares the newest event id in the database with the newest it was told about and
drops everything when they differ.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import Event, Item

# Events that never change any item view.
NO_EFFECT = (
    "view.",
    "favorite.",
    "token.",
    "webhook.",
    "automation.",
    "inbox.",
    "capacity.",
    "push.",
    "health.",  # Health page alerts: no item
    "message.",  # chat; a task made from a message has its own item events
    "channel.",
    "doc.",  # docs and notes; a task made from a doc records item.created
    "account.prefs_changed",
    "account.digest",
    "account.created",
    "item.watched",
    "item.unwatched",
    "item.read",  # an account opened the item: its comment dots, not the view
    "item.reminder",
    "item.due_soon",
    "item.due",
    "item.overdue",
    "comment.reacted",
    "comment.unreacted",
)
# Events about one item (event.item_id) whose effect stays on it and its neighbours.
ITEM_SCOPED = ("item.", "comment.", "checklist.")
# Item events whose effect reaches items the cache cannot name from the event alone.
EVERYTHING = frozenset({"item.linked", "item.unlinked"})
# Item events that change the parent's subtask count but carry no parent_id.
PARENT_COUNTS = frozenset({"item.archived", "item.unarchived", "item.trashed", "item.restored"})
MAX_ITEMS = 250_000  # past this many cached (or remembered as invalidated), start over
CHUNK = 2000  # ids per views() call (each batch query takes them as one IN list)
SINGLE_FLIGHT = 200  # a render missing more than this waits for any other big build first
_building = threading.Lock()
# Tests set this: every render also builds each cached view afresh and fails on a difference,
# so the whole suite checks the invalidation rules above.
VERIFY = False


class ItemCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._json: dict[str, bytes] = {}
        self._near: dict[str, tuple[str, ...]] = {}  # item -> the items its view names
        self._rev: dict[str, set[str]] = {}  # item -> cached items whose views name it
        self._touched: dict[str, int] = {}  # item -> sequence of its last invalidation
        self._seq = 0
        self._cleared = 0  # sequence of the last drop-everything
        self._newest = ""  # newest event id seen by invalidate()
        self.hits = 0
        self.misses = 0

    # --- invalidation (called by core.apply_change under the write lock) ------------------

    def invalidate(self, session: Session, events: Sequence[Any]) -> None:
        """Drop what `events` (just committed) may have changed. `session` reads the parent of
        an archived, trashed or restored item: its subtask count changes."""
        hidden = {
            ev.item_id
            for ev in events
            if ev.type in PARENT_COUNTS and ev.item_id and ev.item_id not in self._json
        }
        parents: dict[str, str | None] = {}
        if hidden:
            parents = dict(
                session.execute(select(Item.id, Item.parent_id).where(Item.id.in_(hidden))).all()
            )
        with self._lock:
            self._seq += 1
            for ev in events:
                if ev.id > self._newest:
                    self._newest = ev.id
                kind = ev.type
                if kind.startswith(NO_EFFECT):
                    continue
                if ev.item_id and kind.startswith(ITEM_SCOPED) and kind not in EVERYTHING:
                    self._drop(ev.item_id)
                    if parents.get(ev.item_id):
                        self._drop(parents[ev.item_id])
                    for snap in (ev.before_json, ev.after_json):
                        if isinstance(snap, dict) and isinstance(snap.get("parent_id"), str):
                            self._drop(snap["parent_id"])
                    if ev.field == "parent_id":
                        for value in (ev.before_json, ev.after_json):
                            if isinstance(value, str):
                                self._drop(value)
                    continue
                self._clear()
            if len(self._touched) > MAX_ITEMS:
                self._clear()  # the tokens of reads in flight now refuse to store: safe

    def _drop(self, item_id: str) -> None:
        """Drop an item, the items its view names (parent, links) and the items whose views
        name it (its subtasks, the items it blocks or is blocked by)."""
        for other in {item_id, *self._near.get(item_id, ()), *self._rev.pop(item_id, ())}:
            self._touched[other] = self._seq
            self._json.pop(other, None)
            for n in self._near.pop(other, ()):
                self._rev.get(n, set()).discard(other)

    def _clear(self) -> None:
        self._cleared = self._seq
        self._json.clear()
        self._near.clear()
        self._rev.clear()
        self._touched.clear()

    def clear(self) -> None:
        with self._lock:
            self._seq += 1
            self._clear()

    # --- reads ------------------------------------------------------------------------------

    def begin(self, session: Session) -> int:
        """The token to store views read from now on. Drops everything if the database holds
        events this process never saw."""
        newest = session.scalar(select(func.max(Event.id))) or ""
        with self._lock:
            if newest != self._newest:
                self._seq += 1
                self._clear()
                self._newest = newest
            return self._seq

    def get_many(self, ids: Sequence[str]) -> tuple[list[bytes | None], list[str]]:
        with self._lock:
            found = [self._json.get(i) for i in ids]
        missing = [i for i, b in zip(ids, found, strict=True) if b is None]
        self.hits += len(ids) - len(missing)
        self.misses += len(missing)
        return found, missing

    def put(self, token: int, built: Sequence[tuple[str, bytes, tuple[str, ...]]]) -> None:
        with self._lock:
            if self._cleared > token:
                return
            if len(self._json) + len(built) > MAX_ITEMS:
                self._clear()
                return
            for item_id, data, near in built:
                if self._touched.get(item_id, 0) > token:
                    continue
                self._json[item_id] = data
                self._near[item_id] = near
                for n in near:
                    self._rev.setdefault(n, set()).add(item_id)

    def stats(self) -> dict[str, int]:
        return {"items": len(self._json), "hits": self.hits, "misses": self.misses}


cache = ItemCache()


def render(
    session: Session,
    ids: Sequence[str],
    views: Callable[[Session, Sequence[Item]], list[dict[str, Any]]],
    adapter: TypeAdapter,
    token: int,
) -> list[bytes]:
    """The `ItemOut` JSON of each id, in order: cached ones as they are, the rest built with
    `views` (one batch per `CHUNK`) and stored under `token` (from `cache.begin()`, taken
    before `ids` was read)."""
    if _restricted(session):
        # The cache is shared by every caller, but a restricted caller's view is not the
        # same view (links to items it cannot see are absent): neither serve nor store it.
        return _build(session, ids, [None] * len(ids), list(ids), views, adapter, None)
    found, missing = cache.get_many(ids)
    if VERIFY:
        _verify(session, ids, found, views, adapter)
    if len(missing) > SINGLE_FLIGHT:
        # Four boards opening at once build the views once, not four times over.
        with _building:
            again, still = cache.get_many(missing)
            filled = dict(zip(missing, again, strict=True))
            found = [b if b is not None else filled[i] for i, b in zip(ids, found, strict=True)]
            return _build(session, ids, found, still, views, adapter, token)
    return _build(session, ids, found, missing, views, adapter, token)


def _build(session, ids, found, missing, views, adapter, token) -> list[bytes]:  # noqa: ANN001
    if missing:
        built: dict[str, bytes] = {}
        for k in range(0, len(missing), CHUNK):
            chunk = missing[k : k + CHUNK]
            rows = list(session.scalars(select(Item).where(Item.id.in_(chunk))))
            fresh = []
            for view in views(session, rows):
                data = adapter.dump_json(view, warnings=False)
                built[view["id"]] = data
                near = (view["parent_id"],) if view["parent_id"] else ()
                near += tuple(view["blocked_by"]) + tuple(view["blocking"]) + tuple(view["related"])
                fresh.append((view["id"], data, near))
            if token is not None:
                cache.put(token, fresh)
        found = [b if b is not None else built.get(i) for i, b in zip(ids, found, strict=True)]
    return [b for b in found if b is not None]  # an id deleted since it was read is left out


def _restricted(session: Session) -> bool:
    from slate.service import access  # access imports models only; keep the import local

    found = access.current(session)
    return found is not None and found.lists is not None


def trim(parts: Sequence[bytes], wanted: set[str] | None) -> list[bytes]:
    """Each view cut to the `wanted` fields (`fields=id,title`); None keeps them whole. Cheaper
    than building the views afresh: an agent's `fields=` read still hits the cache."""
    if wanted is None:
        return list(parts)
    out = []
    for data in parts:
        view = {k: v for k, v in json.loads(data).items() if k in wanted}
        out.append(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode())
    return out


def warm(session: Session, query: Callable[..., Sequence[str]], views, adapter) -> int:  # noqa: ANN001
    """Build the board's first load (`query` returns its ids) into the cache; for startup."""
    token = cache.begin(session)
    return len(render(session, query(session), views, adapter, token))


class StaleView(AssertionError):
    pass


def _verify(session, ids, found, views, adapter) -> None:  # noqa: ANN001
    cached = {i: b for i, b in zip(ids, found, strict=True) if b is not None}
    if not cached:
        return
    rows = list(session.scalars(select(Item).where(Item.id.in_(list(cached)))))
    for view in views(session, rows):
        fresh = adapter.dump_json(view, warnings=False)
        if fresh != cached[view["id"]]:
            raise StaleView(f"cached view of {view['id']} is stale:\n{cached[view['id']]}\n{fresh}")
