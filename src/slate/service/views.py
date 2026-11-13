"""Saved views (docs/views.md): a named lens over items, stored as validated config JSON.

The board evaluates filters, grouping and sorting client-side; the server only checks the shape,
stores it, and emits `view.*` events so every connected board updates live.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import ScopeKind, View, utcnow
from slate.service.core import Actor, apply_change
from slate.service.errors import Forbidden, Invalid, NotFound

MAX_FILTER_DEPTH = 3

FILTER_FIELDS = {
    "status": {"is", "is_not"},
    "status_kind": {"is", "is_not"},
    "assignee": {"is", "is_not", "empty", "not_empty"},
    "tag": {"any", "all", "none", "empty"},
    "priority": {"is", "is_not", "empty"},
    "due": {
        "overdue",
        "today",
        "today_or_earlier",
        "tomorrow",
        "this_week",
        "next_n_days",
        "before",
        "after",
        "between",
        "empty",
        "not_empty",
    },
    "start": {"before", "after", "between", "empty", "not_empty"},
    "list": {"is", "is_not"},
    "section": {"is", "is_not"},
    "text": {"contains"},
    "recurring": {"is"},
    "has_subtasks": {"is"},
    "waiting_on": {"is", "empty", "not_empty"},
    "created": {"within_days"},
    "updated": {"within_days"},
    "blocked": {"is"},
    "estimate": {"empty", "not_empty", "gt", "lt"},
}
LAYOUTS = ("board", "list", "table", "calendar", "timeline", "box")


GROUP_BY = {
    "none",
    "status",
    "status_kind",
    "due",
    "priority",
    "assignee",
    "tag",
    "list",
    "section",
}
SORT_FIELDS = {
    "manual",
    "due",
    "priority",
    "title",
    "created",
    "updated",
    "start",
    "estimate",
}
# In the order a board card shows them (web/src/lib/views.ts ALL_CARD_FIELDS).
CARD_FIELDS = {
    "description",
    "subtasks",
    "comments",
    "checklist",
    "attachments",
    "due",
    "start",
    "priority",
    "tags",
    "status",
    "assignees",
    "list",
    "waiting",
    "recurrence",
    "estimate",
    "blocked",
}

DEFAULT_CARD_FIELDS = [
    "description",
    "subtasks",
    "comments",
    "checklist",
    "attachments",
    "due",
    "priority",
    "tags",
    "status",
    "assignees",
    "list",
    "waiting",
    "recurrence",
    "blocked",  # shows only while the task waits on an open one
]

# Seeded for the first admin, at bootstrap and by the migration on an existing database.
SEED_VIEWS: tuple[dict[str, Any], ...] = (
    {
        "id": "vwthisweek01",
        "name": "This week",
        "position": 0,
        "config": {
            "layout": "board",
            "group_by": "due",
            "due_window_days": 6,
            "sort": [{"field": "due", "dir": "asc"}, {"field": "priority", "dir": "desc"}],
            "filter": {"op": "and", "rules": []},
            "show_closed": False,
            "show_subtasks": False,
            "hide_empty_groups": False,
            "density": "comfortable",
            "card_fields": DEFAULT_CARD_FIELDS,
        },
    },
    {
        "id": "vwagenda0001",
        "name": "Agenda",
        "position": 1,
        "config": {
            "layout": "list",
            "group_by": "due",
            "due_window_days": 0,
            "sort": [{"field": "priority", "dir": "desc"}, {"field": "due", "dir": "asc"}],
            "filter": {
                "op": "or",
                "rules": [
                    {"field": "due", "cmp": "today_or_earlier", "value": None},
                    {"field": "waiting_on", "cmp": "is", "value": "me"},
                ],
            },
            "show_closed": False,
            "show_subtasks": False,
            "hide_empty_groups": True,
            "density": "comfortable",
            "card_fields": DEFAULT_CARD_FIELDS,
        },
    },
)


def validate_config(config: Any, *, scope_kind: str) -> dict[str, Any]:
    """Check a view config and return it normalised. Raises Invalid in plain words."""
    if not isinstance(config, dict):
        raise Invalid("config must be an object")
    unknown = set(config) - {
        "layout",
        "group_by",
        "due_window_days",
        "sort",
        "filter",
        "show_closed",
        "show_subtasks",
        "hide_empty_groups",
        "density",
        "card_fields",
        "calendar_span",
        "timeline_zoom",
        "title_lines",
        "collapse_empty",
    }
    if unknown:
        raise Invalid(f"unknown config key(s): {', '.join(sorted(unknown))}")

    layout = config.get("layout", "board")
    if layout not in LAYOUTS:
        raise Invalid(f"layout must be one of {list(LAYOUTS)}")
    calendar_span = config.get("calendar_span", "month")
    if calendar_span not in ("month", "week"):
        raise Invalid("calendar_span must be 'month' or 'week'")
    timeline_zoom = config.get("timeline_zoom", "week")
    if timeline_zoom not in ("day", "week", "month"):
        raise Invalid("timeline_zoom must be 'day', 'week' or 'month'")
    group_by = config.get("group_by", "none")
    if group_by not in GROUP_BY:
        raise Invalid(f"group_by must be one of {sorted(GROUP_BY)}")
    if group_by == "status" and scope_kind != "list":
        raise Invalid("group_by 'status' is only valid in a list scope; use 'status_kind'")
    window = config.get("due_window_days", 6)
    if not isinstance(window, int) or not 0 <= window <= 30:
        raise Invalid("due_window_days must be an integer from 0 to 30")

    sort = config.get("sort", [])
    if not isinstance(sort, list):
        raise Invalid("sort must be a list")
    for entry in sort:
        if not isinstance(entry, dict) or set(entry) - {"field", "dir"}:
            raise Invalid("each sort entry is {field, dir}")
        if entry.get("field") not in SORT_FIELDS:
            raise Invalid(f"sort field must be one of {sorted(SORT_FIELDS)}")
        if entry.get("dir") not in ("asc", "desc"):
            raise Invalid("sort dir must be 'asc' or 'desc'")

    _check_filter(config.get("filter", {"op": "and", "rules": []}), depth=1)

    for key in ("show_closed", "show_subtasks", "hide_empty_groups", "collapse_empty"):
        if not isinstance(config.get(key, False), bool):
            raise Invalid(f"{key} must be true or false")
    density = config.get("density", "comfortable")
    if density not in ("comfortable", "compact"):
        raise Invalid("density must be 'comfortable' or 'compact'")
    card_fields = config.get("card_fields", DEFAULT_CARD_FIELDS)
    if not isinstance(card_fields, list) or any(f not in CARD_FIELDS for f in card_fields):
        raise Invalid(f"card_fields must be drawn from {sorted(CARD_FIELDS)}")
    title_lines = config.get("title_lines", 2)
    if type(title_lines) is not int or not 0 <= title_lines <= 3:
        raise Invalid("title_lines must be 0 (whole title) or 1 to 3")

    return {
        "layout": layout,
        "group_by": group_by,
        "due_window_days": window,
        "sort": [{"field": e["field"], "dir": e["dir"]} for e in sort],
        "filter": config.get("filter", {"op": "and", "rules": []}),
        "show_closed": config.get("show_closed", False),
        "show_subtasks": config.get("show_subtasks", False),
        "hide_empty_groups": config.get("hide_empty_groups", False),
        "density": density,
        "card_fields": list(card_fields),
        "calendar_span": calendar_span,
        "timeline_zoom": timeline_zoom,
        "title_lines": title_lines,
        "collapse_empty": config.get("collapse_empty", True),
    }


def _check_filter(node: Any, *, depth: int) -> None:
    if depth > MAX_FILTER_DEPTH:
        raise Invalid(f"filter nesting deeper than {MAX_FILTER_DEPTH} levels")
    if not isinstance(node, dict) or set(node) != {"op", "rules"}:
        raise Invalid("a filter group is {op, rules}")
    if node["op"] not in ("and", "or"):
        raise Invalid("filter op must be 'and' or 'or'")
    if not isinstance(node["rules"], list):
        raise Invalid("filter rules must be a list")
    for rule in node["rules"]:
        if isinstance(rule, dict) and "op" in rule:
            _check_filter(rule, depth=depth + 1)
            continue
        if not isinstance(rule, dict) or set(rule) - {"field", "cmp", "value"}:
            raise Invalid("a filter rule is {field, cmp, value}")
        allowed = FILTER_FIELDS.get(rule.get("field"))
        if allowed is None:
            raise Invalid(f"unknown filter field {rule.get('field')!r}")
        if rule.get("cmp") not in allowed:
            raise Invalid(f"filter {rule['field']} does not support cmp {rule.get('cmp')!r}")


def view_out(view: View) -> dict[str, Any]:
    return {
        "id": view.id,
        "name": view.name,
        "scope_kind": view.scope_kind.value,
        "scope_id": view.scope_id,
        "config": view.config_json,
        "position": view.position,
        "owner_id": view.owner_id,
        "shared": view.shared,
        "created_at": view.created_at,
        "updated_at": view.updated_at,
        "archived_at": view.archived_at,
    }


def get_view(session: Session, view_id: str) -> View:
    view = session.get(View, view_id)
    if view is None:
        raise NotFound(f"view {view_id} not found")
    return view


def list_views(
    session: Session,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    include_archived: bool = False,
) -> list[View]:
    stmt = select(View)
    if scope_kind is not None:
        stmt = stmt.where(View.scope_kind == ScopeKind(scope_kind))
        stmt = stmt.where(View.scope_id == scope_id)
    if not include_archived:
        stmt = stmt.where(View.archived_at.is_(None))
    return list(session.scalars(stmt.order_by(View.position, View.created_at)))


def _scope(scope_kind: str, scope_id: str | None) -> tuple[ScopeKind, str | None]:
    try:
        kind = ScopeKind(scope_kind)
    except ValueError:
        raise Invalid("scope_kind must be 'everything', 'section' or 'list'") from None
    if kind is ScopeKind.everything:
        return kind, None
    if not scope_id:
        raise Invalid(f"a {kind.value} scope needs a scope_id")
    return kind, scope_id


def create_view(
    session: Session,
    actor: Actor,
    *,
    name: str,
    scope_kind: str,
    scope_id: str | None = None,
    config: dict[str, Any],
    position: int | None = None,
    shared: bool = True,
) -> View:
    name = (name or "").strip()
    if not name or len(name) > 100:
        raise Invalid("a view name is required (100 characters or fewer)")
    kind, scope_id = _scope(scope_kind, scope_id)
    clean = validate_config(config, scope_kind=kind.value)
    if position is None:
        position = len(
            list_views(session, scope_kind=kind.value, scope_id=scope_id, include_archived=True)
        )
    with apply_change(session, actor) as cx:
        view = cx.create(
            View(
                name=name,
                scope_kind=kind,
                scope_id=scope_id,
                config_json=clean,
                position=position,
                owner_id=actor.account_id,
                shared=shared,
            ),
            "view.created",
        )
    return view


def _require_may_change(session: Session, actor: Actor, view: View) -> None:
    """A saved view is changed or archived by its owner or an admin; Slate's own landing
    views, which everyone shares, by an admin only."""
    from slate.service import access

    seed = view.id in {spec["id"] for spec in SEED_VIEWS}
    if not seed and view.owner_id == actor.account_id:
        return
    if not access.is_admin(session, actor.account_id):
        if seed:
            raise Forbidden("only an owner or admin can change Slate's landing views")
        raise Forbidden("only the view's owner or an admin can change it")


def update_view(session: Session, actor: Actor, view_id: str, changes: dict[str, Any]) -> View:
    unknown = set(changes) - {"name", "config", "position", "shared"}
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    view = get_view(session, view_id)
    _require_may_change(session, actor, view)
    config = None
    if "config" in changes:
        config = validate_config(changes["config"], scope_kind=view.scope_kind.value)
    name = changes.get("name")
    if name is not None and (not name.strip() or len(name.strip()) > 100):
        raise Invalid("a view name is required (100 characters or fewer)")
    with apply_change(session, actor) as cx:
        changed = False
        if name is not None:
            changed |= cx.set(view, "name", name.strip(), "view.updated")
        if config is not None:
            changed |= cx.set(view, "config_json", config, "view.updated")
        if "position" in changes:
            changed |= cx.set(view, "position", int(changes["position"]), "view.updated")
        if "shared" in changes:
            changed |= cx.set(view, "shared", bool(changes["shared"]), "view.updated")
        if changed:
            cx.set(view, "updated_at", utcnow(), "view.updated")
    return view


def set_archived(session: Session, actor: Actor, view_id: str, *, archived: bool) -> View:
    view = get_view(session, view_id)
    _require_may_change(session, actor, view)
    with apply_change(session, actor) as cx:
        if archived and view.archived_at is None:
            cx.set(view, "archived_at", utcnow(), "view.archived")
        elif not archived and view.archived_at is not None:
            cx.set(view, "archived_at", None, "view.unarchived")
    return view


def seed_default_views(session: Session, actor: Actor) -> list[View]:
    """Create the landing views if they are not already there. Safe to call twice."""
    made = []
    for spec in SEED_VIEWS:
        if session.get(View, spec["id"]) is not None:
            continue
        with apply_change(session, actor) as cx:
            made.append(
                cx.create(
                    View(
                        id=spec["id"],
                        name=spec["name"],
                        scope_kind=ScopeKind.everything,
                        scope_id=None,
                        config_json=spec["config"],
                        position=spec["position"],
                        owner_id=actor.account_id,
                        shared=True,
                    ),
                    "view.created",
                )
            )
    return made


ScopeLiteral = Literal["everything", "section", "list"]
