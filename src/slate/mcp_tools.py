"""The MCP tool table: params models, actions and their two bindings, schemas, descriptions.

One `ToolSpec` per resource; each `Action` has a scope, params, `local` and `rest` (the
equivalent REST call, used by stdio mode). **An action is the REST handler plus an id**:
`bind(handler, doc, params)` reads the method, path, path ids, query parameters, body model
and scope from the handler's route. Its params are the REST request model
(`with_id(ItemPatch, "id")`, or the body model itself by default) or a model derived from the
GET handler's query parameters (`query_model(items_api.query_items)`), and `local` runs the
REST handler itself through `via`, so a field added to a REST route reaches agents without a
second copy here. Only the one-line `doc` an agent reads is written here.
`tests/test_mcp_parity.py` fails on any drift in params or scope. The few actions whose local
path differs from REST (batch, check-in, the work loop's `next` and `finish`, `me`) pass their
own `local`; three (`slate_share who` and `private`, `slate_automation runs`) pick one of two
routes and are written out. `slate.mcp_server` serves this table over streamable HTTP and
stdio. See docs/mcp-server.md.
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
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model, model_validator
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
from slate.api import work as work_api
from slate.api.accounts import AccountPatch, TokenIn, me_view
from slate.api.auth import Auth
from slate.api.automations import AutomationPatch, RunNowIn
from slate.api.chat import MessageIn, MessagePatch, ReadIn, TaskIn
from slate.api.doc_comments import DocCommentAssignIn, DocCommentIn, DocCommentPatch
from slate.api.docs import (
    Base64FileIn,
    Base64RevisionIn,
    DocDuplicateIn,
    DocMoveIn,
    DocPatch,
    DocTaskIn,
    PinIn,
    PromoteIn,
)
from slate.api.events import CURSOR_PATTERN
from slate.api.permissions import RoleIn
from slate.api.recurrences import AdoptIn, RecurrencePatch
from slate.api.reminders import ReminderIn
from slate.api.schemas import (
    ChecklistEntryIn,
    ChecklistEntryPatch,
    ChecklistIn,
    ChecklistPatch,
    CommentIn,
    ItemOut,
    ItemPatch,
    LinkIn,
    ListPatch,
    ReactionIn,
    ReportIn,
    SectionPatch,
    SplitIn,
    StatusesPut,
    TagMergeIn,
    TagPatch,
    WatchersIn,
    WatchIn,
)
from slate.api.views import ViewPatch
from slate.api.work import FinishIn, FinishOut, HeartbeatIn, NextIn, NextOut
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


def omit(model: type[BaseModel], *names: str) -> type[BaseModel]:
    """A strict copy of `model` without `names`. The REST model keeps those fields."""
    fields: dict[str, Any] = {}
    for key, info in model.model_fields.items():
        if key not in names:
            fields[key] = (info.annotation, info)
    return create_model(
        f"{model.__name__}Sans{''.join(_camel(n) for n in names)}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


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


_MISSING = object()


def _pick(p: BaseModel, source: Any, in_path: bool = False) -> Any:
    """What params `p` give a handler parameter: `source(p)` when `source` is a function,
    else the field named `source`; a path id that no field names is the params' `id`."""
    if callable(source):
        return source(p)
    fields = type(p).model_fields
    if source in fields:
        return getattr(p, source)
    if in_path and "id" in fields:
        return getattr(p, "id")  # noqa: B009 - `p.id` is not on every params model
    return _MISSING


def _body_model(fn: Callable[..., Any]) -> type[BaseModel] | None:
    hints = typing.get_type_hints(fn, include_extras=True)
    for name in inspect.signature(fn).parameters:
        kind = _unwrap(hints[name])
        if inspect.isclass(kind) and issubclass(kind, BaseModel):
            return kind
    return None


def via(
    fn: Callable[..., Any], *, empty: Callable[[Any], Any] | None = None, **binds: Any
) -> Callable[[Local, Any], Any]:
    """The local binding that runs REST handler `fn` as the MCP caller.

    Each handler parameter is filled by its annotation: the `Auth` from the caller's principal
    and actor, the `Session`, the body model from the params (`_body`), and every other (path
    or query) parameter from the params field of the same name, or as `binds` says: a field
    name (`key="channel"`) or a function of the params (`fields=lambda p: ...`). A path id
    that no params field names (`item_id` in `/items/{item_id}`) is the params' `id`. The
    answer is rendered with the route's `response_model`; a 204 route answers `empty(params)`.
    """
    route = _route_of(fn)
    path_ids = {param.name for param in route.dependant.path_params}
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
            source = binds.get(name, name)
            plan.append((name, "value", (source, param.default, name in path_ids)))
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
                source, default, in_path = what
                value = _pick(p, source, in_path)
                if value is not _MISSING:
                    kwargs[name] = value
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


class WorkNext(omit(NextIn, "resume")):
    wait: int = Field(
        default=0, ge=0, le=MCP_WAIT, description="Seconds to wait for a task when none is free"
    )


class WorkFinish(omit(FinishIn, "comment")):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {"properties": {"outcome": {"const": "failed"}}, "required": ["outcome"]},
            "then": {
                "required": ["note"],
                "properties": {"note": {"type": "string", "pattern": r"\S"}},
            },
            "else": {
                "if": {
                    "properties": {"hand_to": {"type": "string", "minLength": 1}},
                    "required": ["hand_to"],
                },
                "else": {"not": {"required": ["note"]}},
            },
        },
    )
    item_id: str = Field(description="The task you hold")
    note: str | None = Field(
        default=None,
        max_length=4000,
        description="With failed: what broke, never posted. With hand_to: the hand-off message.",
    )

    @model_validator(mode="after")
    def failure_note(self):
        if self.outcome == "failed":
            if not (self.note or "").strip():
                raise ValueError("failed needs a note: what broke")
        elif not self.hand_to and "note" in self.model_fields_set:
            raise ValueError("note is only for failed or hand_to; use reply to post a comment")
        return self


class RecurrenceRef(P):
    id: str = Field(description="Recurrence rule id")


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


def _scope_of(route: Any) -> str:
    """The scope the route's `require(...)` dependency demands."""
    stack, found = list(route.dependant.dependencies), set()
    while stack:
        dep = stack.pop()
        if isinstance(getattr(dep.call, "scope", None), str):
            found.add(dep.call.scope)
        stack.extend(dep.dependencies)
    if len(found) != 1:
        raise LookupError(f"{route.path}: expected one required scope, found {sorted(found)}")
    return found.pop()


