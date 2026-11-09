"""Automations: ClickUp-style "when this happens, if these hold, do that" rules.

A rule (`automations` row) has a scope (one list, one section, or everywhere), one trigger,
any number of conditions (all must hold) and one or more actions. The engine reads the event
log after a cursor (`engine_cursors` row `automations`), so it sees exactly what every board,
webhook and agent sees, in commit order, and resumes after a restart.

Vocabulary (also served by `catalog()` so the board and agents build rules from one list):

    triggers    task_created, status_changed {to?, to_kind?, from?}, assignee_added {account?},
                assignee_removed {account?}, tag_added {tag?}, tag_removed {tag?},
                priority_changed {to?}, due_arrives, due_passes, comment_added {mentions?},
                moved_in, task_unblocked, task_blocked,
                agent_reported {state?} (an agent's badge changes state: working, blocked,
                review, done or idle; a new note in the same state is not a change)
    conditions  {field: priority|tag|assignee|status|status_kind|list, op: is|is_not, value}
    actions     set_status {status}, set_priority {priority}, assign {account},
                unassign {account}, add_tag {tag}, remove_tag {tag}, move_list {list_id},
                set_due {in_days}, comment {body}, archive, webhook {url}

Rules of the engine:

- Actions run as the `slate` system agent (source `system`) with `session_ref`
  `automation:<rule id>`, so history says "Slate (automation) moved this to Done". Every
  change is an ordinary event (AGENTS.md rule 3).
- Events written by an automation never trigger automations (ClickUp's rule too): no loops,
  no chains.
- Each action is its own change: one failing (a status the item's list does not have) is
  logged in the run and the others still run.
- A run is logged in `automation_runs` (bookkeeping, not an event), with one result per
  action. Runs older than `RUN_KEEP` are pruned as the engine goes.
- The `webhook` action is the one that leaves Slate: a JSON POST (`{automation, event, item}`)
  signed like webhook deliveries (`X-Slate-Signature: sha256=<HMAC of the body>`) with the
  rule's own secret (made when the rule first gets a webhook action, shown once, rotatable),
  sent outside any write transaction, one attempt, logged in the run.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from slate import crypto
from slate.dates import LOCAL
from slate.ids import new_id
from slate.models import (
    Account,
    Automation,
    AutomationRun,
    EngineCursor,
    Event,
    Item,
    List,
    Priority,
    Section,
    Status,
    StatusKind,
    utcnow,
)
from slate.service import access
from slate.service import comments as comments_svc
from slate.service import items as items_svc
from slate.service import lists as lists_svc
from slate.service import tags as tags_svc
from slate.service import webhooks as webhooks_svc
from slate.service.core import Actor, apply_change, event_payload, jsonable
from slate.service.errors import Invalid, NotFound, ServiceError, Unavailable

log = logging.getLogger(__name__)

CURSOR = "automations"
SESSION_PREFIX = "automation:"
PAGE = 500
MAX_RULES = 200
MAX_ACTIONS = 10
MAX_CONDITIONS = 10
RUN_KEEP = timedelta(days=30)
RUN_LIST_MAX = 500

# Sends one webhook action: (url, json payload, signing secret) -> (ok, detail). The runner
# passes a real HTTP sender; tests pass a fake. None means webhook actions are logged as not sent.
Sender = Callable[[str, dict[str, Any], str], tuple[bool, str]]
SECRET_PREFIX = "whsec_"

PRIORITIES = [p.value for p in Priority]
KINDS = [k.value for k in StatusKind]

# --- The vocabulary -------------------------------------------------------------------------
# name -> (event type, label, params {name: (kind, required, help)})
TRIGGERS: dict[str, tuple[str, str, dict[str, tuple[str, bool, str]]]] = {
    "task_created": ("item.created", "Task is created", {}),
    "status_changed": (
        "item.status_changed",
        "Status changes",
        {
            "to": ("status", False, "new status name (any if empty)"),
            "to_kind": ("status_kind", False, "or: new status kind (open, active, done...)"),
            "from": ("status", False, "old status name (any if empty)"),
        },
    ),
    "assignee_added": (
        "item.assigned",
        "Assignee is added",
        {"account": ("account", False, "this account (anyone if empty)")},
    ),
    "assignee_removed": (
        "item.assigned",
        "Assignee is removed",
        {"account": ("account", False, "this account (anyone if empty)")},
    ),
    "tag_added": ("item.updated", "Tag is added", {"tag": ("tag", False, "this tag (any)")}),
    "tag_removed": ("item.updated", "Tag is removed", {"tag": ("tag", False, "this tag (any)")}),
    "priority_changed": (
        "item.updated",
        "Priority changes",
        {"to": ("priority", False, "new priority, or 'none' (any if empty)")},
    ),
    "due_arrives": ("item.due", "Due date arrives", {}),
    "due_passes": ("item.overdue", "Due date passes (overdue)", {}),
    "comment_added": (
        "item.commented",
        "Comment is added",
        {"mentions": ("account", False, "only when it @mentions this account")},
    ),
    "moved_in": ("item.moved", "Task moves into this list", {}),
    "task_unblocked": (
        "item.unblocked",
        "Task is unblocked (everything it waits on is done)",
        {},
    ),
    "task_blocked": ("item.blocked", "Task becomes blocked (waits on an open task)", {}),
    "agent_reported": (
        "item.reported",
        "Agent reports a new state (working, blocked, review, done)",
        {"state": ("agent_state", False, "this state (any change if empty)")},
    ),
}
AGENT_STATES = ("working", "blocked", "review", "done", "idle")
# Triggers that also fire on a second event type (none today; kept so a trigger can listen to
# two event types without touching `trigger_fires`).
ALSO_ON: dict[str, tuple[str, ...]] = {}

CONDITION_FIELDS = {
    "priority": "Priority",
    "tag": "Tag",
    "assignee": "Assignee",
    "status": "Status",
    "status_kind": "Status kind",
    "list": "List",
}

ACTIONS: dict[str, tuple[str, dict[str, tuple[str, bool, str]]]] = {
    "set_status": ("Change status", {"status": ("status", True, "status name in the task's list")}),
    "set_priority": (
        "Set priority",
        {"priority": ("priority", True, "urgent, high, normal, low or none")},
    ),
    "assign": ("Add assignee", {"account": ("account", True, "person or agent")}),
    "unassign": ("Remove assignee", {"account": ("account", True, "person or agent")}),
    "add_tag": ("Add tag", {"tag": ("tag", True, "tag name; created if new")}),
    "remove_tag": ("Remove tag", {"tag": ("tag", True, "tag name")}),
    "move_list": ("Move to list", {"list_id": ("list", True, "target list")}),
    "set_due": (
        "Set due date",
        {"in_days": ("number", True, "days from now (0 = today, 5 pm workspace time); -1 clears")},
    ),
    "comment": (
        "Post a comment",
        {"body": ("text", True, "markdown; {title}, {actor}, {status} are filled in")},
    ),
    "archive": ("Archive the task", {}),
    "webhook": ("Call a webhook", {"url": ("url", True, "https URL; gets the event as JSON")}),
}


def catalog() -> dict[str, Any]:
    """Every trigger, condition field and action with its parameters, for builders."""

    def params(spec: dict[str, tuple[str, bool, str]]) -> list[dict[str, Any]]:
        return [{"name": n, "kind": k, "required": r, "help": h} for n, (k, r, h) in spec.items()]

    return {
        "triggers": [
            {"type": t, "label": label, "event": ev, "params": params(p)}
            for t, (ev, label, p) in TRIGGERS.items()
        ],
        "conditions": {
            "fields": [{"field": f, "label": label} for f, label in CONDITION_FIELDS.items()],
            "ops": ["is", "is_not"],
        },
        "actions": [
            {"type": t, "label": label, "params": params(p)} for t, (label, p) in ACTIONS.items()
        ],
    }


# --- Validation ----------------------------------------------------------------------------


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{what} must be a non-empty string")
    return value.strip()


def _account_handle(session: Session, ref: Any, what: str) -> str:
    return items_svc.resolve_account(session, _text(ref, what)).handle


def _priority_value(value: Any, what: str) -> str:
    text = _text(value, what).lower()
    if text not in [*PRIORITIES, "none"]:
        raise Invalid(f"{what} must be one of {', '.join([*PRIORITIES, 'none'])}")
    return text


def _clean_params(
    session: Session, spec: dict[str, tuple[str, bool, str]], raw: Mapping[str, Any], what: str
) -> dict[str, Any]:
    unknown = set(raw) - set(spec) - {"type"}
    if unknown:
        raise Invalid(f"{what} does not take {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for name, (kind, required, _help) in spec.items():
        value = raw.get(name)
        if value is None or value == "":
            if required:
                raise Invalid(f"{what} needs {name}")
            continue
        label = f"{what} {name}"
        if kind == "account":
            out[name] = _account_handle(session, value, label)
        elif kind == "tag":
            out[name] = tags_svc.normalize(_text(value, label))
        elif kind == "priority":
            out[name] = _priority_value(value, label)
        elif kind == "status_kind":
            text = _text(value, label).lower()
            if text not in KINDS:
                raise Invalid(f"{label} must be one of {', '.join(KINDS)}")
            out[name] = text
        elif kind == "list":
            out[name] = lists_svc.get_list(session, _text(value, label)).id
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise Invalid(f"{label} must be a number")
            if not -1 <= value <= 3650:
                raise Invalid(f"{label} must be between -1 and 3650")
            out[name] = int(value)
        elif kind == "url":
            out[name] = webhooks_svc.validate_url(_text(value, label))
        elif kind == "agent_state":
            text = _text(value, label).lower()
            if text not in AGENT_STATES:
                raise Invalid(f"{label} must be one of {', '.join(AGENT_STATES)}")
            out[name] = text
        else:  # status, text
            text = _text(value, label)
            if len(text) > 5000:
                raise Invalid(f"{label} is too long (5000 characters at most)")
            out[name] = text
    return out


def clean_trigger(session: Session, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or not raw.get("type"):
        raise Invalid("trigger must be an object with a type, e.g. {type: status_changed}")
    kind = raw["type"]
    if kind not in TRIGGERS:
        raise Invalid(f"unknown trigger '{kind}'; one of {', '.join(TRIGGERS)}")
    params = _clean_params(session, TRIGGERS[kind][2], raw, f"trigger {kind}")
    if kind == "status_changed" and params.get("to") and params.get("to_kind"):
        raise Invalid("status_changed takes `to` or `to_kind`, not both")
    return {"type": kind, **params}


def clean_conditions(session: Session, raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Invalid("conditions must be a list of {field, op, value}")
    if len(raw) > MAX_CONDITIONS:
        raise Invalid(f"at most {MAX_CONDITIONS} conditions")
    out = []
    for cond in raw:
        if not isinstance(cond, Mapping):
            raise Invalid("each condition is {field, op, value}")
        field = cond.get("field")
        if field not in CONDITION_FIELDS:
            raise Invalid(f"condition field must be one of {', '.join(CONDITION_FIELDS)}")
        op = cond.get("op", "is")
        if op not in ("is", "is_not"):
            raise Invalid("condition op must be is or is_not")
        unknown = set(cond) - {"field", "op", "value"}
        if unknown:
            raise Invalid(f"a condition does not take {', '.join(sorted(unknown))}")
        label = f"condition {field}"
        value = cond.get("value")
        if field == "priority":
            value = _priority_value(value, label)
        elif field == "assignee":
            text = _text(value, label).lower()
            value = "nobody" if text == "nobody" else _account_handle(session, text, label)
        elif field == "tag":
            value = tags_svc.normalize(_text(value, label))
        elif field == "status_kind":
            value = _text(value, label).lower()
            if value not in KINDS:
                raise Invalid(f"{label} must be one of {', '.join(KINDS)}")
        elif field == "list":
            value = lists_svc.get_list(session, _text(value, label)).id
        else:
            value = _text(value, label)
        out.append({"field": field, "op": op, "value": value})
    return out


def clean_actions(session: Session, raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise Invalid("actions must be a non-empty list, e.g. [{type: set_status, status: Done}]")
    if len(raw) > MAX_ACTIONS:
        raise Invalid(f"at most {MAX_ACTIONS} actions")
    out = []
    for act in raw:
        if not isinstance(act, Mapping) or not act.get("type"):
            raise Invalid("each action is an object with a type")
        kind = act["type"]
        if kind not in ACTIONS:
            raise Invalid(f"unknown action '{kind}'; one of {', '.join(ACTIONS)}")
        out.append({"type": kind, **_clean_params(session, ACTIONS[kind][1], act, kind)})
    return out


def _scope(
    session: Session, list_id: str | None, section_id: str | None
) -> tuple[str | None, str | None]:
    if list_id and section_id:
        raise Invalid("scope a rule to a list or a section, not both")
    if list_id:
        lists_svc.get_list(session, list_id)
    if section_id and session.get(Section, section_id) is None:
        raise NotFound(f"section {section_id} not found")
    # The rule runs as the system agent over everything in its scope, so the caller must be
    # allowed to edit all of it (a restricted caller cannot make a workspace-wide rule).
    access.check_scope(session, list_id or None, section_id or None, "automations")
    return list_id or None, section_id or None


# --- Reads ---------------------------------------------------------------------------------


def get_automation(session: Session, automation_id: str) -> Automation:
    rule = session.get(Automation, automation_id)
    if rule is None:
        raise NotFound(f"automation {automation_id} not found")
    return rule


def list_automations(
    session: Session, *, list_id: str | None = None, section_id: str | None = None
) -> list[Automation]:
    q = select(Automation).order_by(Automation.created_at, Automation.id)
    if list_id:
        q = q.where(Automation.list_id == list_id)
    if section_id:
        q = q.where(Automation.section_id == section_id)
    return list(session.scalars(q))


def run_stats(session: Session, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Per rule: runs in the kept window and the last run's time and status."""
    if not ids:
        return {}
    counts = dict(
        session.execute(
            select(AutomationRun.automation_id, func.count())
            .where(AutomationRun.automation_id.in_(ids))
            .group_by(AutomationRun.automation_id)
        ).all()
    )
    last: dict[str, AutomationRun] = {}
    for run in session.scalars(
        select(AutomationRun)
        .where(AutomationRun.automation_id.in_(ids))
        .order_by(AutomationRun.at.desc(), AutomationRun.id.desc())
    ):
        last.setdefault(run.automation_id, run)
        if len(last) == len(ids):
            break
    return {
        i: {
            "run_count": counts.get(i, 0),
            "last_run_at": last[i].at if i in last else None,
            "last_status": last[i].status if i in last else None,
        }
        for i in ids
    }


