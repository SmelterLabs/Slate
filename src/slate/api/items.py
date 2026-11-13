"""/items: create, read, query, update, archive, split, history, versions, undo-delete."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.events import CURSOR_PATTERN
from slate.api.schemas import (
    ActivityOut,
    AssigneeOut,
    EventOut,
    ItemDetailOut,
    ItemIn,
    ItemKind,
    ItemOut,
    ItemPatch,
    ReportIn,
    SplitIn,
    VersionOut,
    WatchersIn,
    WatchIn,
    When,
)
from slate.models import BodyVersion
from slate.service import (
    activity,
    aiadd,
    item_cache,
    items,
    reports,
    trash,
    watchers,
    work,
)
from slate.service import brief as brief_svc
from slate.service.core import event_payload

router = APIRouter(tags=["items"])
JSON = "application/json"


ITEM_FIELDS = tuple(ItemOut.model_fields)
_ITEM_ADAPTER = TypeAdapter(ItemOut)


def parse_fields(fields: str | None) -> set[str] | None:
    """`fields=id,title,status` -> the set (id always included); None means every field."""
    if not fields:
        return None
    wanted = {f.strip() for f in fields.split(",") if f.strip()}
    unknown = wanted - set(ITEM_FIELDS)
    if unknown:
        raise HTTPException(
            422, detail=f"unknown field(s) {sorted(unknown)}; fields: {', '.join(ITEM_FIELDS)}"
        )
    return wanted | {"id"}


@router.get("/items", response_model=list[ItemOut])
def query_items(
    _: ReadAuth,
    session: DbSession,
    list_id: str | None = None,
    section_id: str | None = None,
    status: Annotated[str | None, Query(description="Status id or name")] = None,
    assignee: Annotated[str | None, Query(description="Account handle or id")] = None,
    tag: Annotated[str | None, Query(description="Tag name")] = None,
    external_ref: Annotated[
        str | None, Query(description="Exact match on the filer's own reference (unique)")
    ] = None,
    due_before: Annotated[
        When | None, Query(description="Exclusive; ISO 8601 or YYYY-MM-DD")
    ] = None,
    due_after: Annotated[
        When | None, Query(description="Exclusive; ISO 8601 or YYYY-MM-DD")
    ] = None,
    q: Annotated[str | None, Query(description="Substring of title or body")] = None,
    stale: Annotated[
        bool, Query(description="Due moved 3+ times, or open untouched 30+ days")
    ] = False,
    include_archived: bool = False,
    archived: Annotated[
        bool | None, Query(description="true: only archived (not trashed); false: not archived")
    ] = None,
    trashed: Annotated[bool, Query(description="Only items in the trash")] = False,
    closed: Annotated[
        bool | None, Query(description="true: only done/cancelled; false: only open/active")
    ] = None,
    order: Annotated[
        Literal["position", "completed", "archived", "trashed", "updated", "created"],
        Query(description="position (default), or newest first by that date"),
    ] = "position",
    kind: ItemKind | None = None,
    blocked: Annotated[
        bool | None, Query(description="true: waits on an open item; false: waits on none")
    ] = None,
    changed_since: Annotated[
        str | None,
        Query(
            pattern=CURSOR_PATTERN,
            description="Event id (e.g. a previous X-Slate-Cursor): only items changed after it",
        ),
    ] = None,
    fields: Annotated[
        str | None,
        Query(description="Comma-separated item fields to return (id always); default all"),
    ] = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    ids: Annotated[
        str | None,
        Query(description="Comma-separated item ids (at most 1000): only these items"),
    ] = None,
    open_comment_for: Annotated[
        str | None,
        Query(description="Handle or id: only items with an open Ask addressed to them"),
    ] = None,
) -> Any:
    """Filters combine with AND. `status` is an id or name; `assignee` a handle; `tag` a name;
    due bounds are exclusive and accept ISO 8601 or YYYY-MM-DD; `q` matches title or body;
    `stale` = due date moved 3+ times, or open and untouched for 30+ days; `blocked` = waits
    on an open item (true) or on none (false).

    For agents: `fields=id,title,status,due_at` trims each item to what you need;
    `limit`/`offset` page through; `changed_since=<cursor>` returns only items with a change
    after that event id (pair it with `include_archived=true` to also see archivals). Every
    response carries `X-Slate-Cursor` for the next `changed_since`, and an `ETag`. `ids=a,b,c`
    fetches several known items in one call (the board's refetch)."""
    wanted = parse_fields(fields)
    id_list = None
    if ids is not None:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        if len(id_list) > 1000:
            raise HTTPException(422, detail="at most 1000 ids")
    # Items come from the item cache (cut to `fields` after): only the matching ids are read.
    token = item_cache.cache.begin(session)
    found = items.query_items(
        session,
        list_id=list_id,
        section_id=section_id,
        open_comment_for=open_comment_for,
        status=status,
        assignee=assignee,
        tag=tag,
        external_ref=external_ref,
        due_before=due_before,
        due_after=due_after,
        q=q,
        kind=kind,
        include_archived=include_archived,
        archived=archived,
        trashed=trashed,
        closed=closed,
        order=order,
        stale=stale,
        blocked=blocked,
        changed_since=changed_since,
        limit=limit,
        offset=offset,
        ids=id_list,
        only_ids=True,
    )
    parts = item_cache.trim(
        item_cache.render(session, found, items.views, _ITEM_ADAPTER, token), wanted
    )
    return Response(b"[" + b",".join(parts) + b"]", media_type=JSON)


@router.post("/items", status_code=status.HTTP_201_CREATED, response_model=ItemOut)
def create_item(body: ItemIn, auth: WriteAuth, session: DbSession) -> dict:
    """Create exactly one item; never children. `parent_id` makes it a subtask; subtasks nest
    four deep under a top task. Use `POST /items/{id}/split` to create several subtasks."""
    item = items.create_item(session, auth.actor, **body.model_dump(exclude_none=True))
    return items.views(session, [item])[0]


class AiItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_id: str
    text: str = Field(min_length=1, max_length=20000, description="The task in plain words")
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Fields the place implies (a column's status, due day, priority, assignee "
        "or tag): any POST /items field but title, body_md, list_id and parent_id",
    )


@router.post("/items/ai", status_code=status.HTTP_201_CREATED, response_model=ItemOut)
def create_item_ai(
    body: AiItemIn, auth: WriteAuth, session: DbSession, tasks: BackgroundTasks
) -> dict:
    """AI add: file exactly one item from plain words (title = the first sentence, description
    = all of it, plus `defaults`) and return it at once; then the configured Ollama model
    (`SLATE_AI_URL`, `SLATE_AI_MODEL`) fills in the title, description, assignees, dates,
    priority, status and tags the words decide, written as Slate's own account (`session_ref`
    `ai-add`) and seen through the stream. Fields changed before the model answers are left
    alone. 409 when AI add is off (`GET /me` `ai_add`)."""
    if not aiadd.enabled():
        raise HTTPException(status.HTTP_409_CONFLICT, "AI add is not set up on this server")
    extra = {k: v for k, v in body.defaults.items() if k not in AI_RESERVED}
    try:
        ItemIn.model_validate({"title": "x", "list_id": body.list_id, **extra})
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    item = aiadd.file(session, auth.actor, body.list_id, body.text, extra)
    tasks.add_task(aiadd.fill_later, aiadd.ask(session, auth.actor, item, body.text))
    return items.views(session, [item])[0]


AI_RESERVED = frozenset({"title", "body_md", "list_id", "parent_id", "body_note"})


@router.get("/items/{item_id}", response_model=ItemDetailOut)
def get_item(item_id: str, _: ReadAuth, session: DbSession) -> dict:
    """The item with its subtasks, tags, assignees, comments and last 20 events."""
    return items.item_detail(session, item_id)


@router.patch("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: str, body: ItemPatch, auth: WriteAuth, session: DbSession) -> dict:
    """Send only what changes; `null` clears a field. `status` by id or name; `assignees`
    (handles) and `tags` (names) replace the whole set."""
    item = items.update_item(session, auth.actor, item_id, body.model_dump(exclude_unset=True))
    return items.views(session, [item])[0]


class BulkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=items.MAX_BULK, description="Item ids")
    changes: dict = Field(
        description=(
            "Any update field, plus add_tags, remove_tags, archive (bool), list_id (move),"
            " status_kind (each item's own list's first status of that kind)"
        )
    )


@router.post("/items/bulk", response_model=list[ItemOut])
def bulk_update(body: BulkIn, auth: WriteAuth, session: DbSession) -> list[dict]:
    """One change applied to many items: every PATCH field, plus add_tags, remove_tags,
    archive, trash (true/false, sent alone: to the trash and back), list_id (move to another
    list; subtasks follow their parent) and status_kind (each item gets its own list's first
    status of that kind, so one call works across lists). All or nothing; one event per item
    per changed field."""
    updated = items.bulk_update(session, auth.actor, body.ids, body.changes)
    return items.views(session, updated)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str, auth: WriteAuth, session: DbSession) -> Response:
    """Undo a create: only its creator, only within 10 minutes. Otherwise archive it."""
    items.delete_item(session, auth.actor, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/items/{item_id}/archive", response_model=ItemOut)
def archive_item(item_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Hide the item (and its subtasks). History, comments and versions stay."""
    return items.views(session, [items.archive_item(session, auth.actor, item_id)])[0]


@router.post("/items/{item_id}/unarchive", response_model=ItemOut)
def unarchive_item(item_id: str, auth: WriteAuth, session: DbSession) -> dict:
    return items.views(session, [items.unarchive_item(session, auth.actor, item_id)])[0]


@router.post("/items/{item_id}/trash", response_model=ItemOut)
def trash_item(item_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Move the item (and its subtasks) to the trash: hidden everywhere, restorable, deleted
    for good after the trash period (30 days by default)."""
    return items.views(session, [trash.trash_item(session, auth.actor, item_id)])[0]


@router.post("/items/{item_id}/restore", response_model=ItemOut)
def restore_item(item_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Take the item (and the subtasks trashed with it) out of the trash."""
    return items.views(session, [trash.restore_item(session, auth.actor, item_id)])[0]


@router.post("/items/{item_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_item(item_id: str, auth: WriteAuth, session: DbSession) -> Response:
    """Delete a trashed item for good, with its subtasks. Its history stays."""
    trash.purge_item(session, auth.actor, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/items/{item_id}/split", status_code=status.HTTP_201_CREATED, response_model=list[ItemOut]
)
def split_item(item_id: str, body: SplitIn, auth: WriteAuth, session: DbSession) -> list[dict]:
    """The only way to create several subtasks at once."""
    children = [c.model_dump(exclude_none=True) for c in body.children]
    return items.views(session, items.split(session, auth.actor, item_id, children))


@router.get("/items/{item_id}/events", response_model=list[EventOut])
def item_events(item_id: str, _: ReadAuth, session: DbSession) -> list[Any]:
    """The item's full history, oldest first."""
    # A guest's copy is scrubbed of workspace agents in event_payload (service/guests.py).
    return [event_payload(e) for e in items.item_events(session, item_id)]


@router.get("/items/{item_id}/versions", response_model=list[VersionOut])
def item_versions(item_id: str, _: ReadAuth, session: DbSession) -> list[BodyVersion]:
    """Every body version, oldest first."""
    return items.body_versions(session, item_id)


@router.get("/items/{item_id}/activity", response_model=list[ActivityOut])
def item_activity(
    item_id: str,
    _: ReadAuth,
    session: DbSession,
    include: Literal["all", "comments", "events"] = "all",
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> list[dict]:
    """Comments and history in one list, oldest first: what the task window's right column
    shows. `limit` keeps the newest entries."""
    return activity.activity(session, item_id, include=include, limit=limit)


def _people(accounts: list) -> list[dict]:
    return [
        {"id": a.id, "handle": a.handle, "display_name": a.display_name, "kind": a.kind.value}
        for a in accounts
    ]


@router.get("/items/{item_id}/watchers", response_model=list[AssigneeOut])
def list_watchers(item_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    return _people(watchers.watchers_of(session, items.get_item(session, item_id).id))


@router.put("/items/{item_id}/watchers", response_model=list[AssigneeOut])
def set_watchers(item_id: str, body: WatchersIn, auth: WriteAuth, session: DbSession) -> list[dict]:
    """Replace who watches the item."""
    return _people(watchers.set_watchers(session, auth.actor, item_id, body.watchers))


@router.post("/items/{item_id}/watch", response_model=list[AssigneeOut])
def watch(
    item_id: str, auth: WriteAuth, session: DbSession, body: WatchIn | None = None
) -> list[dict]:
    """Add a watcher: yourself, or `account` (a person or agent; they are told you added them).
    A watcher hears about every change to the item (inbox `watching`, check-in `watching`, an
    `assignee_me` webhook)."""
    account = body.account if body else None
    return _people(watchers.watch(session, auth.actor, item_id, account))


@router.post("/items/{item_id}/unwatch", response_model=list[AssigneeOut])
def unwatch(
    item_id: str, auth: WriteAuth, session: DbSession, body: WatchIn | None = None
) -> list[dict]:
    """Remove a watcher: yourself, or `account`."""
    account = body.account if body else None
    return _people(watchers.unwatch(session, auth.actor, item_id, account))


@router.post("/items/{item_id}/report", response_model=ItemOut)
def report(item_id: str, body: ReportIn, auth: WriteAuth, session: DbSession) -> dict:
    """An agent says what it is doing: `working`, `blocked` or `review` put a live badge on the
    card (who, note, progress); `done` clears it and completes the item (unless
    `complete: false`); `idle` just clears it. The note is also posted as your comment.
    `blocked` + `waiting_on: alex` puts the item in that person's Today "waiting on me"."""
    item = reports.report(session, auth.actor, item_id, **body.model_dump())
    return items.views(session, [item])[0]


@router.post("/items/{item_id}/run", response_model=EventOut, status_code=status.HTTP_202_ACCEPTED)
def run(item_id: str, auth: WriteAuth, session: DbSession) -> Any:
    """Run now: ask the item's agent assignees to start on it now instead of at its run time.
    Records `item.run_requested` (`after.for` = the agents), which reaches each agent's stream
    as a `work` hint (its runner then takes this task with `POST /work/next {item_id}`) and
    webhooks. 422 when no agent is assigned or the item is done, cancelled or archived."""
    return event_payload(work.request_run(session, auth.actor, item_id))


class BriefOut(BaseModel):
    text: str
    ask: str


@router.get("/items/{item_id}/brief", response_model=BriefOut)
def get_brief(
    item_id: str,
    auth: ReadAuth,
    session: DbSession,
    ask: Annotated[
        str | None,
        Query(description="Handle a blocked agent should wait on (default: you, or the owner)"),
    ] = None,
) -> Any:
    """The task as one paste-ready prompt for an agent in another product's chat ("Copy
    brief"): title, due date, description, checklists and recent comments, then the run rule,
    which asks it to end its answer with `SLATE: done`, `SLATE: review`, `SLATE: later` (the
    rest waits for the due time) or `SLATE: blocked waiting_on <ask>`. Post that answer back as
    a comment and the line reports on the task. `ask` (a handle) defaults to you, or the
    workspace owner."""
    who = ask or brief_svc.default_ask(session, auth.actor.account_id)
    return {"text": brief_svc.brief(session, item_id, ask=who), "ask": who}
