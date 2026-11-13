"""The MCP tool table: params models, actions and their two bindings, schemas, descriptions.

One `ToolSpec` per resource; each `Action` has `local` and `rest` (the equivalent REST call,
used by stdio mode). **An action is the REST handler plus an id**: its params are the REST
request model (`with_id(ItemPatch, "id")`) or a model derived from the GET handler's query
parameters (`query_model(items_api.query_items)`), and `local` runs the REST handler itself
through `via(handler, item_id="id")`, so a field added to a REST route reaches agents without
a second copy here. `tests/test_mcp_parity.py` fails on any remaining drift. The few actions
whose local path differs from REST (batch, check-in, the work loop's `next` and `finish`,
`me`) keep a hand-written binding. `slate.mcp_server` serves this table over streamable HTTP
and stdio. See docs/mcp-server.md.
"""

from __future__ import annotations

import inspect
import json
import sys
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from fastapi import HTTPException, Response
from mcp import types as mcp_types
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model
from pydantic.fields import FieldInfo
from sqlalchemy.orm import Session

from slate.api import accounts as accounts_api
from slate.api import agents as agents_api
from slate.api import attachments as attachments_api
from slate.api import automations as automations_api
from slate.api import chat as chat_api
from slate.api import checkin as checkin_api
from slate.api import checklists as checklists_api
from slate.api import comments as comments_api
from slate.api import data as data_api
from slate.api import doc_comments as doc_comments_api
from slate.api import docs as docs_api
from slate.api import events as events_api
from slate.api import favorites as favorites_api
from slate.api import health as health_api
from slate.api import inbox as inbox_api
from slate.api import items as items_api
from slate.api import links as links_api
from slate.api import lists as lists_api
from slate.api import longpoll
from slate.api import permissions as permissions_api
from slate.api import recurrences as recurrences_api
from slate.api import reminders as reminders_api
from slate.api import search as search_api
from slate.api import sections as sections_api
from slate.api import settings as settings_api
from slate.api import tags as tags_api
from slate.api import today as today_api
from slate.api import views as views_api
from slate.api import webhooks as webhooks_api
from slate.api import work as work_api
from slate.api import workload as workload_api
from slate.api.accounts import AccountIn, AccountPatch, TokenIn, me_view
from slate.api.auth import Auth
from slate.api.automations import AutomationIn, AutomationPatch, RunNowIn
from slate.api.chat import MessageIn, MessagePatch, ReadIn, TaskIn
from slate.api.doc_comments import DocCommentAssignIn, DocCommentIn, DocCommentPatch
from slate.api.docs import (
    Base64FileIn,
    Base64RevisionIn,
    DocDuplicateIn,
    DocIn,
    DocMoveIn,
    DocPatch,
    DocTaskIn,
    PinIn,
    PromoteIn,
)
from slate.api.events import CURSOR_PATTERN
from slate.api.favorites import FavoriteIn, FavoriteOrder
from slate.api.permissions import RoleIn, ShareIn
from slate.api.recurrences import AdoptIn, RecurrenceIn, RecurrencePatch
from slate.api.reminders import ReminderIn
from slate.api.schemas import (
    ChecklistEntryIn,
    ChecklistEntryPatch,
    ChecklistIn,
    ChecklistPatch,
    CommentIn,
    ItemIn,
    ItemOut,
    ItemPatch,
    LinkIn,
    ListIn,
    ListPatch,
    ReactionIn,
    ReportIn,
    SectionIn,
    SectionPatch,
    SplitIn,
    StatusesPut,
    TagIn,
    TagMergeIn,
    TagPatch,
    WatchersIn,
    WatchIn,
)
from slate.api.views import ViewIn, ViewPatch
from slate.api.webhooks import WebhookIn, WebhookPatch
from slate.api.work import FinishIn, FinishOut, HeartbeatIn, NextIn, NextOut
from slate.api.workload import CapacityIn
from slate.batch import Later
from slate.service import items, work
from slate.service.accounts import Principal
from slate.service.core import Actor, jsonable

RULE = "Slate rule: create makes exactly one item; use split for subtasks."


class ToolError(Exception):
    """A failure the calling agent should read: returned as an `isError` tool result."""


# --- the REST bridge: an action is the REST handler plus an id ------------------------------


def _camel(name: str) -> str:
    return "".join(part.title() for part in name.split("_"))


def with_id(
    model: type[BaseModel],
    name: str | None = "id",
    description: str = "",
    **more: tuple[Any, FieldInfo],
) -> type[BaseModel]:
    """`model` (a REST request model) made strict, plus a required string field `name` (the
    route's path id) and any `more` fields given as `name=(type, Field(...))`."""
    ns: dict[str, Any] = {
        "model_config": ConfigDict(extra="forbid"),
        "__module__": __name__,
        "__annotations__": {},
    }
    if name:
        ns["__annotations__"][name] = str
        ns[name] = Field(description=description)
    for key, (kind, info) in more.items():
        ns["__annotations__"][key] = kind
        ns[key] = info
    return type(f"{model.__name__}{_camel(name or '')}", (model,), ns)


def strict(model: type[BaseModel]) -> type[BaseModel]:
    """A REST request model as MCP params: the same fields, unknown ones refused."""
    return with_id(model, None)


def _route_of(fn: Callable[..., Any]) -> Any:
    for route in sys.modules[fn.__module__].router.routes:
        if getattr(route, "endpoint", None) is fn:
            return route
    raise LookupError(f"{fn.__qualname__} is not a route")


def query_model(fn: Callable[..., Any], **overrides: Any) -> type[BaseModel]:
    """A params model from a GET handler's query parameters: the same names, types, bounds,
    defaults and descriptions (aliases dropped). `overrides` replace or add fields as
    `name=(type, Field(...))`; `name=None` leaves one out."""
    fields: dict[str, Any] = {}
    for param in _route_of(fn).dependant.query_params:
        info = FieldInfo.merge_field_infos(
            param.field_info, alias=None, validation_alias=None, serialization_alias=None
        )
        fields[param.name] = (param.field_info.annotation, info)
    for key, value in overrides.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    return create_model(
        f"{_camel(fn.__name__)}Query",
        __config__=ConfigDict(extra="forbid"),
        __module__=__name__,
        **fields,
    )


def _unwrap(annotation: Any) -> Any:
    """The type behind `Annotated[...]` and `X | None`."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _unwrap(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        real = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(real) == 1:
            return _unwrap(real[0])
    return annotation


def _body(model: type[BaseModel], p: BaseModel) -> BaseModel:
    """The handler's body model from the MCP params: the same instance, the REST base of a
    `with_id` model (validated already; the id left out), or the overlapping fields of a
    narrow params model."""
    if type(p) is model:
        return p
    keep = {k: getattr(p, k) for k in model.model_fields if k in p.model_fields_set}
    if isinstance(p, model):
        return model.model_construct(_fields_set=set(keep), **keep)
    return model.model_validate(keep)


def _answer(lc: Local, route: Any, adapter: TypeAdapter | None, result: Any, empty: Any) -> Any:
    """A handler's return value as the JSON the REST route would send."""
    if isinstance(result, Response):
        if result.status_code == 204 or not result.body:
            return empty
        return json.loads(result.body)
    if route.status_code == 204:
        return empty
    if adapter is None:
        return jsonable(result)
    out = adapter.dump_python(
        adapter.validate_python(result, from_attributes=True), mode="json", by_alias=True
    )
    if lc.later is not None and route.response_model in (ItemOut, list[ItemOut]):
        # A batch with views "final": answer with marks built once after the commit.
        marks = [Later(v["id"]) for v in (out if isinstance(out, list) else [out])]
        lc.later.extend(marks)
        return marks if isinstance(out, list) else marks[0]
    return out


def via(
    fn: Callable[..., Any], *, empty: Callable[[Any], Any] | None = None, **binds: Any
) -> Callable[[Local, Any], Any]:
    """The local binding that runs REST handler `fn` as the MCP caller.

    Each handler parameter is filled by its annotation: the `Auth` from the caller's principal
    and actor, the `Session`, the body model from the params (`_body`), and every other (path
    or query) parameter from the params field of the same name, or as `binds` says: a field
    name (`item_id="id"`) or a function of the params (`fields=lambda p: ...`). The answer
    is rendered with the route's `response_model`; a 204 route answers `empty(params)`.
    """
    route = _route_of(fn)
    hints = typing.get_type_hints(fn, include_extras=True)
    plan: list[tuple[str, str, Any]] = []
    for name, param in inspect.signature(fn).parameters.items():
        kind = _unwrap(hints[name])
        if kind is Auth:
            plan.append((name, "auth", None))
        elif kind is Session:
            plan.append((name, "session", None))
        elif inspect.isclass(kind) and issubclass(kind, BaseModel):
            plan.append((name, "body", kind))
        else:
            plan.append((name, "value", (binds.get(name, name), param.default)))
    adapter = TypeAdapter(route.response_model) if route.response_model is not None else None

    def local(lc: Local, p: Any) -> Any:
        kwargs: dict[str, Any] = {}
        for name, how, what in plan:
            if how == "auth":
                kwargs[name] = Auth(principal=lc.principal, actor=lc.actor)
            elif how == "session":
                kwargs[name] = lc.session
            elif how == "body":
                kwargs[name] = _body(what, p)
            else:
                source, default = what
                if callable(source):
                    kwargs[name] = source(p)
                elif source in type(p).model_fields:
                    kwargs[name] = getattr(p, source)
                elif default is inspect.Parameter.empty:
                    raise TypeError(f"{fn.__qualname__} needs {name}; no param feeds it")
        try:
            result = fn(**kwargs)
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from None
        return _answer(lc, route, adapter, result, empty(p) if empty else None)

    return local


def _joined(name: str) -> Callable[[Any], str | None]:
    """A list param as the comma-separated query value the REST handler takes."""
    return lambda p: ",".join(getattr(p, name)) if getattr(p, name) else None


# --- params ----------------------------------------------------------------------------------


class P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoParams(P):
    pass


class EventRef(P):
    event_id: str = Field(description="The event's id (from history, slate_events or slate_audit)")


class ItemRef(P):
    id: str = Field(description="Item id")


ITEM_ID = (str, Field(description="Item id"))
FIELDS = (
    list[str] | None,
    Field(default=None, description="Item fields to return (id always), e.g. [title, due_at]"),
)
ItemQuery = query_model(
    items_api.query_items,
    fields=FIELDS,
    ids=(
        list[str] | None,
        Field(default=None, max_length=1000, description="Only these item ids (at most 1000)"),
    ),
)


def _items_query(p: Any) -> dict[str, Any]:
    query = _fields(p, "fields", "ids")
    if p.fields:
        query["fields"] = ",".join(p.fields)
    if getattr(p, "ids", None):
        query["ids"] = ",".join(p.ids)
    return query


# Narrow actions: one intent, one field, on the same PATCH route as `update`.
class ItemStatus(ItemRef):
    status: str = Field(description="Status id or name in the item's list")


class ItemAssign(ItemRef):
    assignees: list[str] = Field(description="Account handles or ids; replaces the whole set")


class ItemTags(ItemRef):
    tags: list[str] = Field(description="Tag names; replaces the whole set, new tags are created")


class ItemComment(ItemRef):
    body_md: str = Field(min_length=1, description="Markdown")


class AttachmentRef(P):
    id: str = Field(description="Attachment id")


class CommentRef(P):
    id: str = Field(description="Comment id")


class CommentEdit(CommentRef):
    body_md: str = Field(min_length=1, description="Markdown; @handle mentions an account")


class CommentAssign(CommentRef):
    assignee: str | None = Field(description="Handle or id; null takes it off")


CHANNEL_DOC = "Channel: general, list:<list id>, section:<section id> or agent:<agent id>"
CHANNEL = (str, Field(description=CHANNEL_DOC))


class MessageRef(P):
    id: str = Field(description="Message id")


class ChecklistRef(P):
    id: str = Field(description="Checklist id")


class ChecklistEntryRef(P):
    entry_id: str = Field(description="Checklist entry id")


class TagRef(P):
    tag: str = Field(description="Tag name or id")


class ListRef(P):
    id: str = Field(description="List id")