_KIND_WORDS = {
    "open": "an open status",
    "active": "an active status",
    "done": "a done status",
    "cancelled": "a cancelled status",
}


def _when(trig: Mapping[str, Any]) -> str:
    kind = trig.get("type")
    label = TRIGGERS.get(kind, ("", str(kind), {}))[1].lower()
    if kind == "agent_reported":
        return f"When an agent reports {trig.get('state') or 'a new state'}"
    out = f"When {label}"
    if kind == "status_changed":
        if trig.get("from"):
            out += f" from {trig['from']}"
        if trig.get("to"):
            out += f" to {trig['to']}"
        elif trig.get("to_kind"):
            out += f" to {_KIND_WORDS.get(trig['to_kind'], trig['to_kind'])}"
    elif kind in ("assignee_added", "assignee_removed") and trig.get("account"):
        out += f" (@{trig['account']})"
    elif kind in ("tag_added", "tag_removed") and trig.get("tag"):
        out += f" ({trig['tag']})"
    elif kind == "priority_changed" and trig.get("to"):
        out += f" to {trig['to']}"
    elif kind == "comment_added" and trig.get("mentions"):
        out += f" mentioning @{trig['mentions']}"
    return out


def _then(session: Session, a: Mapping[str, Any]) -> str:
    kind = a["type"]
    label = ACTIONS.get(kind, (kind, {}))[0].lower()
    if kind in ("set_status", "set_priority"):
        return f"{label} to {a.get('status') or a.get('priority')}"
    if kind in ("assign", "unassign"):
        return f"{label} @{a['account']}"
    if kind in ("add_tag", "remove_tag"):
        return f"{label} {a['tag']}"
    if kind == "move_list":
        lst = session.get(List, a["list_id"])
        return f"{label} {lst.name if lst else 'a removed list'}"
    if kind == "set_due":
        return "clear the due date" if a["in_days"] < 0 else f"set due in {a['in_days']} days"
    return label