def _default_params(fn: Callable[..., Any], route: Any) -> type[BaseModel]:
    if route.dependant.path_params:
        raise TypeError(f"{fn.__qualname__} has a path id: name its params model")
    body = _body_model(fn)
    if body is not None:
        return strict(body)
    return query_model(fn) if route.dependant.query_params else NoParams


def bind(
    fn: Callable[..., Any],
    doc: str,
    params: type[BaseModel] | None = None,
    *,
    send: Literal["given", "set", "all"] | None = None,
    empty: Callable[[Any], Any] | None = None,
    local: Callable[[Local, Any], Any] | None = None,
    **binds: Any,
) -> Action:
    """The action for REST handler `fn`: its scope from the route's `require`, both bindings
    from the route, `doc` the line agents read.

    `params` defaults to the route's body model (`strict`), its query parameters
    (`query_model`) or none. Path ids, query parameters and the body are read from the params
    as `via` reads them (`binds`, the `id` fallback). `send` says which params go over REST:
    `given` (not null; the default), `set` (what the caller sent; the default for PATCH) or
    `all`. `local` replaces the in-process binding when it is more than the handler.
    """
    route = _route_of(fn)
    (method,) = route.methods
    params = params or _default_params(fn, route)
    send = send or ("set" if method == "PATCH" else "given")
    body_model = _body_model(fn)
    path_ids = [(f.name, f.alias) for f in route.dependant.path_params]
    queries = [(f.name, f.alias) for f in route.dependant.query_params]

    def rest(p: Any) -> Rest:
        path = route.path.removeprefix("/api/v1")
        for name, alias in path_ids:
            path = path.replace(f"{{{alias}}}", _q(str(_pick(p, binds.get(name, name), True))))
        sent = p.model_fields_set if send == "set" else type(p).model_fields
        query: dict[str, Any] = {}
        for name, alias in queries:
            source = binds.get(name, name)
            if callable(source) or source in sent:
                value = _pick(p, source)
                if value is not None or send != "given":
                    query[alias] = value
        body = None
        if body_model is not None:
            data = p.model_dump(exclude_unset=send == "set", exclude_none=send == "given")
            body = jsonable({k: v for k, v in data.items() if k in body_model.model_fields})
        return Rest(method, path, query or None, body, empty(p) if empty else None)

    return Action(
        doc,
        _scope_of(route),
        params,
        rest,
        local or via(fn, empty=empty, **binds),
    )


def _dump(model: type[BaseModel], value: Any) -> Any:
    return model.model_validate(value, from_attributes=True).model_dump(mode="json")


def item_views(lc: Local, rows: list, *, now: bool = False) -> list[dict]:
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
    "get": bind(items_api.get_item, "the item with subtasks, comments and last 20 events", ItemRef),
    "list": bind(
        items_api.query_items,
        "items matching every filter given (AND); `fields` trims each item, `changed_since` "
        "returns only items changed after an event id, `limit`/`offset` page",
        ItemQuery,
        fields=_joined("fields"),
        ids=_joined("ids"),
    ),
    "create": bind(
        items_api.create_item, "file ONE item in list_id (parent_id instead makes it one subtask)"
    ),
    "update": bind(
        items_api.update_item,
        "change any fields; null clears; status, assignees and tags replace the set",
        with_id(ItemPatch, "id", "Item id"),
    ),
    "report": bind(
        items_api.report,
        "say what you are doing on it: working, blocked, review or failed sets a badge; "
        "done and idle clear it. Take a task with slate_work next",
        with_id(ReportIn, "id", "Item id"),
        send="set",
    ),
    "run": bind(
        items_api.run,
        "Run now: ask the item's agent assignees to start on it now (records "
        "item.run_requested; their runners take it at once). Refused when no agent is assigned "
        "or it is done, cancelled or archived",
        ItemRef,
    ),
    "stop": bind(
        items_api.stop,
        "Stop: ask the agent working on the item to stop (records item.stop_requested; its "
        "runner ends the run, which finishes answered, saying where it stopped). Refused when "
        "no agent is working on it",
        ItemRef,
    ),
    "brief": bind(
        items_api.get_brief,
        "the task as one paste-ready prompt (title, due, description, checklists, recent "
        "comments) ending with the run rule: finish with a last line `SLATE: done`, `SLATE: "
        "review`, `SLATE: later`, `SLATE: answered` or `SLATE: ask <handle>: <question> "
        "[option | option]`; a comment ending so reports back",
        query_model(items_api.get_brief, id=ITEM_ID),
    ),
    "bulk_update": bind(
        items_api.bulk_update, "apply one change to many items at once (all or nothing; up to 500)"
    ),
    "archive": bind(
        items_api.archive_item, "hide it and its subtasks (soft; history stays)", ItemRef
    ),
    "unarchive": bind(items_api.unarchive_item, "bring it back", ItemRef),
    "trash": bind(
        items_api.trash_item,
        "move it (and its subtasks) to the trash: hidden, restorable, deleted for good after "
        "the trash period (30 days by default)",
        ItemRef,
    ),
    "restore": bind(items_api.restore_item, "take it out of the trash", ItemRef),
    "purge": bind(
        items_api.purge_item,
        "delete a trashed item for good (history stays)",
        ItemRef,
        empty=_deleted("purged"),
    ),
    "split": bind(
        items_api.split_item,
        "create several subtasks under it: the only multi-child path",
        with_id(SplitIn, "id", "Item id"),
    ),
    "delete": bind(
        items_api.delete_item,
        "undo your own create within 10 minutes; otherwise archive",
        ItemRef,
        empty=_deleted("deleted"),
    ),
    "link": bind(
        links_api.add_link,
        "add a link: kind blocks / blocked_by / relates / duplicate_of / duplicated_by, from this "
        "item's side",
        with_id(LinkIn, "id", "Item id"),
        item_id="id",
    ),
    "unlink": bind(
        links_api.remove_link,
        "remove a dependency (same kind and item_id as when added)",
        with_id(LinkIn, "id", "Item id"),
        item_id="id",
        other="item_id",
    ),
    "activity": bind(
        items_api.item_activity,
        "comments and history in one list, oldest first (what people see beside the task)",
        query_model(items_api.item_activity, id=ITEM_ID),
        send="set",
    ),
    "watch": bind(
        items_api.watch,
        "add a watcher (you, or `account`): watchers hear about every change to the item "
        "(check-in `watching`, inbox)",
        with_id(WatchIn, "id", "Item id"),
    ),
    "unwatch": bind(
        items_api.unwatch, "remove a watcher (you, or `account`)", with_id(WatchIn, "id", "Item id")
    ),
    "watchers": bind(items_api.list_watchers, "who follows the item", ItemRef),
    "set_watchers": bind(
        items_api.set_watchers,
        "replace who follows the item (handles or ids)",
        with_id(WatchersIn, "id", "Item id"),
    ),
    "attachments": bind(
        attachments_api.list_attachments,
        "the item's files: id, filename, size, content type",
        ItemRef,
    ),
    "detach": bind(
        attachments_api.remove,
        "remove one attachment by its id",
        AttachmentRef,
        empty=_deleted("deleted"),
    ),
    "attach": bind(
        attachments_api.add_attachment_base64,
        "attach a file (base64) to the item",
        with_id(attachments_api.Base64In, "id", "Item id"),
    ),
    "promote_attachment": bind(
        docs_api.promote_attachment,
        "move an attachment into Docs & Files: a file doc with the bytes as revision 1; "
        "the task gets a comment linking it",
        with_id(PromoteIn, "id", "Attachment id"),
    ),
    "history": bind(items_api.item_events, "every event on the item, oldest first", ItemRef),
    "versions": bind(items_api.item_versions, "every body version, oldest first", ItemRef),
    "revert": bind(
        events_api.revert_event,
        "undo one change from the history (an item.updated field, item.status_changed, "
        "item.assigned or item.moved event) by issuing its inverse as you: a new event, "
        "session_ref revert:<event_id>. Refused when the field has changed again since",
        EventRef,
    ),
}