class AccountRef(P):
    account_id: str = Field(description="Account id (from `list` or `me`)")


class TokenRef(P):
    token_id: str = Field(description="Token id (`me` shows yours)")


# A long-poll through MCP stays under the stdio bridge's 30 s HTTP timeout.
MCP_WAIT = 25


class CheckinQuery(P):
    since: str | None = Field(
        default=None,
        pattern=CURSOR_PATTERN,
        description="`next_since` from your last check-in; omit for the latest news",
    )
    limit: int = Field(default=50, ge=1, le=500, description="Most news entries")
    queue_limit: int = Field(default=50, ge=0, le=1000, description="Most queue items (0: none)")
    fields: list[str] | None = Field(
        default=None, description="Queue item fields to return (id always), e.g. [title, due_at]"
    )
    wait: int = Field(
        default=0,
        ge=0,
        le=MCP_WAIT,
        description="With since: wait up to this many seconds for news (long-poll)",
    )


class WorkNext(NextIn):
    wait: int = Field(
        default=0, ge=0, le=MCP_WAIT, description="Seconds to wait for a task when none is free"
    )


class WorkFinish(FinishIn):
    item_id: str = Field(description="The task you hold")
    # A plain object here: a nested model would put a $ref in the tool's input schema.
    next: dict[str, Any] | None = Field(
        default=None,
        description="Also take your next task: the params of `next` (`{}` for the defaults)",
    )


class RecurrenceRef(P):
    id: str = Field(description="Recurrence rule id")


class WebhookRef(P):
    id: str = Field(description="Webhook id")


class ShareScope(P):
    scope_kind: Literal["section", "list"]
    scope_id: str


class PrivacySet(ShareScope):
    private: bool


class ViewRef(P):
    id: str


class FavoriteRef(P):
    id: str = Field(description="Favorite id (not the target's id)")


class AutomationRef(P):
    id: str = Field(description="Automation id")


class AutomationRuns(P):
    id: str | None = Field(default=None, description="Automation id; omit for every rule")
    limit: int = Field(default=50, ge=1, le=500)


class DocRefP(P):
    id: str = Field(description="Doc id")


class DocVersionRef(P):
    id: str = Field(description="Doc id")
    version_no: int = Field(ge=1, description="Version number")


class DocFileRef(P):
    file_id: str = Field(description="Doc file id")


class DocRevisionRef(P):
    id: str = Field(description="Doc id")
    revision_no: int = Field(ge=1, description="Revision number")


class SectionDocs(P):
    section_id: str = Field(description="Section id")


class DocCompare(P):
    id: str = Field(description="Doc id (a file doc)")
    from_revision: int = Field(ge=1, description="Older revision number")
    to_revision: int | None = Field(default=None, ge=1, description="Default: the current one")


class DocCommentRef(P):
    comment_id: str = Field(description="Doc comment id")


class ReminderItem(P):
    item_id: str = Field(description="Item id")


class ReminderRef(P):
    id: str = Field(description="Reminder id")


# --- the action table ------------------------------------------------------------------------


@dataclass
class Local:
    """What a local action runs with: an open session, the caller, and the change actor."""

    session: Session
    principal: Principal
    actor: Actor
    # Set by a batch (`slate.batch`): item views are left as `Later` marks and built once,
    # after the commit, for every op together.
    later: list[Later] | None = None


@dataclass(frozen=True)
class Rest:
    method: str
    path: str  # under /api/v1
    query: dict[str, Any] | None = None
    body: Any = None
    empty: Any = None  # the result when the API answers 204


@dataclass(frozen=True)
class Action:
    doc: str
    scope: str  # read | write | admin
    params: type[BaseModel]
    rest: Callable[[Any], Rest]
    local: Callable[[Local, Any], Any] | None = None  # None: REST-only (stdio mode)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    about: str  # two sentences: what it is and when to use it
    example: dict[str, Any]
    actions: dict[str, Action]
    read_only: bool = False
    destructive: bool = False
    missing: str | None = None  # error text for an action with no local binding


def _dump(model: type[BaseModel], value: Any) -> Any:
    return model.model_validate(value, from_attributes=True).model_dump(mode="json")


def _item_views(lc: Local, rows: list, *, now: bool = False) -> list[dict]:
    if lc.later is not None and not now:
        marks = [Later(row.id) for row in rows]
        lc.later.extend(marks)
        return marks  # type: ignore[return-value]
    return [_dump(ItemOut, v) for v in items.views(lc.session, rows)]


def _fields(p: BaseModel, *drop: str, unset: bool = False) -> dict[str, Any]:
    """Params as a dict without the ref fields; `unset=True` keeps only what the caller sent."""
    data = p.model_dump(exclude_unset=True) if unset else p.model_dump(exclude_none=True)
    return {k: v for k, v in data.items() if k not in drop}


def _q(value: str) -> str:
    return quote(value, safe="")


def _deleted(key: str, field: str = "id") -> Callable[[Any], dict[str, Any]]:
    return lambda p: {key: getattr(p, field)}


# Items --------------------------------------------------------------------------------------

ITEM_ACTIONS = {
    "get": Action(
        "the item with subtasks, comments and last 20 events",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}"),
        local=via(items_api.get_item, item_id="id"),
    ),
    "list": Action(
        "items matching every filter given (AND); `fields` trims each item, `changed_since` "
        "returns only items changed after an event id, `limit`/`offset` page",
        "read",
        ItemQuery,
        rest=lambda p: Rest("GET", "/items", query=_items_query(p)),
        local=via(items_api.query_items, fields=_joined("fields"), ids=_joined("ids")),
    ),
    "create": Action(
        "file ONE item in list_id (parent_id instead makes it one subtask)",
        "write",
        strict(ItemIn),
        rest=lambda p: Rest("POST", "/items", body=_fields(p)),
        local=via(items_api.create_item),
    ),
    "update": Action(
        "change any fields; null clears; assignees/tags replace the set",
        "write",
        with_id(ItemPatch, "id", "Item id"),
        rest=lambda p: Rest("PATCH", f"/items/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(items_api.update_item, item_id="id"),
    ),
    "set_status": Action(
        "move to a status of its list (done stamps completed_at)",
        "write",
        ItemStatus,
        rest=lambda p: Rest("PATCH", f"/items/{_q(p.id)}", body={"status": p.status}),
        local=via(items_api.update_item, item_id="id"),
    ),
    "assign": Action(
        "set who it is assigned to (replaces the set; to take an item yourself, `report` with "
        "assign_me)",
        "write",
        ItemAssign,
        rest=lambda p: Rest("PATCH", f"/items/{_q(p.id)}", body={"assignees": p.assignees}),
        local=via(items_api.update_item, item_id="id"),
    ),
    "tag": Action(
        "set its tags",
        "write",
        ItemTags,
        rest=lambda p: Rest("PATCH", f"/items/{_q(p.id)}", body={"tags": p.tags}),
        local=via(items_api.update_item, item_id="id"),
    ),
    "report": Action(
        "say what you are doing on it: state working / blocked / review shows a live badge on "
        "the card (with note and progress); done clears it and completes the item; idle clears "
        "it. The note is also posted as your comment",
        "write",
        with_id(ReportIn, "id", "Item id"),
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/report", body=_fields(p, "id", unset=True)),
        local=via(items_api.report, item_id="id"),
    ),
    "run": Action(
        "Run now: ask the item's agent assignees to start on it now (records "
        "item.run_requested; their runners take it at once). Refused when no agent is assigned "
        "or it is done, cancelled or archived",
        "write",
        ItemRef,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/run"),
        local=via(items_api.run, item_id="id"),
    ),
    "brief": Action(
        "the task as one paste-ready prompt (title, due, description, checklists, recent "
        "comments) ending with the run rule: finish with a last line `SLATE: done`, `SLATE: "
        "review`, `SLATE: later` or `SLATE: blocked waiting_on <handle>`; a comment ending so "
        "reports back",
        "read",
        query_model(items_api.get_brief, id=ITEM_ID),
        rest=lambda p: Rest(
            "GET", f"/items/{_q(p.id)}/brief", query={"ask": p.ask} if p.ask else None
        ),
        local=via(items_api.get_brief, item_id="id"),
    ),
    "comment": Action(
        "add a comment",
        "write",
        ItemComment,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/comments", body={"body_md": p.body_md}),
        local=via(comments_api.add_comment, item_id="id"),
    ),
    "bulk_update": Action(
        "apply one change to many items at once (all or nothing; up to 500)",
        "write",
        strict(items_api.BulkIn),
        rest=lambda p: Rest("POST", "/items/bulk", body={"ids": p.ids, "changes": p.changes}),
        local=via(items_api.bulk_update),
    ),
    "archive": Action(
        "hide it and its subtasks (soft; history stays)",
        "write",
        ItemRef,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/archive"),
        local=via(items_api.archive_item, item_id="id"),
    ),
    "unarchive": Action(
        "bring it back",
        "write",
        ItemRef,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/unarchive"),
        local=via(items_api.unarchive_item, item_id="id"),
    ),
    "trash": Action(
        "move it (and its subtasks) to the trash: hidden, restorable, deleted for good after "
        "the trash period (30 days by default)",
        "write",
        ItemRef,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/trash"),
        local=via(items_api.trash_item, item_id="id"),
    ),
    "restore": Action(
        "take it out of the trash",
        "write",
        ItemRef,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/restore"),
        local=via(items_api.restore_item, item_id="id"),
    ),
    "purge": Action(
        "delete a trashed item for good (history stays)",
        "write",
        ItemRef,
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/purge", empty={"purged": p.id}),
        local=via(items_api.purge_item, item_id="id", empty=_deleted("purged")),
    ),
    "split": Action(
        "create several subtasks under it: the only multi-child path",
        "write",
        with_id(SplitIn, "id", "Item id"),
        rest=lambda p: Rest(
            "POST",
            f"/items/{_q(p.id)}/split",
            body={"children": [c.model_dump(exclude_none=True) for c in p.children]},
        ),
        local=via(items_api.split_item, item_id="id"),
    ),
    "delete": Action(
        "undo your own create within 10 minutes; otherwise archive",
        "write",
        ItemRef,
        rest=lambda p: Rest("DELETE", f"/items/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(items_api.delete_item, item_id="id", empty=_deleted("deleted")),
    ),
    "link": Action(
        "add a link: kind blocks / blocked_by / relates / duplicate_of / duplicated_by, from this "
        "item's side",
        "write",
        with_id(LinkIn, "id", "Item id"),
        rest=lambda p: Rest(
            "POST", f"/items/{_q(p.id)}/links", body={"kind": p.kind, "item_id": p.item_id}
        ),
        local=via(links_api.add_link, item_id="id"),
    ),
    "unlink": Action(
        "remove a dependency (same kind and item_id as when added)",
        "write",
        with_id(LinkIn, "id", "Item id"),
        rest=lambda p: Rest(
            "DELETE", f"/items/{_q(p.id)}/links", query={"kind": p.kind, "other": p.item_id}
        ),
        local=via(links_api.remove_link, item_id="id", other="item_id"),
    ),
    "activity": Action(
        "comments and history in one list, oldest first (what people see beside the task)",
        "read",
        query_model(items_api.item_activity, id=ITEM_ID),
        rest=lambda p: Rest(
            "GET", f"/items/{_q(p.id)}/activity", query=_fields(p, "id", unset=True)
        ),
        local=via(items_api.item_activity, item_id="id"),
    ),
    "watch": Action(
        "add a watcher (you, or `account`): watchers hear about every change to the item "
        "(check-in `watching`, inbox, an assignee_me webhook)",
        "write",
        with_id(WatchIn, "id", "Item id"),
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/watch", body=_fields(p, "id")),
        local=via(items_api.watch, item_id="id"),
    ),
    "unwatch": Action(
        "remove a watcher (you, or `account`)",
        "write",
        with_id(WatchIn, "id", "Item id"),
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/unwatch", body=_fields(p, "id")),
        local=via(items_api.unwatch, item_id="id"),
    ),
    "watchers": Action(
        "who follows the item",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}/watchers"),
        local=via(items_api.list_watchers, item_id="id"),
    ),
    "set_watchers": Action(
        "replace who follows the item (handles or ids)",
        "write",
        with_id(WatchersIn, "id", "Item id"),
        rest=lambda p: Rest("PUT", f"/items/{_q(p.id)}/watchers", body=_fields(p, "id")),
        local=via(items_api.set_watchers, item_id="id"),
    ),
    "attachments": Action(
        "the item's files: id, filename, size, content type",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}/attachments"),
        local=via(attachments_api.list_attachments, item_id="id"),
    ),
    "detach": Action(
        "remove one attachment by its id",
        "write",
        AttachmentRef,
        rest=lambda p: Rest("DELETE", f"/attachments/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(attachments_api.remove, attachment_id="id", empty=_deleted("deleted")),
    ),
    "attach": Action(
        "attach a file (base64) to the item",
        "write",
        with_id(attachments_api.Base64In, "id", "Item id"),
        rest=lambda p: Rest("POST", f"/items/{_q(p.id)}/attachments/base64", body=_fields(p, "id")),
        local=via(attachments_api.add_attachment_base64, item_id="id"),
    ),
    "promote_attachment": Action(
        "move an attachment into Docs & Files: a file doc with the bytes as revision 1; "
        "the task gets a comment linking it",
        "write",
        with_id(PromoteIn, "id", "Attachment id"),
        rest=lambda p: Rest(
            "POST", f"/attachments/{_q(p.id)}/promote", body=jsonable(_fields(p, "id"))
        ),
        local=via(docs_api.promote_attachment, attachment_id="id"),
    ),
    "history": Action(
        "every event on the item, oldest first",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}/events"),
        local=via(items_api.item_events, item_id="id"),
    ),
    "versions": Action(
        "every body version, oldest first",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}/versions"),
        local=via(items_api.item_versions, item_id="id"),
    ),
    "revert": Action(
        "undo one change from the history (an item.updated field, item.status_changed, "
        "item.assigned or item.moved event) by issuing its inverse as you: a new event, "
        "session_ref revert:<event_id>. Refused when the field has changed again since",
        "write",
        EventRef,
        rest=lambda p: Rest("POST", f"/events/{_q(p.event_id)}/revert"),
        local=via(events_api.revert_event),
    ),
}