def describe(session: Session, rule: Automation) -> str:
    """One plain-English line: "When status changes to Done, then add tag shipped"."""
    ifs = []
    for c in rule.conditions_json or []:
        value = c["value"]
        if c["field"] == "list":
            lst = session.get(List, value)
            value = lst.name if lst else "a removed list"
        is_ = "is not" if c["op"] == "is_not" else "is"
        ifs.append(f"{c['field'].replace('_', ' ')} {is_} {value}")
    out = _when(rule.trigger_json or {})
    if ifs:
        out += ", if " + " and ".join(ifs)
    return out + ", then " + ", ".join(_then(session, a) for a in rule.actions_json or []) + "."


def automation_view(
    session: Session, rule: Automation, stats: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    stats = stats or run_stats(session, [rule.id])[rule.id]
    return {
        "id": rule.id,
        "name": rule.name,
        "list_id": rule.list_id,
        "section_id": rule.section_id,
        "trigger": rule.trigger_json,
        "conditions": rule.conditions_json or [],
        "actions": rule.actions_json,
        "enabled": rule.enabled,
        "signed": bool(rule.secret_enc),
        "webhook_secret": getattr(rule, "new_secret", None),
        "description": describe(session, rule),
        "created_by": rule.created_by,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        **stats,
    }


def automation_views(session: Session, rules: Sequence[Automation]) -> list[dict[str, Any]]:
    stats = run_stats(session, [r.id for r in rules])
    return [automation_view(session, r, stats[r.id]) for r in rules]


def list_runs(
    session: Session, *, automation_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    q = select(AutomationRun).order_by(AutomationRun.at.desc(), AutomationRun.id.desc())
    if automation_id:
        get_automation(session, automation_id)
        q = q.where(AutomationRun.automation_id == automation_id)
    runs = list(session.scalars(q.limit(max(1, min(limit, RUN_LIST_MAX)))))
    return run_views(session, runs)


def run_views(session: Session, runs: Sequence[AutomationRun]) -> list[dict[str, Any]]:
    titles = dict(
        session.execute(
            select(Item.id, Item.title).where(Item.id.in_({r.item_id for r in runs if r.item_id}))
        ).all()
    )
    names = dict(
        session.execute(
            select(Automation.id, Automation.name).where(
                Automation.id.in_({r.automation_id for r in runs})
            )
        ).all()
    )
    return [
        {
            "id": r.id,
            "automation_id": r.automation_id,
            "automation_name": names.get(r.automation_id),
            "event_id": r.event_id,
            "item_id": r.item_id,
            "item_title": titles.get(r.item_id),
            "triggered_by": r.triggered_by,
            "status": r.status,
            "results": r.results_json or [],
            "at": r.at,
        }
        for r in runs
    ]


# --- Rule writes ---------------------------------------------------------------------------

_UPDATABLE = {"name", "list_id", "section_id", "trigger", "conditions", "actions", "enabled"}


def _name(value: Any) -> str:
    text = _text(value, "name")
    if len(text) > 120:
        raise Invalid("name is limited to 120 characters")
    return text


def create_automation(
    session: Session,
    actor: Actor,
    *,
    name: str,
    trigger: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]] | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    enabled: bool = True,
) -> Automation:
    if session.scalar(select(func.count()).select_from(Automation)) >= MAX_RULES:
        raise Invalid(f"Slate keeps at most {MAX_RULES} automations")
    list_id, section_id = _scope(session, list_id, section_id)
    rule = Automation(
        id=new_id(),
        name=_name(name),
        list_id=list_id,
        section_id=section_id,
        trigger_json=clean_trigger(session, trigger),
        conditions_json=clean_conditions(session, list(conditions or [])),
        actions_json=clean_actions(session, list(actions)),
        enabled=bool(enabled),
        created_by=actor.account_id,
    )
    secret = _new_secret(rule) if _has_webhook(rule.actions_json) else None
    with apply_change(session, actor) as cx:
        cx.create(rule, "automation.created")
    rule.new_secret = secret  # shown once, in this response only
    return rule