# Lists, sections, tags ----------------------------------------------------------------------

LIST_ACTIONS = {
    "list": bind(lists_api.list_lists, "lists with their statuses"),
    "create": bind(
        lists_api.create_list, "a list in a section (default statuses open, active, done)"
    ),
    "update": bind(
        lists_api.update_list,
        "rename, reorder, set default_status, or archived true/false",
        with_id(ListPatch, "id", "List id"),
    ),
    "set_statuses": bind(
        lists_api.set_statuses,
        "replace the statuses (columns) in order; fallback for stranded items",
        with_id(StatusesPut, "id", "List id"),
        send="all",
    ),
}

SECTION_ACTIONS = {
    "list": bind(sections_api.list_sections, "all sections"),
    "create": bind(
        sections_api.create_section, "a section (one per audience or rhythm, never per topic)"
    ),
    "update": bind(
        sections_api.update_section,
        "rename, reorder, change audience, or archived true/false",
        with_id(SectionPatch, "id", "Section id"),
    ),
}

TAG_ACTIONS = {
    "list": bind(tags_api.list_tags, "all tags with item_count"),
    "create": bind(tags_api.create_tag, "a tag (stored lowercase)"),
    "rename": bind(
        tags_api.update_tag,
        "rename or recolor a tag; items keep it",
        with_id(TagPatch, "tag", "Current tag name or id"),
    ),
    "merge": bind(
        tags_api.merge_tag,
        "fold a tag into another: items and rules carry `into` instead; the tag is deleted",
        with_id(TagMergeIn, "tag", "The tag (name or id) to fold away"),
    ),
    "delete": bind(
        tags_api.delete_tag,
        "remove a tag from every item and rule, then delete it",
        TagRef,
        empty=_deleted("deleted", "tag"),
    ),
}

# Recurrences --------------------------------------------------------------------------------

RECURRENCE_ACTIONS = {
    "preview": bind(
        recurrences_api.preview,
        "try an RRULE without saving: next dates and the rule in plain English",
    ),
    "upcoming": bind(
        recurrences_api.upcoming,
        "dates scheduled rules will fill in a window that are not items yet",
    ),
    "list": bind(recurrences_api.list_recurrences, "recurrence rules, optionally in one list"),
    "get": bind(
        recurrences_api.get_recurrence, "one rule with its next occurrences", RecurrenceRef
    ),
    "create": bind(recurrences_api.create_recurrence, "a rule that spawns one item per occurrence"),
    "update": bind(
        recurrences_api.update_recurrence,
        "change a rule (`ended: true` ends it for good)",
        with_id(RecurrencePatch, "id", "Recurrence rule id"),
    ),
    "pause": bind(recurrences_api.pause_recurrence, "stop spawning", RecurrenceRef),
    "resume": bind(
        recurrences_api.resume_recurrence, "start spawning again from today", RecurrenceRef
    ),
    "adopt": bind(
        recurrences_api.adopt,
        "make an existing item the rule's current occurrence (it keeps its due date)",
        with_id(AdoptIn, "id", "Recurrence rule id"),
    ),
    "run_now": bind(
        recurrences_api.run_now,
        "spawn everything due within the rule's lead window now",
        RecurrenceRef,
    ),
}

# Accounts, sharing, audit -------------------------------------------------------------------


def _me(lc: Local) -> dict:
    # GET /me also renews a browser session cookie; the MCP side only needs the view.
    return me_view(lc.session, lc.principal).model_dump(mode="json")


ACCOUNT_ID = (str, Field(description="Account id (from `list` or `me`)"))