# Lists, sections, tags ----------------------------------------------------------------------

LIST_ACTIONS = {
    "list": Action(
        "lists with their statuses",
        "read",
        query_model(lists_api.list_lists),
        rest=lambda p: Rest("GET", "/lists", query=_fields(p)),
        local=via(lists_api.list_lists),
    ),
    "create": Action(
        "a list in a section (default statuses open, active, done)",
        "write",
        strict(ListIn),
        rest=lambda p: Rest("POST", "/lists", body=_fields(p)),
        local=via(lists_api.create_list),
    ),
    "update": Action(
        "rename, reorder, set default_status, or archived true/false",
        "write",
        with_id(ListPatch, "id", "List id"),
        rest=lambda p: Rest("PATCH", f"/lists/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(lists_api.update_list, list_id="id"),
    ),
    "set_statuses": Action(
        "replace the statuses (columns) in order; fallback for stranded items",
        "write",
        with_id(StatusesPut, "id", "List id"),
        rest=lambda p: Rest(
            "PUT", f"/lists/{_q(p.id)}/statuses", body=p.model_dump(exclude={"id"})
        ),
        local=via(lists_api.set_statuses, list_id="id"),
    ),
}

SECTION_ACTIONS = {
    "list": Action(
        "all sections",
        "read",
        query_model(sections_api.list_sections),
        rest=lambda p: Rest("GET", "/sections", query=_fields(p)),
        local=via(sections_api.list_sections),
    ),
    "create": Action(
        "a section (one per audience or rhythm, never per topic)",
        "write",
        strict(SectionIn),
        rest=lambda p: Rest("POST", "/sections", body=_fields(p)),
        local=via(sections_api.create_section),
    ),
    "update": Action(
        "rename, reorder, change audience, or archived true/false",
        "write",
        with_id(SectionPatch, "id", "Section id"),
        rest=lambda p: Rest("PATCH", f"/sections/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(sections_api.update_section, section_id="id"),
    ),
}

TAG_ACTIONS = {
    "list": Action(
        "all tags with item_count",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/tags"),
        local=via(tags_api.list_tags),
    ),
    "create": Action(
        "a tag (stored lowercase)",
        "write",
        strict(TagIn),
        rest=lambda p: Rest("POST", "/tags", body=_fields(p)),
        local=via(tags_api.create_tag),
    ),
    "rename": Action(
        "rename or recolor a tag; items keep it",
        "write",
        with_id(TagPatch, "tag", "Current tag name or id"),
        rest=lambda p: Rest("PATCH", f"/tags/{_q(p.tag)}", body=_fields(p, "tag", unset=True)),
        local=via(tags_api.update_tag),
    ),
    "merge": Action(
        "fold a tag into another: items and rules carry `into` instead; the tag is deleted",
        "write",
        with_id(TagMergeIn, "tag", "The tag (name or id) to fold away"),
        rest=lambda p: Rest("POST", f"/tags/{_q(p.tag)}/merge", body={"into": p.into}),
        local=via(tags_api.merge_tag),
    ),
    "delete": Action(
        "remove a tag from every item and rule, then delete it",
        "write",
        TagRef,
        rest=lambda p: Rest("DELETE", f"/tags/{_q(p.tag)}", empty={"deleted": p.tag}),
        local=via(tags_api.delete_tag, empty=_deleted("deleted", "tag")),
    ),
}

# Recurrences --------------------------------------------------------------------------------

RECURRENCE_ACTIONS = {
    "preview": Action(
        "try an RRULE without saving: next dates and the rule in plain English",
        "read",
        query_model(recurrences_api.preview),
        rest=lambda p: Rest("GET", "/recurrences/preview", query=_fields(p)),
        local=via(recurrences_api.preview),
    ),
    "upcoming": Action(
        "dates scheduled rules will fill in a window that are not items yet",
        "read",
        query_model(recurrences_api.upcoming),
        rest=lambda p: Rest(
            "GET", "/recurrences/upcoming", query=p.model_dump(mode="json", exclude_none=True)
        ),
        local=via(recurrences_api.upcoming),
    ),
    "list": Action(
        "recurrence rules, optionally in one list",
        "read",
        query_model(recurrences_api.list_recurrences),
        rest=lambda p: Rest("GET", "/recurrences", query=_fields(p)),
        local=via(recurrences_api.list_recurrences),
    ),
    "get": Action(
        "one rule with its next occurrences",
        "read",
        RecurrenceRef,
        rest=lambda p: Rest("GET", f"/recurrences/{_q(p.id)}"),
        local=via(recurrences_api.get_recurrence, rule_id="id"),
    ),
    "create": Action(
        "a rule that spawns one item per occurrence",
        "write",
        strict(RecurrenceIn),
        rest=lambda p: Rest("POST", "/recurrences", body=_fields(p)),
        local=via(recurrences_api.create_recurrence),
    ),
    "update": Action(
        "change a rule (`ended: true` ends it for good)",
        "write",
        with_id(RecurrencePatch, "id", "Recurrence rule id"),
        rest=lambda p: Rest("PATCH", f"/recurrences/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(recurrences_api.update_recurrence, rule_id="id"),
    ),
    "pause": Action(
        "stop spawning",
        "write",
        RecurrenceRef,
        rest=lambda p: Rest("POST", f"/recurrences/{_q(p.id)}/pause"),
        local=via(recurrences_api.pause_recurrence, rule_id="id"),
    ),
    "resume": Action(
        "start spawning again from today",
        "write",
        RecurrenceRef,
        rest=lambda p: Rest("POST", f"/recurrences/{_q(p.id)}/resume"),
        local=via(recurrences_api.resume_recurrence, rule_id="id"),
    ),
    "adopt": Action(
        "make an existing item the rule's current occurrence (it keeps its due date)",
        "write",
        with_id(AdoptIn, "id", "Recurrence rule id"),
        rest=lambda p: Rest("POST", f"/recurrences/{_q(p.id)}/adopt", body={"item_id": p.item_id}),
        local=via(recurrences_api.adopt, rule_id="id"),
    ),
    "run_now": Action(
        "spawn everything due within the rule's lead window now",
        "write",
        RecurrenceRef,
        rest=lambda p: Rest("POST", f"/recurrences/{_q(p.id)}/run-now"),
        local=via(recurrences_api.run_now, rule_id="id"),
    ),
}

# Webhooks: every action needs `write`, like /api/v1/webhooks ---------------------------------

WEBHOOK_ACTIONS = {
    "list": Action(
        "your webhooks (an admin sees everyone's)",
        "write",
        NoParams,
        rest=lambda p: Rest("GET", "/webhooks"),
        local=via(webhooks_api.list_webhooks),
    ),
    "create": Action(
        "subscribe a URL to event types; the reply holds the signing secret, shown only once",
        "write",
        strict(WebhookIn),
        rest=lambda p: Rest("POST", "/webhooks", body=_fields(p)),
        local=via(webhooks_api.create_webhook),
    ),
    "update": Action(
        "change url, events, filter (replaced whole) or disabled",
        "write",
        with_id(WebhookPatch, "id", "Webhook id"),
        rest=lambda p: Rest("PATCH", f"/webhooks/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(webhooks_api.update_webhook, webhook_id="id"),
    ),
    "deliveries": Action(
        "the delivery log, newest first",
        "write",
        query_model(webhooks_api.list_deliveries, id=(str, Field(description="Webhook id"))),
        rest=lambda p: Rest("GET", f"/webhooks/{_q(p.id)}/deliveries", query=_fields(p, "id")),
        local=via(webhooks_api.list_deliveries, webhook_id="id"),
    ),
    "test": Action(
        "send a webhook.test delivery now (one attempt) and return its outcome",
        "write",
        WebhookRef,
        rest=lambda p: Rest("POST", f"/webhooks/{_q(p.id)}/test"),
        local=via(webhooks_api.test_webhook, webhook_id="id"),
    ),
}

# Accounts, sharing, audit -------------------------------------------------------------------


def _me(lc: Local) -> dict:
    # GET /me also renews a browser session cookie; the MCP side only needs the view.
    return me_view(lc.session, lc.principal).model_dump(mode="json")


ACCOUNT_ID = (str, Field(description="Account id (from `list` or `me`)"))

ACCOUNT_ACTIONS = {
    "me": Action(
        "who this token is, and its scopes",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/me"),
        local=lambda lc, p: _me(lc),
    ),
    "list": Action(
        "all accounts (admin)",
        "admin",
        NoParams,
        rest=lambda p: Rest("GET", "/accounts"),
        local=via(accounts_api.list_accounts),
    ),
    "create": Action(
        "a person or agent account (admin)",
        "admin",
        strict(AccountIn),
        rest=lambda p: Rest("POST", "/accounts", body=_fields(p)),
        local=via(accounts_api.create_account),
    ),
    "create_token": Action(
        "issue a token; its plaintext is shown only in this result (admin)",
        "admin",
        with_id(TokenIn, "account_id", "Account id"),
        rest=lambda p: Rest(
            "POST", f"/accounts/{_q(p.account_id)}/tokens", body=_fields(p, "account_id")
        ),
        local=via(accounts_api.create_token),
    ),
    "update": Action(
        "rename an account, or disable it (`disabled: true` stops its tokens at once) (admin)",
        "admin",
        with_id(AccountPatch, "account_id", "Account id"),
        rest=lambda p: Rest(
            "PATCH", f"/accounts/{_q(p.account_id)}", body=_fields(p, "account_id", unset=True)
        ),
        local=via(accounts_api.update_account),
    ),
    "tokens": Action(
        "an account's tokens with last use, never the secret (admin, or your own)",
        "read",
        query_model(accounts_api.list_tokens, account_id=ACCOUNT_ID),
        rest=lambda p: Rest(
            "GET",
            f"/accounts/{_q(p.account_id)}/tokens",
            query={"include_revoked": p.include_revoked},
        ),
        local=via(accounts_api.list_tokens),
    ),
    "usage": Action(
        "events per day by source, busiest event types, requests per token (admin, or yours)",
        "read",
        query_model(accounts_api.account_usage, account_id=ACCOUNT_ID),
        rest=lambda p: Rest("GET", f"/accounts/{_q(p.account_id)}/usage", query={"days": p.days}),
        local=via(accounts_api.account_usage),
    ),
    "revoke_token": Action(
        "revoke a token at once (admin, or your own token)",
        "read",
        TokenRef,
        rest=lambda p: Rest("DELETE", f"/tokens/{_q(p.token_id)}", empty={"revoked": True}),
        local=via(accounts_api.revoke_token, empty=lambda p: {"revoked": True}),
    ),
    "set_role": Action(
        "change an account's role: owner, admin, member, guest (admin; admins only by the owner)",
        "admin",
        with_id(RoleIn, "account_id", "Account id (from `list` or `me`)"),
        rest=lambda p: Rest("PUT", f"/accounts/{_q(p.account_id)}/role", body={"role": p.role}),
        local=via(permissions_api.set_role),
    ),
    "access": Action(
        "every section and list an account reaches, with its level (admin, or yourself)",
        "read",
        AccountRef,
        rest=lambda p: Rest("GET", f"/accounts/{_q(p.account_id)}/access"),
        local=via(permissions_api.account_access),
    ),
    "agent_rules": Action(
        "what agent accounts may never do here, whatever their token (admin)",
        "admin",
        NoParams,
        rest=lambda p: Rest("GET", "/settings/agents"),
        local=via(settings_api.get_agent_rules),
    ),
    "set_agent_rules": Action(
        "replace what agents may never do: `denied` lists action keys (owner or admin person)",
        "admin",
        strict(settings_api.AgentRulesIn),
        rest=lambda p: Rest("PUT", "/settings/agents", body=_fields(p)),
        local=via(settings_api.set_agent_rules),
    ),
}


def _scope_path(kind: str, scope_id: str) -> str:
    return f"/{'sections' if kind == 'section' else 'lists'}/{_q(scope_id)}"


_who = {
    "section": via(permissions_api.section_access, section_id="scope_id"),
    "list": via(permissions_api.list_access, list_id="scope_id"),
}
_private = {
    "section": via(sections_api.update_section, section_id="scope_id"),
    "list": via(lists_api.update_list, list_id="scope_id"),
}

SHARE_ACTIONS = {
    "list": Action(
        "shares, by account and/or section/list (admin)",
        "admin",
        query_model(permissions_api.list_shares),
        rest=lambda p: Rest("GET", "/shares", query=_fields(p)),
        local=via(permissions_api.list_shares),
    ),
    "set": Action(
        "share a section or list with an account at view, comment or edit; level null revokes "
        "(admin)",
        "admin",
        strict(ShareIn),
        rest=lambda p: Rest("PUT", "/shares", body=p.model_dump()),
        local=via(permissions_api.put_share),
    ),
    "who": Action(
        "everyone who reaches a section or list, at what level and why (admin)",
        "admin",
        ShareScope,
        rest=lambda p: Rest("GET", f"{_scope_path(p.scope_kind, p.scope_id)}/access"),
        local=lambda lc, p: _who[p.scope_kind](lc, p),
    ),
    "private": Action(
        "make a section or list private (members stop seeing it unless shared) or public (admin)",
        "write",
        PrivacySet,
        rest=lambda p: Rest(
            "PATCH", _scope_path(p.scope_kind, p.scope_id), body={"private": p.private}
        ),
        local=lambda lc, p: _private[p.scope_kind](lc, p),
    ),
}

AUDIT_ACTIONS = {
    "list": Action(
        "who (person or agent) did what, when, through which door; newest first, filterable; "
        "pass next_before back as before",
        "read",
        query_model(permissions_api.audit_log),
        rest=lambda p: Rest("GET", "/audit", query=_fields(p)),
        local=via(permissions_api.audit_log),
    ),
    "facets": Action(
        "event types and actors in the log you can see",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/audit/facets"),
        local=via(permissions_api.audit_facets),
    ),
}

# Today, check-in, work, health, agents, events ----------------------------------------------

TODAY_ACTIONS = {
    "get": Action(
        "overdue, due today, waiting on you (`fields` trims each item)",
        "read",
        query_model(today_api.get_today, fields=FIELDS),
        rest=lambda p: Rest(
            "GET", "/today", query={"fields": ",".join(p.fields)} if p.fields else None
        ),
        local=via(today_api.get_today, fields=_joined("fields")),
    )
}


def _checkin(lc: Local, p: CheckinQuery) -> dict:
    try:
        wanted = items_api.parse_fields(",".join(p.fields)) if p.fields else None
    except HTTPException as exc:
        raise ToolError(exc.detail) from None
    account = lc.principal.account

    def attempt() -> dict | None:
        out = checkin_api.build(lc.session, account, p.since, p.limit, p.queue_limit)
        return out if out["news"] or p.since is None or not p.wait else None

    out = longpoll.wait_sync(attempt, p.wait, lc.session) or checkin_api.build(
        lc.session, account, p.since, p.limit, p.queue_limit
    )
    return json.loads(checkin_api.dump(out, wanted))


CHECKIN_ACTIONS = {
    "get": Action(
        "your open items (queue) and others' events that concern you (news, with why), "
        "from a cursor",
        "read",
        CheckinQuery,
        rest=lambda p: Rest("GET", "/checkin", query=_items_query(p)),
        local=_checkin,
    )
}


def _take_next(lc: Local, p: NextIn) -> dict:
    got = longpoll.wait_sync(
        lambda: work_api.take_next(lc.session, lc.actor, p), p.wait, lc.session
    )
    return got or work_api.nothing()


def _work_finish(lc: Local, p: WorkFinish) -> dict:
    item = work.finish(lc.session, lc.actor, p.item_id, **_fields(p, "item_id", "next"))
    nxt = None
    if p.next is not None and not lc.session.info.get("work_finish_replayed"):
        try:
            nxt = _take_next(lc, WorkNext.model_validate(p.next))
        except ValidationError as exc:
            raise ToolError(f"next: {exc}") from None
    return _dump(FinishOut, {"item": items.views(lc.session, [item])[0], "next": nxt})


WORK_ACTIONS = {
    "reconcile": Action(
        "confirm remote termination with run proof and actual runtime evidence; "
        "unlock without completing",
        "write",
        with_id(work_api.ReconcileIn, "item_id", "The task you hold"),
        rest=lambda p: Rest("POST", f"/work/{_q(p.item_id)}/reconcile", body=_fields(p, "item_id")),
        local=via(work_api.reconcile),
    ),
    "chat_heartbeat": Action(
        "renew a chat turn with its run_id proof",
        "write",
        strict(work_api.ChatHeartbeatIn),
        rest=lambda p: Rest("POST", "/work/chat/heartbeat", body=_fields(p)),
        local=via(work_api.chat_heartbeat),
    ),
    "chat_finish": Action(
        "settle only the proved chat attempt; identical retries return its durable receipt",
        "write",
        strict(work_api.ChatFinishIn),
        rest=lambda p: Rest("POST", "/work/chat/finish", body=_fields(p)),
        local=via(work_api.chat_finish),
    ),
    "chat_reconcile": Action(
        "confirm remote chat termination with run proof and actual runtime evidence",
        "write",
        strict(work_api.ChatReconcileIn),
        rest=lambda p: Rest("POST", "/work/chat/reconcile", body=_fields(p)),
        local=via(work_api.chat_reconcile),
    ),
    "doc_heartbeat": Action(
        "renew a doc turn (a page mention from next) with its run_id proof",
        "write",
        strict(work_api.DocHeartbeatIn),
        rest=lambda p: Rest("POST", "/work/doc/heartbeat", body=_fields(p)),
        local=via(work_api.doc_heartbeat),
    ),
    "doc_finish": Action(
        "end a doc turn: done with reply posts your comment in the thread (a new discussion "
        "thread for a mention in the page body); release posts nothing. Identical retries "
        "return its durable receipt",
        "write",
        strict(work_api.DocFinishIn),
        rest=lambda p: Rest("POST", "/work/doc/finish", body=_fields(p)),
        local=via(work_api.doc_finish),
    ),
    "doc_reconcile": Action(
        "confirm remote doc-turn termination with run proof and actual runtime evidence",
        "write",
        strict(work_api.DocReconcileIn),
        rest=lambda p: Rest("POST", "/work/doc/reconcile", body=_fields(p)),
        local=via(work_api.doc_reconcile),
    ),
    "next": Action(
        "take your next task: the best open one assigned to you or waiting on you (pool: also "
        "unassigned ones) that is not blocked, not held by another agent and not waiting on a "
        "person; priority order, then due date. It is yours for lease_seconds; "
        "item is null when none is free (wait: long-poll for one)",
        "write",
        WorkNext,
        rest=lambda p: Rest("POST", "/work/next", body=_fields(p)),
        local=lambda lc, p: _dump(NextOut, _take_next(lc, p)),
    ),
    "heartbeat": Action(
        "keep holding the task (the lease runs lease_seconds from now); note/progress update "
        "the badge. An error saying you do not hold it means stop: another agent has it",
        "write",
        with_id(HeartbeatIn, "item_id", "The task you hold"),
        rest=lambda p: Rest("POST", f"/work/{_q(p.item_id)}/heartbeat", body=_fields(p, "item_id")),
        local=via(work_api.heartbeat),
    ),
    "finish": Action(
        "report back in one call: outcome done / review / later (stay assigned, no badge, the "
        "due time still to come runs you again) / blocked (waiting_on) / release, a "
        "note posted as your comment, a status, hand_to another account; "
        "next: {} also takes your next task. Agent finishes consume only taken Ask dispatches, "
        "not resolution; asked-only agent done explicitly resolves only its taken Asks. "
        "New/reassigned Asks during the run remain pending; humans must explicitly resolve",
        "write",
        WorkFinish,
        rest=lambda p: Rest("POST", f"/work/{_q(p.item_id)}/finish", body=_fields(p, "item_id")),
        local=_work_finish,
    ),
    "held": Action(
        "the tasks you hold now and when each lease runs out",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/work"),
        local=via(work_api.my_leases),
    ),
    "plan": Action(
        "what each agent is about to run in the next 24 h, by the same rule as `next`: tasks "
        "whose run time comes (reason due), Run now presses (run_requested), open Asks (ask), "
        "and the tasks each holds now with their age; soonest first",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/agents/plan"),
        local=via(agents_api.agents_plan),
    ),
}

HEALTH_ACTIONS = {
    "get": Action(
        "Slate's health (owners and admins): overall status and checks, background pollers "
        "(last run, last success, errors), engines, calls and errors per token in the last "
        "hour, latency percentiles, failed automation runs, queues, database size",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/health"),
        local=via(health_api.get_health),
    )
}

AGENT_ACTIONS = {
    "list": Action(
        "each agent's presence (online, last_seen_at), current reports (working), open "
        "assignments and events in the last 24 h",
        "read",
        query_model(agents_api.list_agents),
        rest=lambda p: Rest("GET", "/agents", query={"kind": p.kind}),
        local=via(agents_api.list_agents),
    )
}

EVENT_ACTIONS = {
    "poll": Action(
        "events after `since`, oldest first; pass next_since back",
        "read",
        query_model(events_api.list_events),
        rest=lambda p: Rest("GET", "/events", query=_fields(p)),
        local=via(events_api.list_events),
    )
}

# Views, favorites ---------------------------------------------------------------------------

VIEW_ACTIONS = {
    "list": Action(
        "saved views, optionally for one scope",
        "read",
        query_model(views_api.list_views),
        rest=lambda p: Rest("GET", "/views", query=_fields(p)),
        local=via(views_api.list_views),
    ),
    "get": Action(
        "one saved view",
        "read",
        ViewRef,
        rest=lambda p: Rest("GET", f"/views/{_q(p.id)}"),
        local=via(views_api.get_view, view_id="id"),
    ),
    "create": Action(
        "save a view. config example: {layout: board, group_by: due, due_window_days: 6, "
        "sort: [{field: priority, dir: desc}], filter: {op: and, rules: [{field: tag, cmp: any, "
        "value: [home]}]}, show_closed: false}",
        "write",
        strict(ViewIn),
        rest=lambda p: Rest("POST", "/views", body=_fields(p)),
        local=via(views_api.create_view),
    ),
    "update": Action(
        "rename, reconfigure, reorder or share a view",
        "write",
        with_id(ViewPatch, "id", "View id"),
        rest=lambda p: Rest("PATCH", f"/views/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(views_api.update_view, view_id="id"),
    ),
    "archive": Action(
        "hide a view",
        "write",
        ViewRef,
        rest=lambda p: Rest("POST", f"/views/{_q(p.id)}/archive"),
        local=via(views_api.archive_view, view_id="id"),
    ),
    "unarchive": Action(
        "bring an archived view back",
        "write",
        ViewRef,
        rest=lambda p: Rest("POST", f"/views/{_q(p.id)}/unarchive"),
        local=via(views_api.unarchive_view, view_id="id"),
    ),
}

FAVORITE_ACTIONS = {
    "list": Action(
        "your favorites in bar order, each with its target's name and scope",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/favorites"),
        local=via(favorites_api.list_favorites),
    ),
    "add": Action(
        "pin a list, section, saved view or item (kind + target_id); pinning twice is a no-op",
        "write",
        strict(FavoriteIn),
        rest=lambda p: Rest("POST", "/favorites", body=_fields(p)),
        local=via(favorites_api.add_favorite),
    ),
    "remove": Action(
        "unpin by favorite id",
        "write",
        FavoriteRef,
        rest=lambda p: Rest("DELETE", f"/favorites/{_q(p.id)}", empty={"removed": p.id}),
        local=via(favorites_api.remove_favorite, favorite_id="id", empty=_deleted("removed")),
    ),
    "reorder": Action(
        "set the bar order; ids must list every favorite once",
        "write",
        strict(FavoriteOrder),
        rest=lambda p: Rest("PUT", "/favorites/order", body=_fields(p)),
        local=via(favorites_api.reorder_favorites),
    ),
}

# Comments, chat, checklists -----------------------------------------------------------------

COMMENT_ACTIONS = {
    "list": Action(
        "the item's comments, oldest first, with reactions",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}/comments"),
        local=via(comments_api.list_comments, item_id="id"),
    ),
    "add": Action(
        "comment on item id; @handle mentions an account; assignee makes it an Ask (open in "
        "their queue until explicitly resolved); replies notify earlier participants but "
        "do not resolve Asks; keep_open is accepted for compatibility",
        "write",
        with_id(CommentIn, "id", "Item id"),
        rest=lambda p: Rest(
            "POST", f"/items/{_q(p.id)}/comments", body=_fields(p, "id", unset=True)
        ),
        local=via(comments_api.add_comment, item_id="id"),
    ),
    "assign": Action(
        "address a comment to an account, or take it off with assignee null (id is the "
        "comment id; the author or an admin)",
        "write",
        CommentAssign,
        rest=lambda p: Rest("PATCH", f"/comments/{_q(p.id)}", body={"assignee": p.assignee}),
        local=via(comments_api.edit_comment, comment_id="id"),
    ),
    "resolve": Action(
        "close an Ask (id is the comment id; its assignee, author or an admin)",
        "write",
        CommentRef,
        rest=lambda p: Rest("POST", f"/comments/{_q(p.id)}/resolve"),
        local=via(comments_api.resolve, comment_id="id"),
    ),
    "edit": Action(
        "edit your own comment (id is the comment id)",
        "write",
        CommentEdit,
        rest=lambda p: Rest("PATCH", f"/comments/{_q(p.id)}", body={"body_md": p.body_md}),
        local=via(comments_api.edit_comment, comment_id="id"),
    ),
    "delete": Action(
        "delete a comment for good (id is the comment id; your own, or anyone's as an admin)",
        "write",
        CommentRef,
        rest=lambda p: Rest("DELETE", f"/comments/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(comments_api.delete_comment, comment_id="id", empty=_deleted("deleted")),
    ),
    "react": Action(
        "add your emoji reaction to a comment",
        "write",
        with_id(ReactionIn, "id", "Comment id"),
        rest=lambda p: Rest("POST", f"/comments/{_q(p.id)}/reactions", body={"emoji": p.emoji}),
        local=via(comments_api.react, comment_id="id"),
    ),
    "unreact": Action(
        "remove your emoji reaction",
        "write",
        with_id(ReactionIn, "id", "Comment id"),
        rest=lambda p: Rest("DELETE", f"/comments/{_q(p.id)}/reactions/{_q(p.emoji)}"),
        local=via(comments_api.unreact, comment_id="id"),
    ),
}

CHAT_ACTIONS = {
    "channels": Action(
        "every channel in board order with your unread and mention counts",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/channels"),
        local=via(chat_api.list_channels),
    ),
    "messages": Action(
        "a page of a channel, oldest first (newest top-level by default; after: follow it)",
        "read",
        query_model(chat_api.list_messages, channel=CHANNEL),
        rest=lambda p: Rest(
            "GET",
            f"/channels/{_q(p.channel)}/messages",
            query=_fields(p, "channel"),
        ),
        local=via(chat_api.list_messages, key="channel"),
    ),
    "thread": Action(
        "a thread: its first message then every reply",
        "read",
        MessageRef,
        rest=lambda p: Rest("GET", f"/messages/{_q(p.id)}/thread"),
        local=via(chat_api.get_thread, message_id="id"),
    ),
    "upload_file": Action(
        "upload a file (base64) to a channel, then send it: post with its id in file_ids",
        "write",
        with_id(attachments_api.Base64In, "channel", CHANNEL_DOC),
        rest=lambda p: Rest(
            "POST", f"/channels/{_q(p.channel)}/files/base64", body=_fields(p, "channel")
        ),
        local=via(chat_api.upload_file_base64, key="channel"),
    ),
    "discard_file": Action(
        "take back an upload you have not sent",
        "write",
        MessageRef,
        rest=lambda p: Rest("DELETE", f"/message-files/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(chat_api.discard_file, file_id="id", empty=_deleted("deleted")),
    ),
    "post": Action(
        "post to a channel, or reply in a thread with parent_id; file_ids sends your uploads",
        "write",
        with_id(MessageIn, "channel", CHANNEL_DOC),
        rest=lambda p: Rest(
            "POST", f"/channels/{_q(p.channel)}/messages", body=_fields(p, "channel")
        ),
        local=via(chat_api.post_message, key="channel"),
    ),
    "edit": Action(
        "edit your own message",
        "write",
        with_id(MessagePatch, "id", "Message id"),
        rest=lambda p: Rest("PATCH", f"/messages/{_q(p.id)}", body={"body_md": p.body_md}),
        local=via(chat_api.edit_message, message_id="id"),
    ),
    "delete": Action(
        "delete your own message (its thread stays)",
        "write",
        MessageRef,
        rest=lambda p: Rest("DELETE", f"/messages/{_q(p.id)}"),
        local=via(chat_api.delete_message, message_id="id"),
    ),
    "react": Action(
        "add your emoji reaction to a message",
        "write",
        with_id(ReactionIn, "id", "Message id"),
        rest=lambda p: Rest("POST", f"/messages/{_q(p.id)}/reactions", body={"emoji": p.emoji}),
        local=via(chat_api.react, message_id="id"),
    ),
    "unreact": Action(
        "remove your emoji reaction",
        "write",
        with_id(ReactionIn, "id", "Message id"),
        rest=lambda p: Rest("DELETE", f"/messages/{_q(p.id)}/reactions/{_q(p.emoji)}"),
        local=via(chat_api.unreact, message_id="id"),
    ),
    "task": Action(
        "turn a message into one task (in the channel's list unless list_id)",
        "write",
        with_id(TaskIn, "id", "Message id"),
        rest=lambda p: Rest("POST", f"/messages/{_q(p.id)}/task", body=_fields(p, "id")),
        local=via(chat_api.make_task, message_id="id"),
    ),
    "read": Action(
        "mark a channel read up to through (default its newest message)",
        "write",
        with_id(ReadIn, "channel", CHANNEL_DOC),
        rest=lambda p: Rest("POST", f"/channels/{_q(p.channel)}/read", body=_fields(p, "channel")),
        local=via(chat_api.mark_read, key="channel"),
    ),
    "read_all": Action(
        "mark every channel read",
        "write",
        NoParams,
        rest=lambda p: Rest("POST", "/channels/read-all"),
        local=via(chat_api.read_all),
    ),
}

CHECKLIST_ACTIONS = {
    "list": Action(
        "the item's checklists with their entries",
        "read",
        ItemRef,
        rest=lambda p: Rest("GET", f"/items/{_q(p.id)}/checklists"),
        local=via(checklists_api.list_checklists, item_id="id"),
    ),
    "create": Action(
        "a checklist on item_id, optionally with its first entries",
        "write",
        with_id(ChecklistIn, "item_id", "Item id"),
        rest=lambda p: Rest(
            "POST",
            f"/items/{_q(p.item_id)}/checklists",
            body={"name": p.name, "items": p.items},
        ),
        local=via(checklists_api.create_checklist),
    ),
    "rename": Action(
        "rename a checklist (position reorders it)",
        "write",
        with_id(ChecklistPatch, "id", "Checklist id"),
        rest=lambda p: Rest("PATCH", f"/checklists/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(checklists_api.update_checklist, checklist_id="id"),
    ),
    "delete": Action(
        "remove a checklist and its entries",
        "write",
        ChecklistRef,
        rest=lambda p: Rest("DELETE", f"/checklists/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(checklists_api.delete_checklist, checklist_id="id", empty=_deleted("deleted")),
    ),
    "add_entry": Action(
        "add an entry to checklist_id",
        "write",
        with_id(ChecklistEntryIn, "checklist_id", "Checklist id"),
        rest=lambda p: Rest(
            "POST", f"/checklists/{_q(p.checklist_id)}/items", body=_fields(p, "checklist_id")
        ),
        local=via(checklists_api.add_entry),
    ),
    "update_entry": Action(
        "tick (done true), untick, rename, assign or reorder an entry",
        "write",
        with_id(ChecklistEntryPatch, "entry_id", "Checklist entry id"),
        rest=lambda p: Rest(
            "PATCH", f"/checklist-items/{_q(p.entry_id)}", body=_fields(p, "entry_id", unset=True)
        ),
        local=via(checklists_api.update_entry),
    ),
    "remove_entry": Action(
        "delete an entry",
        "write",
        ChecklistEntryRef,
        rest=lambda p: Rest(
            "DELETE", f"/checklist-items/{_q(p.entry_id)}", empty={"deleted": p.entry_id}
        ),
        local=via(checklists_api.delete_entry, empty=_deleted("deleted", "entry_id")),
    ),
}

# Automations --------------------------------------------------------------------------------

_runs_of = via(automations_api.automation_runs, automation_id="id")
_all_runs = via(automations_api.all_runs)

AUTOMATION_ACTIONS = {
    "catalog": Action(
        "every trigger, condition field and action with its parameters",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/automations/catalog"),
        local=via(automations_api.catalog),
    ),
    "list": Action(
        "rules, each with a plain-English description and its last run",
        "read",
        query_model(automations_api.list_automations),
        rest=lambda p: Rest("GET", "/automations", query=_fields(p)),
        local=via(automations_api.list_automations),
    ),
    "get": Action(
        "one rule",
        "read",
        AutomationRef,
        rest=lambda p: Rest("GET", f"/automations/{_q(p.id)}"),
        local=via(automations_api.get_automation, automation_id="id"),
    ),
    "create": Action(
        "name, trigger {type,...}, actions [{type,...}], optional conditions and list_id or "
        "section_id (neither = every list); see catalog",
        "write",
        strict(AutomationIn),
        rest=lambda p: Rest("POST", "/automations", body=p.model_dump(mode="json")),
        local=via(automations_api.create_automation),
    ),
    "update": Action(
        "change any field; enabled false pauses the rule",
        "write",
        with_id(AutomationPatch, "id", "Automation id"),
        rest=lambda p: Rest("PATCH", f"/automations/{_q(p.id)}", body=_fields(p, "id", unset=True)),
        local=via(automations_api.update_automation, automation_id="id"),
    ),
    "delete": Action(
        "remove a rule and its run log (its history stays)",
        "write",
        AutomationRef,
        rest=lambda p: Rest("DELETE", f"/automations/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(automations_api.delete_automation, automation_id="id", empty=_deleted("deleted")),
    ),
    "run": Action(
        "run a rule's actions on one item now (trigger and conditions skipped)",
        "write",
        with_id(RunNowIn, "id", "Automation id"),
        rest=lambda p: Rest("POST", f"/automations/{_q(p.id)}/run", body={"item_id": p.item_id}),
        local=via(automations_api.run_automation, automation_id="id"),
    ),
    "rotate_secret": Action(
        "a new secret for verifying the rule's webhook signatures (shown once)",
        "write",
        AutomationRef,
        rest=lambda p: Rest("POST", f"/automations/{_q(p.id)}/secret"),
        local=via(automations_api.rotate_secret, automation_id="id"),
    ),
    "runs": Action(
        "the run log, newest first: what fired, on which item, how each action went",
        "read",
        AutomationRuns,
        rest=lambda p: Rest(
            "GET",
            f"/automations/{_q(p.id)}/runs" if p.id else "/automation-runs",
            query={"limit": p.limit},
        ),
        local=lambda lc, p: (_runs_of if p.id else _all_runs)(lc, p),
    ),
}

# Docs ---------------------------------------------------------------------------------------

DOC_ID = (str, Field(description="Doc id"))

DOC_ACTIONS = {
    "list": Action(
        "pages in tree order without bodies; q searches titles and bodies",
        "read",
        query_model(docs_api.list_docs),
        rest=lambda p: Rest("GET", "/docs", query=_fields(p)),
        local=via(docs_api.list_docs),
    ),
    "get": Action(
        "one page: body, path, subpages, outline, embedded tasks, links, backlinks, files",
        "read",
        DocRefP,
        rest=lambda p: Rest("GET", f"/docs/{_q(p.id)}"),
        local=via(docs_api.get_doc, doc_id="id"),
    ),
    "templates": Action(
        "starting points for create's template",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/docs/templates"),
        local=via(docs_api.templates),
    ),
    "create": Action(
        "a page in list_id, section_id, the workspace (neither) or under parent_id; "
        "body_md is Markdown, [[task:<id>]] embeds a task card, [[doc:<id>]] links a page",
        "write",
        strict(DocIn),
        rest=lambda p: Rest("POST", "/docs", body=_fields(p)),
        local=via(docs_api.create_doc),
    ),
    "update": Action(
        "edit title, icon or body: body_md replaces, append_md adds at the end, "
        "replace_section {heading, body_md} rewrites one part; base_revision guards against "
        "overwriting a newer edit; note names a page-history version",
        "write",
        with_id(DocPatch, "id", "Doc id"),
        rest=lambda p: Rest(
            "PATCH", f"/docs/{_q(p.id)}", body=jsonable(_fields(p, "id", unset=True))
        ),
        local=via(docs_api.update_doc, doc_id="id"),
    ),
    "move": Action(
        "move under parent_id, or to the top of list_id / section_id / the workspace; before_id "
        "orders it",
        "write",
        with_id(DocMoveIn, "id", "Doc id"),
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/move", body=_fields(p, "id", unset=True)),
        local=via(docs_api.move_doc, doc_id="id"),
    ),
    "archive": Action(
        "hide a page and its subpages",
        "write",
        DocRefP,
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/archive"),
        local=via(docs_api.archive_doc, doc_id="id"),
    ),
    "unarchive": Action(
        "bring an archived page back",
        "write",
        DocRefP,
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/unarchive"),
        local=via(docs_api.unarchive_doc, doc_id="id"),
    ),
    "delete": Action(
        "delete an archived page and its subpages for good",
        "write",
        DocRefP,
        rest=lambda p: Rest("DELETE", f"/docs/{_q(p.id)}"),
        local=via(docs_api.delete_doc, doc_id="id"),
    ),
    "duplicate": Action(
        "copy a page (and its subpages) next to it",
        "write",
        with_id(DocDuplicateIn, "id", "Doc id"),
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/duplicate", body=_fields(p, "id")),
        local=via(docs_api.duplicate_doc, doc_id="id"),
    ),
    "versions": Action(
        "page history, newest first (no bodies)",
        "read",
        DocRefP,
        rest=lambda p: Rest("GET", f"/docs/{_q(p.id)}/versions"),
        local=via(docs_api.versions, doc_id="id"),
    ),
    "version": Action(
        "one version with its body",
        "read",
        DocVersionRef,
        rest=lambda p: Rest("GET", f"/docs/{_q(p.id)}/versions/{p.version_no}"),
        local=via(docs_api.version, doc_id="id"),
    ),
    "diff": Action(
        "unified diff of the body between two versions (default to: the current text)",
        "read",
        query_model(docs_api.diff, id=DOC_ID),
        rest=lambda p: Rest(
            "GET",
            f"/docs/{_q(p.id)}/diff",
            query={"from": p.from_version, **({"to": p.to_version} if p.to_version else {})},
        ),
        local=via(docs_api.diff, doc_id="id"),
    ),
    "restore": Action(
        "put an older version's title and body back (as a new version)",
        "write",
        DocVersionRef,
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/versions/{p.version_no}/restore"),
        local=via(docs_api.restore, doc_id="id"),
    ),
    "create_task": Action(
        "file one task from the page: replace names the text that becomes its live card "
        f"(else the card is added at the end); the task links back. {RULE}",
        "write",
        with_id(DocTaskIn, "id", "Doc id"),
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/tasks", body=jsonable(_fields(p, "id"))),
        local=via(docs_api.create_task, doc_id="id"),
    ),
    "attach": Action(
        "upload an image or file into the page; paste the returned markdown into the body",
        "write",
        with_id(Base64FileIn, "id", "Doc id"),
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/files/base64", body=_fields(p, "id")),
        local=via(docs_api.add_file_base64, doc_id="id"),
    ),
    "remove_file": Action(
        "remove an uploaded file",
        "write",
        DocFileRef,
        rest=lambda p: Rest("DELETE", f"/doc-files/{_q(p.file_id)}", empty={"removed": p.file_id}),
        local=via(docs_api.remove_file, empty=_deleted("removed", "file_id")),
    ),
    "upload": Action(
        "the next revision of a file doc (a PDF, spreadsheet, photo); the previous revision "
        "stays, superseded; note says what changed and is required",
        "write",
        with_id(Base64RevisionIn, "id", "Doc id (a file doc: create with kind='file')"),
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/revisions/base64", body=_fields(p, "id")),
        local=via(docs_api.add_revision_base64, doc_id="id"),
    ),
    "revisions": Action(
        "a file doc's revisions, newest first (metadata; bytes at each url)",
        "read",
        DocRefP,
        rest=lambda p: Rest("GET", f"/docs/{_q(p.id)}/revisions"),
        local=via(docs_api.revisions, doc_id="id"),
    ),
    "restore_revision": Action(
        "make an older revision of a file doc current again (as a new revision)",
        "write",
        DocRevisionRef,
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/revisions/{p.revision_no}/restore"),
        local=via(docs_api.restore_revision, doc_id="id"),
    ),
    "hub": Action(
        "a section's documents hub: its drawers with counts, pages outside any drawer, what "
        "needs you (open asks to you, open review threads by others on docs you touched)",
        "read",
        SectionDocs,
        rest=lambda p: Rest("GET", f"/sections/{_q(p.section_id)}/documents"),
        local=via(docs_api.section_documents),
    ),
    "drawer": Action(
        "a drawer's entries (folders and documents) at its top or inside a folder, with "
        "revisions, pins, tags, thread counts; a drawer is a list of kind drawer",
        "read",
        query_model(
            docs_api.drawer,
            list_id=(str, Field(description="The drawer (a list of kind drawer)")),
        ),
        rest=lambda p: Rest(
            "GET",
            f"/lists/{_q(p.list_id)}/drawer",
            query={"folder": p.folder} if p.folder else {},
        ),
        local=via(docs_api.drawer),
    ),
    "to_drawer": Action(
        "turn a top-level page into a drawer of the same name in its section (its subpages "
        "become the entries; the page moves in too)",
        "write",
        DocRefP,
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/to-drawer"),
        local=via(docs_api.to_drawer, doc_id="id"),
    ),
    "pin_revision": Action(
        "mark a file doc's revision as the one in use; one pin per doc",
        "write",
        with_id(
            PinIn,
            "id",
            "Doc id (a file doc)",
            revision_no=(int, Field(ge=1, description="The revision in use")),
        ),
        rest=lambda p: Rest(
            "POST", f"/docs/{_q(p.id)}/revisions/{p.revision_no}/pin", body={"note": p.note}
        ),
        local=via(docs_api.pin_revision, doc_id="id"),
    ),
    "unpin_revision": Action(
        "take the pin off a file doc",
        "write",
        DocRefP,
        rest=lambda p: Rest("DELETE", f"/docs/{_q(p.id)}/pin"),
        local=via(docs_api.unpin_revision, doc_id="id"),
    ),
    "pin_to_top": Action(
        "pin a document (file, page or folder) to the top of its drawer or folder listing, "
        "for everyone",
        "write",
        DocRefP,
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/top"),
        local=via(docs_api.pin_to_top, doc_id="id"),
    ),
    "unpin_from_top": Action(
        "take a document off the top of its drawer or folder listing",
        "write",
        DocRefP,
        rest=lambda p: Rest("DELETE", f"/docs/{_q(p.id)}/top"),
        local=via(docs_api.unpin_from_top, doc_id="id"),
    ),
    "compare_revisions": Action(
        "what changed between two revisions of a file doc: text files line by line, "
        "CSV/XLSX cell by cell per sheet, PDFs by extracted text (approximate); images none",
        "read",
        DocCompare,
        rest=lambda p: Rest(
            "GET",
            f"/docs/{_q(p.id)}/revisions/compare",
            query={
                "from": p.from_revision,
                **({"to": p.to_revision} if p.to_revision else {}),
            },
        ),
        local=via(
            docs_api.compare_revisions, doc_id="id", from_no="from_revision", to_no="to_revision"
        ),
    ),
    "comments": Action(
        "every comment on the doc, oldest first (review threads carry an anchor and can be "
        "resolved; discussion has none; replies carry parent_id)",
        "read",
        DocRefP,
        rest=lambda p: Rest("GET", f"/docs/{_q(p.id)}/comments"),
        local=via(doc_comments_api.list_comments, doc_id="id"),
    ),
    "comment": Action(
        "comment on a page or file: with anchor (quote/start/end on a page; page and/or x,y "
        "on a file) a review thread, without one discussion; parent_id replies; "
        "suggestion_md proposes replacement text for the quote; assignee makes it an Ask",
        "write",
        with_id(DocCommentIn, "id", "Doc id"),
        rest=lambda p: Rest("POST", f"/docs/{_q(p.id)}/comments", body=jsonable(_fields(p, "id"))),
        local=via(doc_comments_api.add_comment, doc_id="id"),
    ),
    "edit_comment": Action(
        "edit your own doc comment",
        "write",
        with_id(DocCommentPatch, "comment_id", "Doc comment id"),
        rest=lambda p: Rest(
            "PATCH", f"/doc-comments/{_q(p.comment_id)}", body={"body_md": p.body_md}
        ),
        local=via(doc_comments_api.edit_comment),
    ),
    "resolve_comment": Action(
        "close a review thread or Ask (its first comment)",
        "write",
        DocCommentRef,
        rest=lambda p: Rest("POST", f"/doc-comments/{_q(p.comment_id)}/resolve"),
        local=via(doc_comments_api.resolve),
    ),
    "reopen_comment": Action(
        "reopen a resolved thread",
        "write",
        DocCommentRef,
        rest=lambda p: Rest("POST", f"/doc-comments/{_q(p.comment_id)}/reopen"),
        local=via(doc_comments_api.reopen),
    ),
    "assign_comment": Action(
        "address a doc comment to an account (an Ask), or take it off",
        "write",
        with_id(DocCommentAssignIn, "comment_id", "Doc comment id"),
        rest=lambda p: Rest(
            "POST", f"/doc-comments/{_q(p.comment_id)}/assign", body={"assignee": p.assignee}
        ),
        local=via(doc_comments_api.assign),
    ),
    "accept_suggestion": Action(
        "apply a review comment's suggestion_md to the page and resolve the thread",
        "write",
        DocCommentRef,
        rest=lambda p: Rest("POST", f"/doc-comments/{_q(p.comment_id)}/accept"),
        local=via(doc_comments_api.accept),
    ),
    "activity": Action(
        "the doc's comments plus the events the side panel folds in (revisions, saves, "
        "moves, resolved threads), oldest first",
        "read",
        query_model(doc_comments_api.activity, id=DOC_ID),
        rest=lambda p: Rest("GET", f"/docs/{_q(p.id)}/activity", query={"limit": p.limit}),
        local=via(doc_comments_api.activity, doc_id="id"),
    ),
}

# Workload -----------------------------------------------------------------------------------

WORKLOAD_ACTIONS = {
    "boxes": Action(
        "the Box view: per person and agent (and unassigned) open, in progress, overdue and "
        "recently done counts, with open minutes against this week's capacity",
        "read",
        query_model(workload_api.get_boxes),
        rest=lambda p: Rest("GET", "/workload/boxes", query=_fields(p)),
        local=via(workload_api.get_boxes),
    ),
    "capacities": Action(
        "every person's and agent's capacity (hours a day, tasks a day, days)",
        "read",
        query_model(workload_api.list_capacities),
        rest=lambda p: Rest("GET", "/capacities", query=_fields(p)),
        local=via(workload_api.list_capacities),
    ),
    "capacity": Action(
        "one account's capacity",
        "read",
        AccountRef,
        rest=lambda p: Rest("GET", f"/accounts/{_q(p.account_id)}/capacity"),
        local=via(workload_api.get_capacity),
    ),
    "set_capacity": Action(
        "set your own capacity (or anyone's as an admin): hours_per_day, tasks_per_day "
        "(null = no limit) and work_days (0 = Monday)",
        "write",
        with_id(CapacityIn, "account_id", "Account id (a person or an agent)"),
        rest=lambda p: Rest(
            "PATCH",
            f"/accounts/{_q(p.account_id)}/capacity",
            body=_fields(p, "account_id", unset=True),
        ),
        local=via(workload_api.set_capacity),
    ),
    "reset_capacity": Action(
        "put an account's capacity back to the defaults for its kind",
        "write",
        AccountRef,
        rest=lambda p: Rest("DELETE", f"/accounts/{_q(p.account_id)}/capacity"),
        local=via(workload_api.reset_capacity),
    ),
}

# Reminders ----------------------------------------------------------------------------------

REMINDER_ACTIONS = {
    "add": Action(
        "remind you (or `account`) about an item `at` a time, or `before_minutes` before its "
        "due date; it arrives in the inbox, check-in and on phone and desktop. Alerts before "
        "due: one call per lead (5, 15, 30, 60, 120, 1440, 2880, 10080...), any number per item; "
        "the same lead twice is one alert. Setting one for a person from a chat app: pass "
        "`account` (`alex`), else it reminds you",
        "write",
        with_id(ReminderIn, "item_id", "Item id"),
        rest=lambda p: Rest(
            "POST", f"/items/{_q(p.item_id)}/reminders", body=_fields(p, "item_id")
        ),
        local=via(reminders_api.add_reminder),
    ),
    "list": Action(
        "an item's reminders, fired or not",
        "read",
        ReminderItem,
        rest=lambda p: Rest("GET", f"/items/{_q(p.item_id)}/reminders"),
        local=via(reminders_api.item_reminders),
    ),
    "upcoming": Action(
        "your reminders that have not gone off yet, soonest first",
        "read",
        query_model(reminders_api.my_reminders),
        rest=lambda p: Rest("GET", "/reminders", query={"limit": p.limit}),
        local=via(reminders_api.my_reminders),
    ),
    "remove": Action(
        "remove a reminder (yours, or one you set)",
        "write",
        ReminderRef,
        rest=lambda p: Rest("DELETE", f"/reminders/{_q(p.id)}", empty={"deleted": p.id}),
        local=via(reminders_api.remove_reminder, reminder_id="id", empty=_deleted("deleted")),
    ),
    "digest": Action(
        "what your daily digest says right now: overdue, due today, waiting on you",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/me/digest"),
        local=via(reminders_api.my_digest),
    ),
}

# Export, batch, inbox, search ---------------------------------------------------------------

EXPORT_ACTIONS = {
    "json": Action(
        "a list, a section or everything as one JSON document: every task (the item shape) "
        "with comments, checklists and links, plus the lists, statuses, tags and "
        "fields around them; trashed tasks are left out",
        "read",
        query_model(data_api.export_data, format=None),
        rest=lambda p: Rest("GET", "/export", query={"format": "json", **_fields(p)}),
        local=via(data_api.export_data, format=lambda p: "json"),
    ),
    "empty_trash": Action(
        "delete everything in the trash for good (admins)",
        "admin",
        NoParams,
        rest=lambda p: Rest("POST", "/trash/empty"),
        local=via(data_api.empty_trash),
    ),
}


def _batch(lc: Local, p: Any) -> dict:
    from slate import batch

    return batch.run_batch(
        lc.session, lc.principal, lc.actor, p.ops, atomic=p.atomic, views=p.views
    )


def _batch_actions() -> dict[str, Action]:
    from slate.batch import BatchIn

    return {
        "run": Action(
            "{atomic, results: [{ok, result | error}]}, one slot per op in order",
            "read",
            BatchIn,
            rest=lambda p: Rest("POST", "/batch", body=p.model_dump(exclude_none=True)),
            local=_batch,
        )
    }


INBOX_ACTIONS = {
    "list": Action(
        "your notifications, newest first: mentions, assignments, due nudges and changes on "
        "items you are assigned to or watch",
        "read",
        query_model(inbox_api.list_inbox),
        rest=lambda p: Rest("GET", "/inbox", query=_fields(p)),
        local=via(inbox_api.list_inbox),
    ),
    "count": Action(
        "how many are unread",
        "read",
        NoParams,
        rest=lambda p: Rest("GET", "/inbox/count"),
        local=via(inbox_api.inbox_count),
    ),
    "read": Action(
        "mark entries read: event_ids, every entry for an item_id, or all",
        "write",
        strict(inbox_api.InboxMarkIn),
        rest=lambda p: Rest("POST", "/inbox/read", body=_fields(p)),
        local=via(inbox_api.mark_read),
    ),
    "clear": Action(
        "clear entries out of the inbox (same targets as read)",
        "write",
        strict(inbox_api.InboxMarkIn),
        rest=lambda p: Rest("POST", "/inbox/clear", body=_fields(p)),
        local=via(inbox_api.clear),
    ),
}

SEARCH_ACTIONS = {
    "search": Action(
        "find q in tasks (title, description), docs (title, page text, file text) and chat "
        "messages at once: typed hits, tasks first, then docs, then messages (newest first); "
        "only what you can read; `%` and `_` are literal",
        "read",
        query_model(search_api.search),
        rest=lambda p: Rest("GET", "/search", query={"q": p.q, "limit": p.limit}),
        local=via(search_api.search),
    )
}


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "slate_item",
            "Slate items",
            "Read and change items (tasks) on Slate, the workspace's task board: find, file, "
            "update, move, assign, tag, comment, report what you are doing (live badge), "
            "archive, split and read history. Use it whenever you file, look up or progress a "
            "piece of work.",
            {
                "action": "create",
                "params": {
                    "list_id": "Ab12Cd34Ef56",
                    "title": "Renew passport",
                    "due_at": "2026-11-01",
                },
            },
            ITEM_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_comment",
            "Slate comments",
            "Comments are the conversation on an item; @handle mentions an account and reaches "
            "its inbox and assignee_me webhook. Use it to ask someone something, answer, or react; "
            "for progress on work you hold, slate_item report shows it on the card.",
            {"action": "add", "params": {"id": "Ab12Cd34Ef56", "body_md": "Done, @alex"}},
            COMMENT_ACTIONS,
        ),
        ToolSpec(
            "slate_chat",
            "Slate chat",
            "Chat channels: General, one per section, one per list and one per agent (channel "
            "`general`, `list:<id>`, `section:<id>`, `agent:<id>`), with threads, reactions and "
            "@mentions. Use it to talk with people and other agents about a list's work, answer a "
            "mention or thread reply from check-in, or turn a message into a task. Your own chat "
            "(`agent:<your id>`) is where a person talks to you outside any card: every message "
            "they post there mentions you; answer there. A message's `files` are what was sent "
            "with it (a screenshot, a PDF): read one with GET /api/v1/message-files/<id>.",
            {"action": "post", "params": {"channel": "general", "body_md": "On it, @alex"}},
            CHAT_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_checklist",
            "Slate checklists",
            "Checklists are small ticked to-dos inside one item, not subtasks. Use them for steps "
            "of a single piece of work; tick entries as you finish them.",
            {
                "action": "create",
                "params": {"item_id": "Ab12Cd34Ef56", "name": "Steps", "items": ["call", "pay"]},
            },
            CHECKLIST_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_view",
            "Slate saved views",
            "A view is a saved lens: layout (board or list), group_by (due, status, "
            "priority, assignee, tag, list, section, status_kind), sort, and a filter of "
            "{op, rules} where each rule is {field, cmp, value} or a nested group. Use it to "
            "build or adjust the lenses people look at. Example config: "
            "{layout: board, group_by: due, due_window_days: 6, "
            "sort: [{field: priority, dir: desc}, {field: due, dir: asc}], "
            "filter: {op: and, rules: [{field: status_kind, cmp: is, value: [open, active]}]}}.",
            {"action": "list", "params": {"scope": "everything"}},
            VIEW_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_favorite",
            "Slate favorites",
            "Favorites are your own pinned lists, sections, saved views and items, shown as "
            "one-click chips across the top of the board. Use it to pin what you (or the "
            "account whose token you hold) will open often, or to reorder the bar.",
            {"action": "add", "params": {"kind": "list", "target_id": "Ab12Cd34Ef56"}},
            FAVORITE_ACTIONS,
        ),
        ToolSpec(
            "slate_automation",
            "Slate automations",
            "Automations are when-this-then-that rules on a list, section or everywhere: a "
            "trigger (status changes, assignee added, due date passes...), optional conditions "
            "and actions (set status, assign, tag, comment, move, call a webhook). Use it to "
            "set up routine handling once instead of doing it by hand; read catalog first.",
            {
                "action": "create",
                "params": {
                    "name": "Tag shipped work",
                    "list_id": "Ab12Cd34Ef56",
                    "trigger": {"type": "status_changed", "to_kind": "done"},
                    "actions": [{"type": "add_tag", "tag": "shipped"}],
                },
            },
            AUTOMATION_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_workload",
            "Slate workload",
            "Who has how much on: the Box view's per-person and per-agent counts (open, in "
            "progress, overdue, recently done) and open minutes against this week's capacity. "
            "Use boxes before handing work to someone, and set_capacity to say how much you "
            "can take on.",
            {"action": "boxes", "params": {"kinds": "agents"}},
            WORKLOAD_ACTIONS,
        ),
        ToolSpec(
            "slate_reminder",
            "Slate reminders",
            "Reminders tell an account about an item at a time, or a set time before its due "
            "date, through its inbox, check-in and phone and desktop notifications. Use it to "
            "follow up on something later; digest shows today's overdue and due work.",
            {"action": "add", "params": {"item_id": "Ab12Cd34Ef56", "before_minutes": 60}},
            REMINDER_ACTIONS,
        ),
        ToolSpec(
            "slate_inbox",
            "Slate inbox",
            "Your notifications as the board's Inbox shows them: who mentioned you, what you "
            "were assigned, and what changed on items you follow, with read state. Mark what "
            "you handled as read. To poll for work on a cursor, slate_checkin is quicker.",
            {"action": "list", "params": {"unread": True}},
            INBOX_ACTIONS,
        ),
        ToolSpec(
            "slate_list",
            "Slate lists",
            "Lists live in a section and own their statuses, which are the board's columns. Use "
            "it to find a list_id before filing items, or to create, rename or re-column a list.",
            {"action": "list", "params": {"section_id": "Ab12Cd34Ef56"}},
            LIST_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_section",
            "Slate sections",
            "Sections are the top-level separations (for example Personal and Work). Use it to "
            "find a section, or to create or rename one when someone asks for a new audience or "
            "rhythm.",
            {"action": "list", "params": {}},
            SECTION_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_tag",
            "Slate tags",
            "Tags cut across every section and list. Use it to see the existing tags before "
            "inventing one, or to create, rename, recolor, merge or delete a tag.",
            {"action": "rename", "params": {"tag": "ai project", "name": "ai-project"}},
            TAG_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_recurrence",
            "Slate recurring rules",
            "Recurring rules spawn an item on a schedule (RFC 5545 RRULE, workspace timezone), or "
            "with trigger on_done the next one when the current one is done (reopen: true reuses "
            "the same task). skip_weekends moves weekend dates to Monday. Use it instead of "
            "filing dated copies of a chore by hand.",
            {
                "action": "create",
                "params": {
                    "list_id": "Ab12Cd34Ef56",
                    "title_template": "Bulk trash pickup",
                    "rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE",
                },
            },
            RECURRENCE_ACTIONS,
        ),
        ToolSpec(
            "slate_webhook",
            "Slate webhooks",
            "Webhooks POST signed events to your URL so you are woken instead of polling. Use it "
            "to register, inspect or test your own doorbell.",
            {
                "action": "create",
                "params": {
                    "url": "https://agent.example/slate",
                    "events": ["item.assigned", "item.due"],
                    "filter": {"assignee_me": True},
                },
            },
            WEBHOOK_ACTIONS,
        ),
        ToolSpec(
            "slate_account",
            "Slate accounts",
            "Accounts are the people and agents who own and change items; assignees are account "
            "handles. Use `me` to learn who you are; the other actions need an admin token.",
            {"action": "me"},
            ACCOUNT_ACTIONS,
        ),
        ToolSpec(
            "slate_today",
            "Slate today",
            "Your day across every section: overdue, due before midnight workspace time, and "
            "items waiting on you, most urgent first. Use it to decide what to work on next.",
            {},
            TODAY_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_checkin",
            "Slate check-in",
            "Start here each time you wake: `queue` is your open items (assigned to you or "
            "waiting on you), most urgent first; `news` is what other accounts did that "
            "concerns you since your last call (assigned, unassigned, mentioned, reply, "
            "waiting_on_you, on_your_item, watching, reminder, digest); a chat mention or thread "
            "reply carries the message in `after` (answer with slate_chat). Keep `next_since` "
            "and pass it back as `since`.",
            {"params": {"since": "0001Ab2Cd3Ef"}},
            CHECKIN_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_work",
            "Slate work loop",
            "The agent work loop in three calls: `next` takes your best free task (with a lease "
            "so nobody else takes it), `heartbeat` keeps it while you work, `finish` reports "
            "back (outcome, comment, hand-off) and can take the next one. Use it instead "
            "of picking tasks from slate_checkin by hand.",
            {
                "action": "finish",
                "params": {
                    "item_id": "Ab12Cd34Ef56",
                    "outcome": "done",
                    "note": "Sent the invoice",
                    "next": {},
                },
            },
            WORK_ACTIONS,
        ),
        ToolSpec(
            "slate_doc",
            "Slate docs",
            "Docs are nested Markdown pages (plans, notes, specs, logs) in a list, a section or "
            "the workspace, with page history; [[task:<id>]] embeds a live task card. Use it to "
            "keep a plan or work log, read what people wrote, or file tasks from a page. A page "
            "that starts with a ```facts block of `key: value` lines (a policy number, a "
            "renewal date) is a record: `get` returns them as `facts` and search finds a "
            "value; write or change them with `update` like any body text.",
            {
                "action": "update",
                "params": {"id": "Ab12Cd34Ef56", "append_md": "- 14:05 deployed the fix"},
            },
            DOC_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_agents",
            "Slate agents",
            "The roster of agent accounts: who is online, what each is working on (their "
            "current reports), how many open items each holds. Use it before picking up work, "
            "to see whether another agent is already on it.",
            {"params": {}},
            AGENT_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_health",
            "Slate health",
            "Whether Slate is working: background pollers, engines, error "
            "rates and slow calls per token, failed automations and queue depths (owners and "
            "admins). Use it when calls fail or seem slow, before reporting Slate as broken.",
            {"params": {}},
            HEALTH_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_batch",
            "Slate batch",
            "Run up to 100 calls of the other tools in one round trip, all-or-nothing by "
            "default (atomic: false commits each op alone). Use it to file or change many "
            "things at once; a string param `$ref.field` reuses an earlier op's result.",
            {
                "params": {
                    "ops": [
                        {
                            "ref": "t",
                            "tool": "item",
                            "action": "create",
                            "params": {"list_id": "Ab12Cd34Ef56", "title": "Renew passport"},
                        },
                        {
                            "tool": "comment",
                            "action": "add",
                            "params": {"id": "$t.id", "body_md": "Filed"},
                        },
                    ]
                }
            },
            _batch_actions(),
            destructive=True,
        ),
        ToolSpec(
            "slate_events",
            "Slate events",
            "Every change on Slate is an event with an actor. Use it to catch up on everything "
            "that changed, or audit one account (actor); keep next_since and pass it back as "
            "since. For only what concerns you, slate_checkin is the filtered version.",
            {"params": {"since": "0", "type": "item.assigned,item.commented"}},
            EVENT_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_share",
            "Slate sharing",
            "Who can see and change what: share a section or list with a person or agent at "
            "view, comment or edit, make one private, and see who reaches it. Owner and admin "
            "only; roles are slate_account set_role.",
            {
                "action": "set",
                "params": {
                    "account_id": "Ab12Cd34Ef56",
                    "scope_kind": "list",
                    "scope_id": "Gh78Ij90Kl12",
                    "level": "comment",
                },
            },
            SHARE_ACTIONS,
        ),
        ToolSpec(
            "slate_audit",
            "Slate audit log",
            "The audit log: every change with who did it (person or agent), when, through which "
            "door (ui, api, mcp, system) and on what, newest first. Use it to answer who changed "
            "this, or what an agent did today; it only shows what your access can read.",
            {"action": "list", "params": {"actor_kind": "agent", "limit": 50}},
            AUDIT_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_export",
            "Slate export",
            "Take Slate's data out: a list, a section or everything as one JSON document with "
            "every task, comment, checklist and link. Use it for a report, an "
            "archive or a move; empty_trash (admins) clears the trash for good.",
            {"action": "json", "params": {"list_id": "Ab12Cd34Ef56"}},
            EXPORT_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_search",
            "Slate search",
            "One search over everything on Slate: tasks (title and description), docs (title, "
            "page text and the text inside files) and chat messages, as typed hits. Use it when "
            "you are looking for something and do not know whether it is a task, a page or a "
            "message.",
            {"action": "search", "params": {"q": "passport", "limit": 30}},
            SEARCH_ACTIONS,
            read_only=True,
        ),
    )
}


# --- tool schemas and descriptions ----------------------------------------------------------


def _strip_titles(node: Any) -> Any:
    """Drop pydantic's auto `title` strings (noise for a model); keep properties named title."""
    if isinstance(node, dict):
        return {
            k: _strip_titles(v)
            for k, v in node.items()
            if not (k == "title" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def single_action(spec: ToolSpec) -> bool:
    return len(spec.actions) == 1


def _shape(prop: dict[str, Any]) -> str:
    return json.dumps(
        {k: v for k, v in prop.items() if k not in ("description", "default")}, sort_keys=True
    )


def _resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """A `$ref` to a shared enum replaced by that enum's schema (one level)."""
    ref = prop.get("$ref", "")
    if ref.startswith("#/$defs/") and ref[len("#/$defs/") :] in defs:
        return {**defs[ref[len("#/$defs/") :]], **{k: v for k, v in prop.items() if k != "$ref"}}
    return prop


def _merged_prop(shapes: list[dict[str, Any]], defs: dict[str, Any]) -> dict[str, Any]:
    """Several actions' schemas for one param name as a single schema accepting all of them."""
    resolved = [_resolve(p, defs) for p in shapes]
    enums: list[str] = []
    all_enums = True
    for r in resolved:
        for o in [_resolve(o, defs) for o in r.get("anyOf", [r])]:
            if o.get("type") == "null":
                continue
            if isinstance(o.get("enum"), list) and all(isinstance(e, str) for e in o["enum"]):
                enums.extend(e for e in o["enum"] if e not in enums)
            else:
                all_enums = False
    if all_enums and enums:
        return {"type": "string", "enum": enums}
    out: list[dict[str, Any]] = []
    for r in resolved:
        for o in r.get("anyOf", [r]):
            clean = {k: v for k, v in o.items() if k not in ("description", "default")}
            if clean not in out:
                out.append(clean)
    return {"anyOf": out}


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    """`{action, params, session_ref}`; params merges every action's fields (shared $defs)."""
    props: dict[str, Any] = {}
    used: dict[str, list[str]] = {}
    defs: dict[str, Any] = {}
    variants: dict[str, list[dict[str, Any]]] = {}
    for name, action in spec.actions.items():
        schema = _strip_titles(action.params.model_json_schema())
        defs.update(schema.get("$defs", {}))
        for key, prop in schema.get("properties", {}).items():
            props.setdefault(key, prop)
            used.setdefault(key, []).append(name)
            if _shape(prop) not in [_shape(v) for v in variants.setdefault(key, [])]:
                variants[key].append(prop)
    # One name, different shapes across actions (`kind` is the item kind for `list` and the
    # link kind for `link`): a strict client validates against the merged schema, so the merged
    # field must accept every action's values. Enums of strings fold into one enum; anything
    # else becomes anyOf. Otherwise the first action's shape wins and `link` is unusable from
    # clients that validate.
    for key, shapes in variants.items():
        if len(shapes) > 1:
            props[key] = _merged_prop(shapes, defs)
    if not single_action(spec):
        for key, prop in props.items():
            # A default from one action is not a default for the merged field. Clients
            # that fill it in can inject invalid extras or turn a PATCH into a reset.
            # Keep defaults in single-action schemas and nested objects/$defs intact.
            prop.pop("default", None)
            note = f"For: {', '.join(used[key])}."
            prop["description"] = f"{prop['description']} {note}" if "description" in prop else note

    out: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
    if not single_action(spec):
        out["properties"]["action"] = {"type": "string", "enum": list(spec.actions)}
        out["required"] = ["action"]
    if props:
        out["properties"]["params"] = {
            "type": "object",
            "description": "Fields for the chosen action; see the tool description.",
            "properties": props,
        }
    if any(a.scope != "read" for a in spec.actions.values()):
        out["properties"]["session_ref"] = {
            "type": "string",
            "maxLength": 200,
            "description": "Optional: your run or session id, recorded on every event written.",
        }
    if defs:
        out["$defs"] = defs
    return out


def _action_line(name: str, action: Action) -> str:
    fields = action.params.model_fields
    required = [k for k, f in fields.items() if f.is_required()]
    optional = [k for k, f in fields.items() if not f.is_required()]
    parts = [", ".join(required)] if required else []
    if optional:
        parts.append(f"optional {', '.join(optional)}")
    return f"- {name}: {action.doc}." + (f" Params: {'; '.join(parts)}." if parts else "")


def description(spec: ToolSpec) -> str:
    lines = [spec.about, RULE]
    if single_action(spec):
        lines.append(f"Returns {spec.actions[next(iter(spec.actions))].doc}.")
    else:
        lines += ["Actions:", *(_action_line(n, a) for n, a in spec.actions.items())]
    lines.append(f"Example: {json.dumps(spec.example)}")
    return "\n".join(lines)


def tool_list() -> list[mcp_types.Tool]:
    return [
        mcp_types.Tool(
            name=spec.name,
            title=spec.title,
            description=description(spec),
            input_schema=input_schema(spec),
            annotations=mcp_types.ToolAnnotations(
                title=spec.title,
                read_only_hint=spec.read_only,
                destructive_hint=None if spec.read_only else spec.destructive,
                open_world_hint=False,
            ),
        )
        for spec in TOOLS.values()
    ]