def _has_webhook(actions: Any) -> bool:
    return any(a.get("type") == "webhook" for a in actions or [])


def _new_secret(rule: Automation) -> str:
    """Make and store (encrypted) a fresh signing secret; returns the plaintext."""
    secret = SECRET_PREFIX + secrets.token_urlsafe(32)
    try:
        rule.secret_enc = crypto.encrypt(secret, aad=rule.id)
    except crypto.SecretKeyError as exc:
        raise Unavailable(f"webhook actions need signing: {exc}") from None
    return secret


def rotate_secret(session: Session, actor: Actor, automation_id: str) -> str:
    """Replace the rule's webhook signing secret. Returns the new one, shown only here."""
    rule = get_automation(session, automation_id)
    with apply_change(session, actor) as cx:
        secret = _new_secret(rule)
        # No secret material in the event, just that it changed.
        cx.record("automation.secret_rotated", subject_id=rule.id, covers=[rule])
    return secret


def signing_secret(rule: Automation) -> str:
    if not rule.secret_enc:
        raise Invalid("this rule has no signing secret; rotate one (POST /automations/{id}/secret)")
    try:
        return crypto.decrypt(rule.secret_enc, aad=rule.id)
    except crypto.SecretKeyError as exc:
        raise Invalid(str(exc)) from None