ACCOUNT_ACTIONS = {
    "me": bind(accounts_api.me, "who this token is, and its scopes", local=lambda lc, p: _me(lc)),
    "list": bind(accounts_api.list_accounts, "all accounts (admin)"),
    "create": bind(accounts_api.create_account, "a person or agent account (admin)"),
    "create_token": bind(
        accounts_api.create_token,
        "issue a token; its plaintext is shown only in this result (admin)",
        with_id(TokenIn, "account_id", "Account id"),
    ),
    "update": bind(
        accounts_api.update_account,
        "rename an account, or disable it (`disabled: true` stops its tokens at once) (admin)",
        with_id(AccountPatch, "account_id", "Account id"),
    ),
    "tokens": bind(
        accounts_api.list_tokens,
        "an account's tokens with last use, never the secret (admin, or your own)",
        query_model(accounts_api.list_tokens, account_id=ACCOUNT_ID),
        send="all",
    ),
    "usage": bind(
        accounts_api.account_usage,
        "events per day by source, busiest event types, requests per token (admin, or yours)",
        query_model(accounts_api.account_usage, account_id=ACCOUNT_ID),
        send="all",
    ),
    "revoke_token": bind(
        accounts_api.revoke_token,
        "revoke a token at once (admin, or your own token)",
        TokenRef,
        empty=lambda p: {"revoked": True},
    ),
    "set_role": bind(
        permissions_api.set_role,
        "change an account's role: owner, admin, member, guest (admin; admins only by the owner)",
        with_id(RoleIn, "account_id", "Account id (from `list` or `me`)"),
    ),
    "access": bind(
        permissions_api.account_access,
        "every section and list an account reaches, with its level (admin, or yourself)",
        AccountRef,
    ),
    "agent_rules": bind(
        settings_api.get_agent_rules,
        "what agent accounts may never do here, whatever their token (admin)",
    ),
    "set_agent_rules": bind(
        settings_api.set_agent_rules,
        "replace what agents may never do: `denied` lists action keys (owner or admin person)",
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
    "list": bind(permissions_api.list_shares, "shares, by account and/or section/list (admin)"),
    "set": bind(
        permissions_api.put_share,
        "share a section or list with an account at view, comment or edit; level null revokes "
        "(admin)",
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
    "list": bind(
        permissions_api.audit_log,
        "who (person or agent) did what, when, through which door; newest first, filterable; "
        "pass next_before back as before",
    ),
    "facets": bind(permissions_api.audit_facets, "event types and actors in the log you can see"),
}

# Today, check-in, work, health, agents, events ----------------------------------------------

TODAY_ACTIONS = {
    "get": bind(
        today_api.get_today,
        "overdue, due today, waiting on you (`fields` trims each item)",
        query_model(today_api.get_today, fields=FIELDS),
        fields=_joined("fields"),
    )
}

# Not a listed tool. `slate://today` still renders this. Agents use slate_work and slate_item.
TODAY_SPEC = ToolSpec(
    "slate_today",
    "Slate today",
    "Overdue, due today and waiting on you.",
    {},
    TODAY_ACTIONS,
    read_only=True,
)


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
    "get": bind(
        checkin_api.get_checkin,
        "your open items (queue) and others' events that concern you (news, with why), "
        "from a cursor",
        CheckinQuery,
        fields=_joined("fields"),
        local=_checkin,
    )
}


def _take_next(lc: Local, p: WorkNext) -> dict:
    body = NextIn.model_validate(p.model_dump(exclude_unset=True))
    got = longpoll.wait_sync(
        lambda: work_api.take_next(lc.session, lc.actor, body), p.wait, lc.session
    )
    return got or work_api.nothing()


def _work_finish(lc: Local, p: WorkFinish) -> dict:
    item = work.finish(lc.session, lc.actor, p.item_id, **_fields(p, "item_id"))
    return _dump(FinishOut, {"item": items.views(lc.session, [item])[0]})


WORK_ACTIONS = {
    "reconcile": bind(
        work_api.reconcile,
        "confirm remote termination with run proof and actual runtime evidence; "
        "unlock without completing",
        with_id(work_api.ReconcileIn, "item_id", "The task you hold"),
    ),
    "chat_heartbeat": bind(work_api.chat_heartbeat, "renew a chat turn with its run_id proof"),
    "chat_finish": bind(
        work_api.chat_finish,
        "settle only the proved chat attempt; identical retries return its durable receipt",
    ),
    "chat_reconcile": bind(
        work_api.chat_reconcile,
        "confirm remote chat termination with run proof and actual runtime evidence",
    ),
    "doc_heartbeat": bind(
        work_api.doc_heartbeat, "renew a doc turn (a page mention from next) with its run_id proof"
    ),
    "doc_finish": bind(
        work_api.doc_finish,
        "end a doc turn: done with reply posts your comment in the thread (a new discussion "
        "thread for a mention in the page body); release posts nothing. Identical retries "
        "return its durable receipt",
    ),
    "doc_reconcile": bind(
        work_api.doc_reconcile,
        "confirm remote doc-turn termination with run proof and actual runtime evidence",
    ),
    "next": bind(
        work_api.next_task,
        "take your next task: the best open one assigned to you or waiting on you (pool: also "
        "unassigned ones) that is not blocked, not held by another agent and not waiting on a "
        "person; priority order, then due date. It is yours for lease_seconds; "
        "item is null when none is free (wait: long-poll for one)",
        WorkNext,
        local=lambda lc, p: _dump(NextOut, _take_next(lc, p)),
    ),
    "heartbeat": bind(
        work_api.heartbeat,
        "keep holding the task (the lease runs lease_seconds from now); note/progress update "
        "the badge. The answer carries messages (Asks sent to you since the take: read them, "
        "then heartbeat with seen: [ids]) and stop (someone asked you to stop: finish answered, "
        "saying where you stopped). An error saying you do not hold it means stop: another "
        "agent has it",
        with_id(HeartbeatIn, "item_id", "The task you hold"),
    ),
    "activity": bind(
        work_api.post_activity,
        "say what you are doing on a task you hold, as it happens (lines: short strings); "
        "shown live in the task window, never stored",
        with_id(work_api.LiveActivityIn, "item_id", "The task you hold"),
    ),
    "finish": bind(
        work_api.finish,
        "report back (run_id from next's lease). outcome is done, review, later, ask "
        "(question: one line), answered (reply), failed (note: what broke), blocked or "
        "release. reply is your comment. status says where the card stands in one line "
        "(required with review). Take the next "
        "task with next, in its own call",
        WorkFinish,
        local=_work_finish,
    ),
    "held": bind(work_api.my_leases, "the tasks you hold now and when each lease runs out"),
}

HEALTH_ACTIONS = {
    "get": bind(
        health_api.get_health,
        "Slate's health (owners and admins): overall status and checks, background pollers "
        "(last run, last success, errors), engines, calls and errors per token in the last "
        "hour, latency percentiles, failed automation runs, queues, database size",
    )
}

AGENT_ACTIONS = {
    "list": bind(
        agents_api.list_agents,
        "each agent's presence (online, last_seen_at), current reports (working), open "
        "assignments and events in the last 24 h",
        send="all",
    )
}

EVENT_ACTIONS = {
    "poll": bind(events_api.list_events, "events after `since`, oldest first; pass next_since back")
}

# Views, favorites ---------------------------------------------------------------------------

VIEW_ACTIONS = {
    "list": bind(views_api.list_views, "saved views, optionally for one scope"),
    "get": bind(views_api.get_view, "one saved view", ViewRef),
    "create": bind(
        views_api.create_view,
        "save a view. config example: {layout: board, group_by: due, due_window_days: 6, "
        "sort: [{field: priority, dir: desc}], filter: {op: and, rules: [{field: tag, cmp: any, "
        "value: [home]}]}, show_closed: false}",
    ),
    "update": bind(
        views_api.update_view,
        "rename, reconfigure, reorder or share a view",
        with_id(ViewPatch, "id", "View id"),
    ),
    "archive": bind(views_api.archive_view, "hide a view", ViewRef),
    "unarchive": bind(views_api.unarchive_view, "bring an archived view back", ViewRef),
}

FAVORITE_ACTIONS = {
    "list": bind(
        favorites_api.list_favorites,
        "your favorites in bar order, each with its target's name and scope",
    ),
    "add": bind(
        favorites_api.add_favorite,
        "pin a list, section, saved view or item (kind + target_id); pinning twice is a no-op",
    ),
    "remove": bind(
        favorites_api.remove_favorite,
        "unpin by favorite id",
        FavoriteRef,
        empty=_deleted("removed"),
    ),
    "reorder": bind(
        favorites_api.reorder_favorites, "set the bar order; ids must list every favorite once"
    ),
}

# Comments, chat, checklists -----------------------------------------------------------------

COMMENT_ACTIONS = {
    "list": bind(
        comments_api.list_comments, "the item's comments, oldest first, with reactions", ItemRef
    ),
    "add": bind(
        comments_api.add_comment,
        "comment on item id; @handle mentions an account; assignee makes it an Ask (open in "
        "their queue until explicitly resolved); replies notify earlier participants but "
        "do not resolve Asks; keep_open is accepted for compatibility",
        with_id(CommentIn, "id", "Item id"),
        send="set",
    ),
    "assign": bind(
        comments_api.edit_comment,
        "address a comment to an account, or take it off with assignee null (id is the "
        "comment id; the author or an admin)",
        CommentAssign,
    ),
    "resolve": bind(
        comments_api.resolve,
        "close an Ask (id is the comment id; its assignee, author or an admin)",
        CommentRef,
    ),
    "answer": bind(
        comments_api.answer,
        "answer an agent's question (a comment with ask) with one of its options: posts your "
        "reply, the card stops waiting on you and the agent is woken (id is the comment id; "
        "the person asked or an admin)",
        with_id(comments_api.AnswerIn, "id", "The question's comment id"),
    ),
    "edit": bind(
        comments_api.edit_comment, "edit your own comment (id is the comment id)", CommentEdit
    ),
    "delete": bind(
        comments_api.delete_comment,
        "delete a comment for good (id is the comment id; your own, or anyone's as an admin)",
        CommentRef,
        empty=_deleted("deleted"),
    ),
    "run": bind(
        comments_api.run_log,
        "the run log kept with an agent's answer: what its run did while it worked (id is "
        "the comment id; a comment with one carries run_steps)",
        CommentRef,
    ),
    "react": bind(
        comments_api.react,
        "add your emoji reaction to a comment",
        with_id(ReactionIn, "id", "Comment id"),
    ),
    "unreact": bind(
        comments_api.unreact, "remove your emoji reaction", with_id(ReactionIn, "id", "Comment id")
    ),
}

CHAT_ACTIONS = {
    "channels": bind(
        chat_api.list_channels, "every channel in board order with your unread and mention counts"
    ),
    "messages": bind(
        chat_api.list_messages,
        "a page of a channel, oldest first (newest top-level by default; after: follow it)",
        query_model(chat_api.list_messages, channel=CHANNEL),
        key="channel",
    ),
    "thread": bind(chat_api.get_thread, "a thread: its first message then every reply", MessageRef),
    "upload_file": bind(
        chat_api.upload_file_base64,
        "upload a file (base64) to a channel, then send it: post with its id in file_ids",
        with_id(attachments_api.Base64In, "channel", CHANNEL_DOC),
        key="channel",
    ),
    "discard_file": bind(
        chat_api.discard_file,
        "take back an upload you have not sent",
        MessageRef,
        empty=_deleted("deleted"),
    ),
    "post": bind(
        chat_api.post_message,
        "post to a channel, or reply in a thread with parent_id; file_ids sends your uploads",
        with_id(MessageIn, "channel", CHANNEL_DOC),
        key="channel",
    ),
    "edit": bind(
        chat_api.edit_message, "edit your own message", with_id(MessagePatch, "id", "Message id")
    ),
    "delete": bind(
        chat_api.delete_message, "delete your own message (its thread stays)", MessageRef
    ),
    "react": bind(
        chat_api.react,
        "add your emoji reaction to a message",
        with_id(ReactionIn, "id", "Message id"),
    ),
    "unreact": bind(
        chat_api.unreact, "remove your emoji reaction", with_id(ReactionIn, "id", "Message id")
    ),
    "task": bind(
        chat_api.make_task,
        "turn a message into one task (in the channel's list unless list_id)",
        with_id(TaskIn, "id", "Message id"),
    ),
    "read": bind(
        chat_api.mark_read,
        "mark a channel read up to through (default its newest message)",
        with_id(ReadIn, "channel", CHANNEL_DOC),
        key="channel",
    ),
    "read_all": bind(chat_api.read_all, "mark every channel read"),
}

CHECKLIST_ACTIONS = {
    "list": bind(
        checklists_api.list_checklists, "the item's checklists with their entries", ItemRef
    ),
    "create": bind(
        checklists_api.create_checklist,
        "a checklist on item_id, optionally with its first entries",
        with_id(ChecklistIn, "item_id", "Item id"),
        send="all",
    ),
    "rename": bind(
        checklists_api.update_checklist,
        "rename a checklist (position reorders it)",
        with_id(ChecklistPatch, "id", "Checklist id"),
    ),
    "delete": bind(
        checklists_api.delete_checklist,
        "remove a checklist and its entries",
        ChecklistRef,
        empty=_deleted("deleted"),
    ),
    "add_entry": bind(
        checklists_api.add_entry,
        "add an entry to checklist_id",
        with_id(ChecklistEntryIn, "checklist_id", "Checklist id"),
    ),
    "update_entry": bind(
        checklists_api.update_entry,
        "tick (done true), untick, rename, assign or reorder an entry",
        with_id(ChecklistEntryPatch, "entry_id", "Checklist entry id"),
    ),
    "remove_entry": bind(
        checklists_api.delete_entry,
        "delete an entry",
        ChecklistEntryRef,
        empty=_deleted("deleted", "entry_id"),
    ),
}

# Automations --------------------------------------------------------------------------------

_runs_of = via(automations_api.automation_runs, automation_id="id")
_all_runs = via(automations_api.all_runs)

AUTOMATION_ACTIONS = {
    "catalog": bind(
        automations_api.catalog, "every trigger, condition field and action with its parameters"
    ),
    "list": bind(
        automations_api.list_automations,
        "rules, each with a plain-English description and its last run",
    ),
    "get": bind(automations_api.get_automation, "one rule", AutomationRef),
    "create": bind(
        automations_api.create_automation,
        "name, trigger {type,...}, actions [{type,...}], optional conditions and list_id or "
        "section_id (neither = every list); see catalog",
        send="all",
    ),
    "update": bind(
        automations_api.update_automation,
        "change any field; enabled false pauses the rule",
        with_id(AutomationPatch, "id", "Automation id"),
    ),
    "delete": bind(
        automations_api.delete_automation,
        "remove a rule and its run log (its history stays)",
        AutomationRef,
        empty=_deleted("deleted"),
    ),
    "run": bind(
        automations_api.run_automation,
        "run a rule's actions on one item now (trigger and conditions skipped)",
        with_id(RunNowIn, "id", "Automation id"),
    ),
    "rotate_secret": bind(
        automations_api.rotate_secret,
        "a new secret for verifying the rule's webhook signatures (shown once)",
        AutomationRef,
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
    "list": bind(
        docs_api.list_docs, "pages in tree order without bodies; q searches titles and bodies"
    ),
    "get": bind(
        docs_api.get_doc,
        "one page: body, path, subpages, outline, embedded tasks, links, backlinks, files",
        DocRefP,
    ),
    "templates": bind(docs_api.templates, "starting points for create's template"),
    "create": bind(
        docs_api.create_doc,
        "a page in list_id, section_id, the workspace (neither) or under parent_id; "
        "body_md is Markdown, [[task:<id>]] embeds a task card, [[doc:<id>]] links a page",
    ),
    "update": bind(
        docs_api.update_doc,
        "edit title, icon or body: body_md replaces, append_md adds at the end, "
        "replace_section {heading, body_md} rewrites one part; base_revision guards against "
        "overwriting a newer edit; note names a page-history version",
        with_id(DocPatch, "id", "Doc id"),
    ),
    "move": bind(
        docs_api.move_doc,
        "move under parent_id, or to the top of list_id / section_id / the workspace; before_id "
        "orders it",
        with_id(DocMoveIn, "id", "Doc id"),
        send="set",
    ),
    "archive": bind(docs_api.archive_doc, "hide a page and its subpages", DocRefP),
    "unarchive": bind(docs_api.unarchive_doc, "bring an archived page back", DocRefP),
    "delete": bind(
        docs_api.delete_doc, "delete an archived page and its subpages for good", DocRefP
    ),
    "duplicate": bind(
        docs_api.duplicate_doc,
        "copy a page (and its subpages) next to it",
        with_id(DocDuplicateIn, "id", "Doc id"),
    ),
    "versions": bind(docs_api.versions, "page history, newest first (no bodies)", DocRefP),
    "version": bind(docs_api.version, "one version with its body", DocVersionRef),
    "diff": bind(
        docs_api.diff,
        "unified diff of the body between two versions (default to: the current text)",
        query_model(docs_api.diff, id=DOC_ID),
    ),
    "restore": bind(
        docs_api.restore,
        "put an older version's title and body back (as a new version)",
        DocVersionRef,
    ),
    "create_task": bind(
        docs_api.create_task,
        "file one task from the page: replace names the text that becomes its live card "
        f"(else the card is added at the end); the task links back. {RULE}",
        with_id(DocTaskIn, "id", "Doc id"),
    ),
    "attach": bind(
        docs_api.add_file_base64,
        "upload an image or file into the page; paste the returned markdown into the body",
        with_id(Base64FileIn, "id", "Doc id"),
    ),
    "remove_file": bind(
        docs_api.remove_file,
        "remove an uploaded file",
        DocFileRef,
        empty=_deleted("removed", "file_id"),
    ),
    "upload": bind(
        docs_api.add_revision_base64,
        "the next revision of a file doc (a PDF, spreadsheet, photo); the previous revision "
        "stays, superseded; note says what changed and is required",
        with_id(Base64RevisionIn, "id", "Doc id (a file doc: create with kind='file')"),
    ),
    "revisions": bind(
        docs_api.revisions,
        "a file doc's revisions, newest first (metadata; bytes at each url)",
        DocRefP,
    ),
    "restore_revision": bind(
        docs_api.restore_revision,
        "make an older revision of a file doc current again (as a new revision)",
        DocRevisionRef,
    ),
    "hub": bind(
        docs_api.section_documents,
        "a section's documents hub: its drawers with counts, pages outside any drawer, what "
        "needs you (open asks to you, open review threads by others on docs you touched)",
        SectionDocs,
    ),
    "drawer": bind(
        docs_api.drawer,
        "a drawer's entries (folders and documents) at its top or inside a folder, with "
        "revisions, pins, tags, thread counts; a drawer is a list of kind drawer",
        query_model(
            docs_api.drawer,
            list_id=(str, Field(description="The drawer (a list of kind drawer)")),
        ),
    ),
    "to_drawer": bind(
        docs_api.to_drawer,
        "turn a top-level page into a drawer of the same name in its section (its subpages "
        "become the entries; the page moves in too)",
        DocRefP,
    ),
    "pin_revision": bind(
        docs_api.pin_revision,
        "mark a file doc's revision as the one in use; one pin per doc",
        with_id(
            PinIn,
            "id",
            "Doc id (a file doc)",
            revision_no=(int, Field(ge=1, description="The revision in use")),
        ),
        send="all",
    ),
    "unpin_revision": bind(docs_api.unpin_revision, "take the pin off a file doc", DocRefP),
    "pin_to_top": bind(
        docs_api.pin_to_top,
        "pin a document (file, page or folder) to the top of its drawer or folder listing, "
        "for everyone",
        DocRefP,
    ),
    "unpin_from_top": bind(
        docs_api.unpin_from_top,
        "take a document off the top of its drawer or folder listing",
        DocRefP,
    ),
    "compare_revisions": bind(
        docs_api.compare_revisions,
        "what changed between two revisions of a file doc: text files line by line, "
        "CSV/XLSX cell by cell per sheet, PDFs by extracted text (approximate); images none",
        DocCompare,
        from_no="from_revision",
        to_no="to_revision",
    ),
    "comments": bind(
        doc_comments_api.list_comments,
        "every comment on the doc, oldest first (review threads carry an anchor and can be "
        "resolved; discussion has none; replies carry parent_id)",
        DocRefP,
    ),
    "comment": bind(
        doc_comments_api.add_comment,
        "comment on a page or file: with anchor (quote/start/end on a page; page and/or x,y "
        "on a file) a review thread, without one discussion; parent_id replies; "
        "suggestion_md proposes replacement text for the quote; assignee makes it an Ask",
        with_id(DocCommentIn, "id", "Doc id"),
    ),
    "edit_comment": bind(
        doc_comments_api.edit_comment,
        "edit your own doc comment",
        with_id(DocCommentPatch, "comment_id", "Doc comment id"),
    ),
    "resolve_comment": bind(
        doc_comments_api.resolve, "close a review thread or Ask (its first comment)", DocCommentRef
    ),
    "reopen_comment": bind(doc_comments_api.reopen, "reopen a resolved thread", DocCommentRef),
    "assign_comment": bind(
        doc_comments_api.assign,
        "address a doc comment to an account (an Ask), or take it off",
        with_id(DocCommentAssignIn, "comment_id", "Doc comment id"),
        send="all",
    ),
    "accept_suggestion": bind(
        doc_comments_api.accept,
        "apply a review comment's suggestion_md to the page and resolve the thread",
        DocCommentRef,
    ),
    "activity": bind(
        doc_comments_api.activity,
        "the doc's comments plus the events the side panel folds in (revisions, saves, "
        "moves, resolved threads), oldest first",
        query_model(doc_comments_api.activity, id=DOC_ID),
        send="all",
    ),
}

# Reminders ----------------------------------------------------------------------------------

REMINDER_ACTIONS = {
    "add": bind(
        reminders_api.add_reminder,
        "remind you (or `account`) about an item `at` a time, or `before_minutes` before its "
        "due date; it arrives in the inbox, check-in and on phone and desktop. Alerts before "
        "due: one call per lead (5, 15, 30, 60, 120, 1440, 2880, 10080...), any number per item; "
        "the same lead twice is one alert. Setting one for a person from a chat app: pass "
        "`account` (`alex`), else it reminds you",
        with_id(ReminderIn, "item_id", "Item id"),
    ),
    "list": bind(reminders_api.item_reminders, "an item's reminders, fired or not", ReminderItem),
    "upcoming": bind(
        reminders_api.my_reminders,
        "your reminders that have not gone off yet, soonest first",
        send="all",
    ),
    "remove": bind(
        reminders_api.remove_reminder,
        "remove a reminder (yours, or one you set)",
        ReminderRef,
        empty=_deleted("deleted"),
    ),
    "digest": bind(
        reminders_api.my_digest,
        "what your daily digest says right now: overdue, due today, waiting on you",
    ),
}

# Export, batch, inbox, search ---------------------------------------------------------------

EXPORT_ACTIONS = {
    "json": bind(
        data_api.export_data,
        "a list, a section or everything as one JSON document: every task (the item shape) "
        "with comments, checklists and links, plus the lists, statuses, tags and "
        "fields around them; trashed tasks are left out",
        query_model(data_api.export_data, format=None),
        format=lambda p: "json",
    ),
    "empty_trash": bind(data_api.empty_trash, "delete everything in the trash for good (admins)"),
}


def _batch(lc: Local, p: Any) -> dict:
    from slate import batch

    return batch.run_batch(
        lc.session, lc.principal, lc.actor, p.ops, atomic=p.atomic, views=p.views
    )


def _batch_actions() -> dict[str, Action]:
    from slate.api import batch as batch_api
    from slate.batch import BatchIn

    doc = "{atomic, results: [{ok, result | error}]}, one slot per op in order"
    return {"run": bind(batch_api.run_batch, doc, BatchIn, local=_batch)}


INBOX_ACTIONS = {
    "list": bind(
        inbox_api.list_inbox,
        "your notifications, newest first: mentions, assignments, due nudges and changes on "
        "items you are assigned to or watch",
    ),
    "count": bind(inbox_api.inbox_count, "how many are unread"),
    "read": bind(
        inbox_api.mark_read, "mark entries read: event_ids, every entry for an item_id, or all"
    ),
    "clear": bind(inbox_api.clear, "clear entries out of the inbox (same targets as read)"),
}

SEARCH_ACTIONS = {
    "search": bind(
        search_api.search,
        "find q in tasks (title, description), docs (title, page text, file text) and chat "
        "messages at once: typed hits, tasks first, then docs, then messages (newest first); "
        "only what you can read; `%` and `_` are literal",
        send="all",
    )
}


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "slate_item",
            "Slate items",
            "Read and change tasks: file one, update it, report progress, split or archive it.",
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
            "The conversation on a task. @handle mentions an account.",
            {"action": "add", "params": {"id": "Ab12Cd34Ef56", "body_md": "Done, @alex"}},
            COMMENT_ACTIONS,
        ),
        ToolSpec(
            "slate_chat",
            "Slate chat",
            "Channels for general, a list, a section or an agent, with threads and mentions.",
            {"action": "post", "params": {"channel": "general", "body_md": "On it, @alex"}},
            CHAT_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_checklist",
            "Slate checklists",
            "Ticked steps inside one task. They are not subtasks.",
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
            "A saved lens: layout, grouping, sort and filter.",
            {"action": "list", "params": {"scope": "everything"}},
            VIEW_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_favorite",
            "Slate favorites",
            "Your pinned lists, sections, views and tasks.",
            {"action": "add", "params": {"kind": "list", "target_id": "Ab12Cd34Ef56"}},
            FAVORITE_ACTIONS,
        ),
        ToolSpec(
            "slate_automation",
            "Slate automations",
            "When-this-then-that rules. Read catalog before you create one.",
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
            "slate_reminder",
            "Slate reminders",
            "Tell an account about a task at a time, or before it is due.",
            {"action": "add", "params": {"item_id": "Ab12Cd34Ef56", "before_minutes": 60}},
            REMINDER_ACTIONS,
        ),
        ToolSpec(
            "slate_inbox",
            "Slate inbox",
            "Your notifications: mentions, assignments and changes on tasks you follow.",
            {"action": "list", "params": {"unread": True}},
            INBOX_ACTIONS,
        ),
        ToolSpec(
            "slate_list",
            "Slate lists",
            "A list lives in a section and owns its columns. Read its note before you file.",
            {"action": "list", "params": {"section_id": "Ab12Cd34Ef56"}},
            LIST_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_section",
            "Slate sections",
            "The top-level groups. Find one, or create one.",
            {"action": "list", "params": {}},
            SECTION_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_tag",
            "Slate tags",
            "Tags cut across every list. See them before you invent one.",
            {"action": "rename", "params": {"tag": "ai project", "name": "ai-project"}},
            TAG_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_recurrence",
            "Slate recurring rules",
            "A rule that spawns a task on a schedule, or when the current one is done.",
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
            "slate_account",
            "Slate accounts",
            "People and agents. `me` says who you are; the other actions need an admin token.",
            {"action": "me"},
            ACCOUNT_ACTIONS,
        ),
        ToolSpec(
            "slate_checkin",
            "Slate check-in",
            "Your open tasks and what changed for you since a cursor. Take work with slate_work.",
            {"params": {"since": "0001Ab2Cd3Ef"}},
            CHECKIN_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_work",
            "Slate work loop",
            "Take a task, keep the lease while you work, and finish it.",
            {
                "action": "finish",
                "params": {
                    "item_id": "Ab12Cd34Ef56",
                    "run_id": "the run_id from next's lease",
                    "outcome": "review",
                    "status": "Check the invoice totals.",
                },
            },
            WORK_ACTIONS,
        ),
        ToolSpec(
            "slate_doc",
            "Slate docs",
            "Markdown pages and files in a list or section, with history.",
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
            "Which agents are online and what each is working on.",
            {"params": {}},
            AGENT_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_health",
            "Slate health",
            "Whether Slate is working. Owners and admins.",
            {"params": {}},
            HEALTH_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_batch",
            "Slate batch",
            "Up to 100 other calls in one round trip. atomic false commits each one alone.",
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
            "Every change, oldest first after a cursor.",
            {"params": {"since": "0", "type": "item.assigned,item.commented"}},
            EVENT_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_share",
            "Slate sharing",
            "Who can see a section or list. Owner and admin.",
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
            "Who changed what, newest first.",
            {"action": "list", "params": {"actor_kind": "agent", "limit": 50}},
            AUDIT_ACTIONS,
            read_only=True,
        ),
        ToolSpec(
            "slate_export",
            "Slate export",
            "A list, a section or everything as one JSON document.",
            {"action": "json", "params": {"list_id": "Ab12Cd34Ef56"}},
            EXPORT_ACTIONS,
            destructive=True,
        ),
        ToolSpec(
            "slate_search",
            "Slate search",
            "Find text in tasks, pages and chat.",
            {"params": {"q": "passport", "limit": 30}},
            SEARCH_ACTIONS,
            read_only=True,
        ),
    )
}