def update_automation(
    session: Session, actor: Actor, automation_id: str, changes: Mapping[str, Any]
) -> Automation:
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    rule = get_automation(session, automation_id)
    values: dict[str, Any] = {}
    if "name" in changes:
        values["name"] = _name(changes["name"])
    if "list_id" in changes or "section_id" in changes:
        values["list_id"], values["section_id"] = _scope(
            session,
            changes.get("list_id", rule.list_id if "section_id" not in changes else None),
            changes.get("section_id", rule.section_id if "list_id" not in changes else None),
        )
    if "trigger" in changes:
        values["trigger_json"] = clean_trigger(session, changes["trigger"])
    if "conditions" in changes:
        values["conditions_json"] = clean_conditions(session, changes["conditions"])
    if "actions" in changes:
        values["actions_json"] = clean_actions(session, changes["actions"])
    if "enabled" in changes:
        if changes["enabled"] is None:
            raise Invalid("enabled cannot be null")
        values["enabled"] = bool(changes["enabled"])
    needs_secret = (
        "actions_json" in values and _has_webhook(values["actions_json"]) and not rule.secret_enc
    )
    secret = None
    with apply_change(session, actor) as cx:
        for key, value in values.items():
            cx.set(rule, key, value, "automation.updated")
        if needs_secret:
            secret = _new_secret(rule)
            cx.record("automation.secret_rotated", subject_id=rule.id, covers=[rule])
    rule.new_secret = secret
    return rule


def delete_automation(session: Session, actor: Actor, automation_id: str) -> None:
    """Remove a rule for good; its run log goes with it, its history stays."""
    rule = get_automation(session, automation_id)
    with apply_change(session, actor) as cx:
        session.execute(delete(AutomationRun).where(AutomationRun.automation_id == rule.id))
        cx.delete(rule, "automation.deleted")


# --- Matching ------------------------------------------------------------------------------


def _names(value: Any) -> set[str]:
    return {str(v) for v in value} if isinstance(value, list) else set()


def trigger_fires(trigger: Mapping[str, Any], ev: Event, status_names: Mapping[str, Any]) -> bool:
    """Does `ev` fire `trigger`? `status_names` maps status id -> (name, kind)."""
    kind = trigger.get("type")
    spec = TRIGGERS.get(kind)
    if spec is None or ev.type not in (spec[0], *ALSO_ON.get(kind, ())):
        return False
    if kind == "agent_reported":
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        before = ev.before_json if isinstance(ev.before_json, dict) else {}
        state = after.get("state")
        if not state or state == before.get("state"):
            return False  # a new note while still working is not a new state
        return not trigger.get("state") or state == trigger["state"]
    if kind == "status_changed":
        new = status_names.get(ev.after_json) or (None, None)
        old = status_names.get(ev.before_json) or (None, None)
        if trigger.get("to") and (new[0] or "").lower() != trigger["to"].lower():
            return False
        if trigger.get("to_kind") and new[1] != trigger["to_kind"]:
            return False
        return not (trigger.get("from") and (old[0] or "").lower() != trigger["from"].lower())
    if kind in ("assignee_added", "assignee_removed"):
        before, after = _names(ev.before_json), _names(ev.after_json)
        changed = after - before if kind == "assignee_added" else before - after
        return bool(changed) and (not trigger.get("account") or trigger["account"] in changed)
    if kind in ("tag_added", "tag_removed"):
        if ev.field != "tags":
            return False
        before, after = _names(ev.before_json), _names(ev.after_json)
        changed = after - before if kind == "tag_added" else before - after
        return bool(changed) and (not trigger.get("tag") or trigger["tag"] in changed)
    if kind == "priority_changed":
        if ev.field != "priority":
            return False
        want = trigger.get("to")
        return not want or (ev.after_json or "none") == want
    if kind == "comment_added":
        want = trigger.get("mentions")
        return not want or want in _names((ev.after_json or {}).get("mentions"))
    if kind == "task_created":
        return True
    if kind == "task_unblocked":
        return bool((ev.after_json or {}).get("open", True))  # not for a task already closed
    return kind in ("due_arrives", "due_passes", "moved_in", "task_blocked")


def conditions_hold(conditions: Sequence[Mapping[str, Any]], view: Mapping[str, Any]) -> bool:
    """All conditions against an item view (`items.views()` shape)."""
    for c in conditions:
        field, value = c["field"], c["value"]
        status = view.get("status") or {}
        if field == "priority":
            ok = (view.get("priority") or "none") == value
        elif field == "tag":
            ok = value in (view.get("tags") or [])
        elif field == "assignee":
            handles = [a["handle"] for a in view.get("assignees") or []]
            ok = not handles if value == "nobody" else value in handles
        elif field == "status":
            ok = (status.get("name") or "").lower() == value.lower()
        elif field == "status_kind":
            ok = status.get("kind") == value
        elif field == "list":
            ok = view.get("list_id") == value
        else:
            ok = False
        if ok == (c["op"] == "is_not"):
            return False
    return True


def _in_scope(rule: Automation, item: Item, section_of: Mapping[str, str]) -> bool:
    if rule.list_id:
        return item.list_id == rule.list_id
    if rule.section_id:
        return section_of.get(item.list_id) == rule.section_id
    return True


# --- Running -------------------------------------------------------------------------------


def system_actor_for(session: Session, rule: Automation) -> Actor:
    from slate.service.due import system_actor

    base = system_actor(session)
    return Actor(
        account_id=base.account_id, source=base.source, session_ref=SESSION_PREFIX + rule.id
    )