# --- tool schemas and descriptions ----------------------------------------------------------

# Listed when the server is started with no tool flag and no SLATE_MCP_TOOLS.
CORE = ("slate_work", "slate_item", "slate_comment", "slate_search", "slate_list")

_SESSION_REF = {
    "type": "string",
    "maxLength": 200,
    "description": "Optional: your run or session id, recorded on every event written.",
}


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


def _params_schema(
    action: Action, *, strip_defaults: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = _strip_titles(action.params.model_json_schema())
    props = schema.get("properties") or {}
    if strip_defaults:
        # A default on a shared params object is applied by some clients to every call.
        # On a branch it would still reset an update when the client fills it in.
        for prop in props.values():
            if isinstance(prop, dict):
                prop.pop("default", None)
    params: dict[str, Any] = {
        **{key: schema[key] for key in ("if", "then", "else") if key in schema},
        "type": "object",
        "additionalProperties": False,
        "properties": props,
    }
    if schema.get("required"):
        params["required"] = list(schema["required"])
    return params, schema.get("$defs") or {}


def _branch(
    name: str,
    action: Action,
    *,
    with_action: bool,
    session: bool,
    strip_defaults: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    params, defs = _params_schema(action, strip_defaults=strip_defaults)
    properties: dict[str, Any] = {}
    required: list[str] = []
    if with_action:
        properties["action"] = {"type": "string", "const": name}
        required.append("action")
    properties["params"] = params
    if params.get("required"):
        required.append("params")
    if session:
        properties["session_ref"] = dict(_SESSION_REF)
    branch: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        branch["required"] = required
    return branch, defs


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    """One object per action.

    MCP requires ``type: object`` at the root of a tool schema, so a root that is only
    ``oneOf`` is not safe. The root stays an object and ``oneOf`` sits beside it, each
    branch carrying only that action's fields (discriminated by ``action``). ``$defs``
    stay on the root: a ``#/$defs`` reference resolves against the document, not the branch.
    """
    session = any(action.scope != "read" for action in spec.actions.values())
    if single_action(spec):
        name, action = next(iter(spec.actions.items()))
        branch, defs = _branch(
            name, action, with_action=False, session=session, strip_defaults=False
        )
        if defs:
            branch["$defs"] = defs
        return branch

    defs: dict[str, Any] = {}
    branches: list[dict[str, Any]] = []
    for name, action in spec.actions.items():
        branch, action_defs = _branch(
            name, action, with_action=True, session=session, strip_defaults=True
        )
        for key, value in action_defs.items():
            if key in defs and defs[key] != value:
                raise RuntimeError(f"{spec.name}: $defs '{key}' differs across actions")
            defs[key] = value
        branches.append(branch)
    out: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": list(spec.actions)},
            "params": {"type": "object"},
        },
        "required": ["action"],
        "oneOf": branches,
    }
    if session:
        out["properties"]["session_ref"] = dict(_SESSION_REF)
    if defs:
        out["$defs"] = defs
    return out


def description(spec: ToolSpec) -> str:
    lines = [spec.about]
    if spec.name == "slate_work":
        lines.append("Start here.")
        lines.append(RULE)
    lines.append(f"Example: {json.dumps(spec.example)}")
    return "\n".join(lines)


def tool_list(names: list[str] | tuple[str, ...] | None = None) -> list[mcp_types.Tool]:
    """The tools `names` names, in that order. None lists every tool."""
    if names is None:
        specs = list(TOOLS.values())
    else:
        missing = [name for name in names if name not in TOOLS]
        if missing:
            raise ToolError(f"unknown tool '{missing[0]}'; tools: {', '.join(TOOLS)}")
        specs = [TOOLS[name] for name in names]
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
        for spec in specs
    ]