def _fill(template: str, item: Item, view: Mapping[str, Any], actor_handle: str | None) -> str:
    status = (view.get("status") or {}).get("name") or ""
    return (
        template.replace("{title}", item.title)
        .replace("{actor}", f"@{actor_handle}" if actor_handle else "someone")
        .replace("{status}", status)
    )


def _due_in(days: int) -> str | None:
    if days < 0:
        return None
    return (utcnow().astimezone(LOCAL).date() + timedelta(days=days)).isoformat()


def _do(
    session: Session,
    actor: Actor,
    action: Mapping[str, Any],
    item: Item,
    actor_handle: str | None,
) -> str:
    """Run one action (one change). Returns a short note; raises ServiceError on failure."""
    kind = action["type"]
    view = items_svc.views(session, [item])[0]
    if kind == "set_status":
        items_svc.update_item(session, actor, item.id, {"status": action["status"]})
        return f"status {action['status']}"
    if kind == "set_priority":
        value = None if action["priority"] == "none" else action["priority"]
        items_svc.update_item(session, actor, item.id, {"priority": value})
        return f"priority {action['priority']}"
    if kind in ("assign", "unassign"):
        handles = {a["handle"] for a in view["assignees"]}
        if kind == "assign":
            handles.add(action["account"])
        else:
            handles.discard(action["account"])
        items_svc.update_item(session, actor, item.id, {"assignees": sorted(handles)})
        return f"{kind} @{action['account']}"
    if kind in ("add_tag", "remove_tag"):
        names = set(view["tags"])
        if kind == "add_tag":
            names.add(action["tag"])
        else:
            names.discard(action["tag"])
        items_svc.update_item(session, actor, item.id, {"tags": sorted(names)})
        return f"{kind.replace('_', ' ')} {action['tag']}"
    if kind == "move_list":
        if item.parent_id:
            raise Invalid("a subtask moves with its parent")
        items_svc.bulk_update(session, actor, [item.id], {"list_id": action["list_id"]})
        return "moved"
    if kind == "set_due":
        items_svc.update_item(session, actor, item.id, {"due_at": _due_in(action["in_days"])})
        return "due cleared" if action["in_days"] < 0 else f"due in {action['in_days']} days"
    if kind == "comment":
        body = _fill(action["body"], item, view, actor_handle)
        comments_svc.add_comment(session, actor, item.id, body_md=body)
        return "commented"
    if kind == "archive":
        items_svc.archive_item(session, actor, item.id)
        return "archived"
    raise Invalid(f"unknown action {kind}")


def run_rule(
    session: Session,
    rule: Automation,
    item: Item,
    *,
    event: Event | None = None,
    triggered_by: str | None = None,
    send: Sender | None = None,
) -> AutomationRun:
    """Run every action of `rule` on `item` and log the run. Each action is its own change."""
    actor = system_actor_for(session, rule)
    handle = (
        session.scalar(select(Account.handle).where(Account.id == triggered_by))
        if triggered_by
        else None
    )
    results: list[dict[str, Any]] = []
    for action in rule.actions_json or []:
        kind = action["type"]
        try:
            if kind == "webhook":
                if send is None:
                    raise Invalid("webhooks are not being sent (engine not running)")
                payload = jsonable(
                    {
                        "automation": {"id": rule.id, "name": rule.name},
                        "event": event_payload(event) if event is not None else None,
                        "item": items_svc.views(session, [item])[0],
                    }
                )
                ok, detail = send(action["url"], payload, signing_secret(rule))
                results.append({"action": kind, "ok": ok, "detail": detail})
                continue
            note = _do(session, actor, action, item, handle)
            results.append({"action": kind, "ok": True, "detail": note})
        except ServiceError as exc:
            session.rollback()
            results.append({"action": kind, "ok": False, "detail": str(exc)})
        except Exception as exc:  # a bug must not stop the engine; log and keep going
            session.rollback()
            log.exception("automation %s action %s crashed", rule.id, kind)
            results.append({"action": kind, "ok": False, "detail": f"error: {exc}"})
        session.refresh(item)
    oks = sum(1 for r in results if r["ok"])
    status = "ok" if oks == len(results) else "failed" if oks == 0 else "partial"
    run = AutomationRun(
        id=new_id(),
        automation_id=rule.id,
        event_id=event.id if event is not None else None,
        item_id=item.id,
        triggered_by=triggered_by,
        status=status,
        results_json=results,
        at=utcnow(),
    )
    session.add(run)
    session.commit()
    return run


def run_now(
    session: Session, actor: Actor, automation_id: str, *, item_id: str, send: Sender | None = None
) -> AutomationRun:
    """Run a rule's actions on one item now, skipping its trigger and conditions. The changes
    are the automation's (system actor); the run log names `actor` as who triggered it."""
    rule = get_automation(session, automation_id)
    item = items_svc.get_item(session, item_id)
    if item.archived_at is not None:
        raise Invalid("the item is archived")
    access.require_list_level(session, item.list_id, access.EDIT, "run automations on this task")
    return run_rule(session, rule, item, triggered_by=actor.account_id, send=send)


def _cursor(session: Session) -> EngineCursor:
    row = session.get(EngineCursor, CURSOR)
    if row is None:
        row = EngineCursor(
            name=CURSOR, event_id=session.scalar(select(func.max(Event.id))), updated_at=utcnow()
        )
        session.add(row)
        session.commit()
    return row


def start(session: Session) -> str | None:
    """Make sure the cursor exists (a new install starts at the newest event, never replaying
    history). Returns the cursor."""
    return _cursor(session).event_id


def process(session: Session, *, now: datetime | None = None, send: Sender | None = None) -> int:
    """Read every event after the cursor, run the rules they fire, advance the cursor.
    Returns the number of runs. Idempotent: the cursor moves after each event."""
    cursor = _cursor(session)
    rules = [r for r in list_automations(session) if r.enabled]
    if not rules:
        latest = session.scalar(select(func.max(Event.id)))
        if latest and latest != cursor.event_id:
            cursor.event_id, cursor.updated_at = latest, utcnow()
            session.commit()
        return 0
    wanted = {
        ev
        for r in rules
        if r.trigger_json["type"] in TRIGGERS
        for ev in (TRIGGERS[r.trigger_json["type"]][0], *ALSO_ON.get(r.trigger_json["type"], ()))
    }
    runs = 0
    while True:
        q = select(Event).order_by(Event.id).limit(PAGE)
        if cursor.event_id:
            q = q.where(Event.id > cursor.event_id)
        page = list(session.scalars(q))
        if not page:
            break
        for ev in page:
            if (
                ev.type in wanted
                and ev.item_id
                and not (ev.session_ref or "").startswith(SESSION_PREFIX)
            ):
                n = _handle(session, rules, ev, send)
                runs += n
            else:
                n = 0
            cursor = _cursor(session)
            cursor.event_id, cursor.updated_at = ev.id, utcnow()
            if n:
                session.commit()  # right after a run: a restart never runs it twice
        session.commit()
        if len(page) < PAGE:
            break
    _prune(session, now or utcnow())
    return runs


def _handle(session: Session, rules: Sequence[Automation], ev: Event, send: Sender | None) -> int:
    item = session.get(Item, ev.item_id)
    if item is None or item.archived_at is not None:
        return 0
    lst = session.get(List, item.list_id)
    if lst is None or lst.archived_at is not None:
        return 0
    section_of = {lst.id: lst.section_id}
    status_names: dict[str, Any] = {}
    if ev.type == "item.status_changed":
        status_names = {
            s.id: (s.name, s.kind.value)
            for s in session.scalars(
                select(Status).where(
                    Status.id.in_([x for x in (ev.before_json, ev.after_json) if x])
                )
            )
        }
    runs = 0
    view = None
    for rule in rules:
        if not _in_scope(rule, item, section_of):
            continue
        if rule.trigger_json.get("type") == "moved_in" and _was_in_scope(session, rule, ev):
            continue  # moved between two lists that are both in scope: not "into" it
        if not trigger_fires(rule.trigger_json, ev, status_names):
            continue
        view = items_svc.views(session, [item])[0]
        if not conditions_hold(rule.conditions_json or [], view):
            continue
        run_rule(session, rule, item, event=ev, triggered_by=ev.actor_id, send=send)
        runs += 1
        session.refresh(item)
        if item.archived_at is not None:
            break
    return runs


def _was_in_scope(session: Session, rule: Automation, ev: Event) -> bool:
    before = ev.before_json
    if rule.list_id:
        return before == rule.list_id
    if rule.section_id:
        old = session.get(List, before) if isinstance(before, str) else None
        return old is not None and old.section_id == rule.section_id
    return False


def _prune(session: Session, now: datetime) -> None:
    # Row by row, not a bulk DELETE: the write guard lets bookkeeping rows through only on
    # flush, and a run log this small never makes that slow.
    old = list(session.scalars(select(AutomationRun).where(AutomationRun.at < now - RUN_KEEP)))
    for run in old:
        session.delete(run)
    if old:
        session.commit()
