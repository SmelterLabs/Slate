"""ClickUp -> Slate importer.

Three steps, each usable alone:

1. `build_plan(source, team_id, now)` reads ClickUp (live or recorded, see `clickup_api`) and
   decides everything: destination list per task, statuses per list, recurrence families,
   what is dropped, flattened or left unassigned. It writes nothing.
2. `render_report(plan, result)` is the text you read (dry run) or the receipt (apply).
3. `apply_plan(session, handle, plan)` writes the plan through the service layer as account
   `handle` with source `system`. Re-running it updates items matched by `external_ref`
   (`clickup:<id>`) and never re-creates a section, list, comment or rule that exists.

Every write is a service-layer call, so every change is an event (AGENTS.md rule 3).

Where things go is data, not code: a `Mapping`, usually loaded from a JSON file with
`load_mapping` (`slate import clickup --mapping FILE`). Without one, every ClickUp list is
imported under its own name into a section named after its ClickUp space, nobody is assigned
and nothing is mirrored. A mapping file looks like this (every key optional):

    {
      "lists": {"Errands": ["Personal", "Todo"], "Recipes": ["Personal", "Home"]},
      "list_ids": {"901300000001": ["Work", "Projects"]},
      "map_unlisted": false,
      "assignees": {"1234567": "alex"},
      "audiences": {"Work": "work"},
      "source_tagged": [["Personal", "Home"]],
      "mirror": {"prefix": "[Shared]", "tag": "shared", "section": "Work", "list": "Shared"},
      "collapse": true,
      "include_closed": true
    }

`lists` matches ClickUp list names case-insensitively, `list_ids` matches list ids (and wins).
When either is given, lists matching neither are left out and reported, unless `map_unlisted`
is true. `source_tagged` lists the destinations that merge several ClickUp lists and so tag
each task with the list it came from. `mirror` moves every task whose name starts with
`prefix` into one shared list, tagged `tag`, whatever list it lived in.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.dates import parse_when
from slate.ids import slugify
from slate.importers import collapse
from slate.importers.clickup_api import (
    Source,
    fetch_comments,
    fetch_lists,
    fetch_tasks,
    list_statuses,
)
from slate.models import Account, AccountKind, Event, Item, Priority, Role
from slate.models import Source as EventSource
from slate.service import checklists as checklists_svc
from slate.service import comments as comments_svc
from slate.service import items as items_svc
from slate.service import lists as lists_svc
from slate.service import recurrences as recurrences_svc
from slate.service import sections as sections_svc
from slate.service.core import Actor
from slate.service.errors import Invalid
from slate.settings import settings

TZ = ZoneInfo(settings.timezone)  # dates in the export are read in the workspace timezone
WINDOW_DAYS = 14  # recurrence members due later than today + this are dropped
MIN_FAMILY = 3
SESSION_REF = "import:clickup"

ACTIVE_NAMES = frozenset({"in progress", "shipping"})
CLOSED_TYPES = frozenset({"closed", "done"})
PRIORITY_IDS = {"1": "urgent", "2": "high", "3": "normal", "4": "low"}
FOOTER = re.compile(r"Imported from ClickUp \S+ on (\d{4}-\d{2}-\d{2})")
COMMENT_MARK = "<!-- clickup-comment:{} -->"

Dest = tuple[str, str]
# progress(phase, done, total): called while reading ClickUp and while writing (see imports).
Progress = Callable[[str, int, int], None]


def _no_progress(_phase: str, _done: int, _total: int) -> None:
    return None


@dataclass(frozen=True)
class Mirror:
    """Tasks whose name starts with `prefix` (case-insensitive) go to `dest` from whatever list
    they lived in, tagged `tag`; the destination takes the statuses of their source lists."""

    prefix: str
    tag: str
    dest: Dest


@dataclass
class Mapping:
    """Where things go (see the module docstring for the JSON form and `load_mapping`).

    `lists` maps ClickUp list ids to Slate (section, list) names and `list_names` maps ClickUp
    list names (case-insensitive); an id match wins. When both are empty, or `map_unlisted` is
    true, a list matching neither goes to (its ClickUp space name, its own name). Otherwise it
    is not imported, and reported. `assignees` maps ClickUp user ids to Slate account handles;
    anyone else is left unassigned and reported. Destinations keep the order of `lists`, then
    `list_names`, then the order ClickUp lists them in.
    """

    lists: dict[str, Dest] = field(default_factory=dict)
    list_names: dict[str, Dest] = field(default_factory=dict)
    map_unlisted: bool = False
    assignees: dict[str, str] = field(default_factory=dict)
    audiences: dict[str, str] = field(default_factory=dict)  # section name -> audience
    source_tagged: frozenset[Dest] = frozenset()
    mirror: Mirror | None = None
    collapse: bool = True
    include_closed: bool = True

    def __post_init__(self) -> None:
        self.list_names = {k.strip().lower(): v for k, v in self.list_names.items()}

    @property
    def catch_all(self) -> bool:
        return self.map_unlisted or not (self.lists or self.list_names)

    def dest_of(self, entry: dict) -> Dest | None:
        lst = entry["list"]
        dest = self.lists.get(str(lst["id"])) or self.list_names.get(lst["name"].strip().lower())
        if dest is None and self.catch_all:
            space = ((entry.get("space") or {}).get("name") or "ClickUp").strip()
            dest = (space or "ClickUp", lst["name"].strip())
        return dest

    @property
    def destinations(self) -> tuple[Dest, ...]:
        """The destinations named up front (catch-all ones are found while reading)."""
        seen: dict[Dest, None] = {}
        for dest in [*self.lists.values(), *self.list_names.values()]:
            seen.setdefault(dest, None)
        if self.mirror is not None:
            seen.setdefault(self.mirror.dest, None)
        return tuple(seen)

    def sections_for(self, destinations: tuple[Dest, ...]) -> tuple[tuple[str, str], ...]:
        names: dict[str, None] = {}
        for section, _ in destinations:
            names.setdefault(section, None)
        return tuple((n, self.audiences.get(n) or guess_audience(n)) for n in names)

    @property
    def sections(self) -> tuple[tuple[str, str], ...]:
        return self.sections_for(self.destinations)

    @classmethod
    def from_dict(cls, data: dict) -> Mapping:
        """A Mapping from the JSON form described in the module docstring."""

        def dest(value: Any, where: str) -> Dest:
            if (
                not isinstance(value, list | tuple)
                or len(value) != 2
                or not all(isinstance(v, str) and v.strip() for v in value)
            ):
                raise Invalid(f"{where}: a destination is [section name, list name]")
            return (value[0].strip(), value[1].strip())

        if not isinstance(data, dict):
            raise Invalid("a ClickUp mapping is a JSON object")
        known = {
            "lists", "list_ids", "map_unlisted", "assignees", "audiences", "source_tagged",
            "mirror", "collapse", "include_closed",
        }  # fmt: skip
        unknown = sorted(set(data) - known)
        if unknown:
            raise Invalid(f"unknown key(s) in the ClickUp mapping: {', '.join(unknown)}")
        mirror = None
        if data.get("mirror"):
            m = data["mirror"]
            if not isinstance(m, dict) or not str(m.get("prefix") or "").strip():
                raise Invalid("mirror: needs a prefix")
            mirror = Mirror(
                prefix=str(m["prefix"]).strip(),
                tag=str(m.get("tag") or "").strip().lower(),
                dest=dest([m.get("section"), m.get("list")], "mirror"),
            )
        return cls(
            lists={
                str(k): dest(v, f"list_ids.{k}") for k, v in (data.get("list_ids") or {}).items()
            },
            list_names={k: dest(v, f"lists.{k}") for k, v in (data.get("lists") or {}).items()},
            map_unlisted=bool(data.get("map_unlisted", False)),
            assignees={
                str(k): str(v).strip().lower() for k, v in (data.get("assignees") or {}).items()
            },
            audiences={str(k): str(v) for k, v in (data.get("audiences") or {}).items()},
            source_tagged=frozenset(
                dest(v, "source_tagged") for v in data.get("source_tagged") or []
            ),
            mirror=mirror,
            collapse=bool(data.get("collapse", True)),
            include_closed=bool(data.get("include_closed", True)),
        )


def load_mapping(path: str | Path) -> Mapping:
    """Read a mapping file (JSON, see the module docstring)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Invalid(f"cannot read the ClickUp mapping {path}: {exc}") from None
    except ValueError as exc:
        raise Invalid(f"the ClickUp mapping {path} is not valid JSON: {exc}") from None
    return Mapping.from_dict(data)


def guess_audience(section: str) -> str:
    """An audience for a new section from its name; personal unless the name says otherwise."""
    words = set(re.findall(r"[a-z]+", section.lower()))
    if words & {"work", "job", "office", "business", "client", "clients", "company"}:
        return "work"
    if words & {"ai", "agent", "agents", "dev", "llm"}:
        return "ai"
    if words & {"family", "kids", "household"}:
        return "family"
    return "personal"


def dest_label(dest: Dest) -> str:
    return f"{dest[0]} → {dest[1]}"


def _day(d: date) -> str:
    return f"{d:%a} {d:%b} {d.day}"


# --- The plan ------------------------------------------------------------------------------


@dataclass
class PlannedItem:
    ref: str  # ClickUp task id
    dest: Dest
    title: str
    body: str  # description plus notes, without the import footer
    url: str
    status: str
    closed: bool
    priority: str | None
    start_at: datetime | None
    due_at: datetime | None
    tags: list[str]
    assignees: list[str]
    parent_ref: str | None = None
    family: str | None = None  # family key, when this task belongs to a collapsed family
    comments: list[dict] = field(default_factory=list)
    checklists: list[dict] = field(default_factory=list)  # {name, items: [{name, resolved}]}
    source_list: str = ""  # the ClickUp list id it came from
    due_date_only: bool = False  # ClickUp's due date had no time (`due_date_time: false`)

    @property
    def external_ref(self) -> str:
        return f"clickup:{self.ref}"


@dataclass
class PlannedRule:
    dest: Dest
    key: str
    title_template: str
    rrule: str
    dtstart: date
    members: list[dict]  # every ClickUp task in the family, sorted by due date
    imported: list[str]  # refs imported as ordinary items (overdue or inside the window)
    dropped: list[str]  # refs beyond the window: not imported, the rule makes them
    body: str | None
    priority: str | None
    tags: list[str]
    assignees: list[str]

    @property
    def words(self) -> str:
        return f"{collapse.describe(self.rrule)}, starting {_day(self.dtstart)}"


@dataclass
class Irregular:
    dest: Dest
    title: str
    members: list[dict]
    gaps: tuple[int, ...]


@dataclass
class Plan:
    team_id: str
    now: datetime
    window_end: date
    items: list[PlannedItem] = field(default_factory=list)
    statuses: dict[Dest, list[dict]] = field(default_factory=dict)
    rules: list[PlannedRule] = field(default_factory=list)
    irregular: list[Irregular] = field(default_factory=list)
    lapsed: list[PlannedRule] = field(default_factory=list)  # regular, but every copy is closed
    twins: list[tuple[dict, dict]] = field(default_factory=list)  # (skipped copy, kept task)
    dropped: list[tuple[dict, str]] = field(default_factory=list)  # (task, rule title)
    flattened: list[tuple[dict, dict]] = field(default_factory=list)  # (task, new parent)
    orphans: list[tuple[dict, str]] = field(default_factory=list)  # (subtask, reason)
    unassigned: list[tuple[dict, list[str]]] = field(default_factory=list)
    unmapped_lists: list[dict] = field(default_factory=list)  # {space, folder, list, tasks}
    notes: list[str] = field(default_factory=list)
    source_lists: list[dict] = field(default_factory=list)
    task_count: int = 0
    closed_count: int = 0
    requests: int = 0
    destinations: tuple[Dest, ...] = ()
    sections: tuple[tuple[str, str], ...] = ()


# --- Field mapping -------------------------------------------------------------------------


def _ms(value: Any, date_only: bool = False) -> datetime | None:
    """ClickUp ms-epoch string -> aware UTC. A date with no time (`*_date_time: false`) follows
    Slate's date rule instead: 17:00 workspace time on that local day."""
    if value in (None, ""):
        return None
    dt = datetime.fromtimestamp(int(value) / 1000, UTC)
    if date_only:
        return parse_when(dt.astimezone(TZ).date().isoformat())
    return dt


def _priority(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("priority") or value.get("id")
    text = str(value).strip().lower()
    text = PRIORITY_IDS.get(text, text)
    return text if text in {p.value for p in Priority} else None


def _status_kind(status: dict) -> str:
    if (status.get("type") or "").lower() in CLOSED_TYPES:
        return "done"
    if (status.get("status") or "").strip().lower() in ACTIVE_NAMES:
        return "active"
    return "open"


def _status_spec(status: dict) -> dict:
    return {
        "name": status["status"].strip(),
        "kind": _status_kind(status),
        "color": status.get("color"),
    }


def _merge_statuses(found: list[dict]) -> list[dict]:
    """Unique by name (first seen wins), open/active in order first, done statuses last."""
    seen: dict[str, dict] = {}
    for st in found:
        seen.setdefault(st["name"].lower(), st)
    specs = list(seen.values())
    return [s for s in specs if s["kind"] != "done"] + [s for s in specs if s["kind"] == "done"]


def _twin_key(task: dict) -> tuple:
    return (
        (task.get("name") or "").strip().lower(),
        task.get("due_date"),
        ((task.get("status") or {}).get("status") or "").lower(),
        (task.get("description") or "").strip()[:200],
    )


def _local_day(dt: datetime | None) -> date | None:
    return dt.astimezone(TZ).date() if dt else None


def _description(task: dict) -> str:
    return (
        task.get("markdown_description")
        or task.get("description")
        or task.get("text_content")
        or ""
    ).strip()


# --- Building the plan ---------------------------------------------------------------------


def _checklists(task: dict) -> list[dict]:
    out = []
    for cl in task.get("checklists") or []:
        entries = [
            {"name": (e.get("name") or "").strip(), "resolved": bool(e.get("resolved"))}
            for e in sorted(cl.get("items") or [], key=lambda e: e.get("orderindex") or 0)
            if (e.get("name") or "").strip()
        ]
        if entries:
            out.append({"name": (cl.get("name") or "Checklist").strip()[:200], "items": entries})
    return out


def build_plan(
    source: Source,
    team_id: str,
    now: datetime | None = None,
    *,
    with_comments: bool = True,
    mapping: Mapping | None = None,
    progress: Progress = _no_progress,
) -> Plan:
    """Read ClickUp and decide everything. Writes nothing.

    `mapping` says where lists go and who is who; without one, every list is imported under
    its own name into a section named after its ClickUp space (see `Mapping`)."""
    mapping = mapping if mapping is not None else Mapping()
    now = now or datetime.now(UTC)
    today = now.astimezone(TZ).date()
    plan = Plan(team_id=str(team_id), now=now, window_end=today + timedelta(days=WINDOW_DAYS))
    assignee_map = mapping.assignees
    mirror = mapping.mirror
    source_tagged = mapping.source_tagged

    # 1. Lists: mapped ones are read; the rest are named in the report.
    tasks: dict[str, dict] = {}
    home: dict[str, Dest] = {}  # task id -> destination of its ClickUp list
    found_statuses: dict[Dest, list[dict]] = defaultdict(list)
    list_statuses_by_id: dict[str, list[dict]] = {}
    progress("Reading ClickUp lists", 0, 0)
    entries = fetch_lists(source, plan.team_id)
    for entry in entries:
        dest = mapping.dest_of(entry)
        if dest is None:
            plan.unmapped_lists.append(entry)
            continue
        plan.source_lists.append({**entry, "dest": dest})
    found: dict[Dest, None] = dict.fromkeys([*mapping.lists.values(), *mapping.list_names.values()])
    for entry in plan.source_lists:
        found.setdefault(entry["dest"], None)
    if mirror is not None:
        found.setdefault(mirror.dest, None)
    plan.destinations = tuple(found)
    plan.sections = mapping.sections_for(plan.destinations)
    for n, entry in enumerate(plan.source_lists):
        lst, dest = entry["list"], entry["dest"]
        progress(f"Reading tasks in “{lst['name']}”", n, len(plan.source_lists))
        specs = [_status_spec(s) for s in list_statuses(source, entry)]
        list_statuses_by_id[lst["id"]] = specs
        found_statuses[dest] += specs
        for task in fetch_tasks(source, plan.team_id, lst["id"]):
            if not mapping.include_closed and _is_closed(task):
                continue
            if task["id"] not in tasks:
                tasks[task["id"]] = task
                home[task["id"]] = dest

    # 1b. Exact twins. A ClickUp list copied wholesale from another one holds closed tasks
    # identical in name, due date, status and description. A closed task that exactly matches
    # one fetched from another list is skipped and reported; the copy whose destination comes
    # first in destination order is the one kept.
    order = {d: i for i, d in enumerate(plan.destinations)}
    by_twin_key: dict[tuple, list[dict]] = defaultdict(list)
    for task in tasks.values():
        by_twin_key[_twin_key(task)].append(task)
    for group in by_twin_key.values():
        dests = {home[t["id"]] for t in group}
        if len(group) < 2 or len(dests) < 2:
            continue
        group.sort(key=lambda t: (order.get(home[t["id"]], 99), t.get("date_created") or ""))
        kept = group[0]
        for t in group[1:]:
            if home[t["id"]] == home[kept["id"]]:
                continue  # same list: a genuine repeat, not a copy
            if ((t.get("status") or {}).get("type") or "").lower() in CLOSED_TYPES:
                plan.twins.append((tasks.pop(t["id"]), kept))
                home.pop(t["id"], None)

    # 2. Route each task and map its fields.
    planned: dict[str, PlannedItem] = {}
    for tid, task in tasks.items():
        name = task["name"].strip()
        mirrored = mirror is not None and name.upper().startswith(mirror.prefix.upper())
        dest = mirror.dest if mirrored else home[tid]
        status = task.get("status") or {}
        spec = _status_spec(status) if status.get("status") else None
        if spec:
            found_statuses[dest].append(spec)
        if mirrored:  # the mirror takes the statuses of the lists its tasks come from
            found_statuses[dest] += list_statuses_by_id.get(task.get("list", {}).get("id"), [])
        handles, others = [], []
        for person in task.get("assignees") or []:
            handle = assignee_map.get(str(person.get("id")))
            if handle:
                handles.append(handle)
            else:
                others.append(person.get("username") or str(person.get("id")))
        if others:
            plan.unassigned.append((task, others))
        tags = [t["name"] for t in task.get("tags") or []]
        if mirrored and mirror.tag and mirror.tag not in {t.lower() for t in tags}:
            tags.append(mirror.tag)
        if dest in source_tagged:
            source_name = (task.get("list") or {}).get("name") or ""
            if source_name:
                tags.append(re.sub(r"[^a-z0-9]+", "-", source_name.lower()).strip("-"))
        closed = (status.get("type") or "").lower() in CLOSED_TYPES
        plan.closed_count += closed
        planned[tid] = PlannedItem(
            ref=tid,
            dest=dest,
            title=name,
            body=_description(task),
            url=task.get("url") or f"https://app.clickup.com/t/{tid}",
            status=spec["name"] if spec else "",
            closed=closed,
            priority=_priority(task.get("priority")),
            start_at=_ms(task.get("start_date"), task.get("start_date_time") is False),
            due_at=_ms(task.get("due_date"), task.get("due_date_time") is False),
            tags=sorted({t.strip().lower() for t in tags if t.strip()}),
            assignees=sorted(set(handles)),
            parent_ref=task.get("parent"),
            checklists=_checklists(task),
            source_list=str((task.get("list") or {}).get("id") or ""),
            due_date_only=bool(task.get("due_date")) and task.get("due_date_time") is False,
        )
    plan.task_count = len(tasks)
    plan.statuses = {
        d: _merge_statuses(found_statuses[d]) for d in plan.destinations if found_statuses[d]
    }

    # 3. Recurrence families: top-level tasks with a due date, grouped per list and name.
    # Closed copies count as evidence for the schedule (ClickUp keeps no recurrence field, so
    # the completed history is the only trail); only open copies become instances or get
    # dropped. A name shared by chores with different tag sets (two places, say) is split into
    # one family per tag set, and the tag that tells them apart goes into the title.
    skip: set[str] = set()
    groups: dict[tuple[Dest, str], list[PlannedItem]] = defaultdict(list)
    for pi in planned.values():
        if pi.parent_ref or pi.due_at is None or not mapping.collapse:
            continue
        key = collapse.family_key(pi.title)
        if key:
            groups[(pi.dest, key)].append(pi)
    families: list[tuple[Dest, str, list[PlannedItem], str]] = []
    for (dest, key), members in groups.items():
        by_tags: dict[tuple[str, ...], list[PlannedItem]] = defaultdict(list)
        for m in members:
            by_tags[tuple(m.tags)].append(m)
        if len(by_tags) == 1:
            families.append((dest, key, members, ""))
            continue
        common = set.intersection(*(set(t) for t in by_tags))
        for tag_set, subset in by_tags.items():
            distinct = [t for t in tag_set if t not in common]
            families.append((dest, key, subset, f" ({', '.join(distinct)})" if distinct else ""))
    for dest, key, members, suffix in families:
        if len(members) < MIN_FAMILY:
            continue
        members.sort(key=lambda m: m.due_at)
        days = [_local_day(m.due_at) for m in members]
        inference = collapse.infer_rrule(days)
        raw = [tasks[m.ref] for m in members]
        if inference.rrule is None:
            title = collapse.strip_dates(members[0].title)[0] + suffix
            plan.irregular.append(Irregular(dest, title, raw, inference.gaps))
            continue
        open_members = [(m, d) for m, d in zip(members, days, strict=True) if not m.closed]
        keep = [m for m, d in open_members if d <= plan.window_end]
        drop = [m for m, d in open_members if d > plan.window_end]
        dtstart = (
            _local_day(drop[0].due_at)
            if drop
            else collapse.next_after(inference.rrule, days[0], days[-1])
        )
        template = collapse.title_template([m.title for m in members]) + suffix
        for m in keep:
            m.family = key
        for m in drop:
            skip.add(m.ref)
            plan.dropped.append((tasks[m.ref], template))
        priorities = Counter(m.priority for m in members if m.priority)
        bodies = [m.body for m in members if m.body]
        # A family with no open copy stopped being done at some point: report it as lapsed and
        # create no rule. Reviving it is an explicit decision made after the import.
        target = plan.rules if open_members else plan.lapsed
        target.append(
            PlannedRule(
                dest=dest,
                key=key,
                title_template=template,
                rrule=inference.rrule,
                dtstart=dtstart,
                members=raw,
                imported=[m.ref for m in keep],
                dropped=[m.ref for m in drop],
                body=bodies[-1] if bodies else None,
                priority=priorities.most_common(1)[0][0] if priorities else None,
                tags=sorted({t for m in members for t in m.tags}),
                assignees=sorted({a for m in members for a in m.assignees}),
            )
        )

    # 4. Subtasks nest four deep. Deeper ones hang off the ancestor four levels up; a missing
    # parent -> top level.
    for pi in planned.values():
        if not pi.parent_ref or pi.ref in skip:
            continue
        parent = planned.get(pi.parent_ref)
        if parent is None or parent.ref in skip:
            why = "its parent is not imported" if parent else "its parent is in a list not imported"
            plan.orphans.append((tasks[pi.ref], why))
            pi.body = _join(
                pi.body, f"_Was a ClickUp subtask (parent clickup:{pi.parent_ref}); {why}._"
            )
            pi.parent_ref = None
            continue
        chain = [parent]
        while chain[-1].parent_ref and chain[-1].parent_ref in planned:
            chain.append(planned[chain[-1].parent_ref])
        if len(chain) >= 4:
            # The parent is already four down: hang this one three below the top instead.
            top = chain[-4]
            pi.body = _join(
                pi.body,
                f"_Was a subtask of “{parent.title}” (clickup:{parent.ref}), itself a subtask; "
                f"Slate nests four deep, so it now sits under “{top.title}”._",
            )
            plan.flattened.append((tasks[pi.ref], tasks[top.ref]))
            pi.parent_ref = top.ref
        if pi.dest != planned[pi.parent_ref].dest:
            plan.notes.append(
                f"“{pi.title}” (clickup:{pi.ref}) would go to {dest_label(pi.dest)} but a subtask "
                f"lives with its parent, so it goes to {dest_label(planned[pi.parent_ref].dest)}."
            )
            pi.dest = planned[pi.parent_ref].dest

    # 5. Order: top-level first, then subtasks (their parents exist by then).
    kept = [pi for pi in planned.values() if pi.ref not in skip]
    plan.items = [pi for pi in kept if not pi.parent_ref] + [pi for pi in kept if pi.parent_ref]

    if with_comments:
        for n, pi in enumerate(plan.items):
            progress("Reading comments", n, len(plan.items))
            pi.comments = fetch_comments(source, pi.ref)
        replies = sum(1 for pi in plan.items for c in pi.comments if int(c.get("reply_count") or 0))
        if replies:
            plan.notes.append(
                f"{replies} comment(s) have threaded replies in ClickUp; replies are not imported."
            )
    plan.requests = getattr(source, "requests", 0)
    return plan


def _is_closed(task: dict) -> bool:
    return ((task.get("status") or {}).get("type") or "").lower() in CLOSED_TYPES


def _join(*parts: str | None) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


# --- Applying the plan ---------------------------------------------------------------------


@dataclass
class ApplyResult:
    sections_created: int = 0
    lists_created: int = 0
    statuses_added: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    archived: int = 0
    comments_added: int = 0
    checklists_added: int = 0
    rules_created: int = 0
    rules_existing: list[str] = field(default_factory=list)
    owned_by_rule: list[str] = field(default_factory=list)  # refs a pre-existing rule now makes
    events: int = 0


def _events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Event)) or 0


def _body(pi: PlannedItem, existing: str | None, today: date) -> str:
    """The body with its footer. A re-run keeps the original import date, so an unchanged task
    stays unchanged."""
    stamp = today.isoformat()
    if existing and (m := FOOTER.search(existing)):
        stamp = m.group(1)
    return _join(pi.body, f"Imported from ClickUp {pi.url} on {stamp}")


def _comment_body(comment: dict) -> str:
    who = (comment.get("user") or {}).get("username") or "someone"
    when = datetime.fromtimestamp(int(comment["date"]) / 1000, UTC).astimezone(TZ)
    text = (comment.get("comment_text") or "").strip() or "_(empty comment)_"
    header = f"**ClickUp comment** by {who} · {when:%Y-%m-%d %H:%M} {when:%Z}"
    return f"{header} {COMMENT_MARK.format(comment['id'])}\n\n{text}"


def _ensure_structure(session: Session, actor: Actor, plan: Plan, result: ApplyResult) -> dict:
    """Sections, lists and statuses. Returns {dest: list id}."""
    sections = {s.slug: s for s in sections_svc.list_sections(session, include_archived=True)}
    for name, audience in plan.sections:
        if slugify(name) not in sections:
            sections[slugify(name)] = sections_svc.create_section(
                session, actor, name=name, audience=audience
            )
            result.sections_created += 1
    out: dict[Dest, str] = {}
    for dest in plan.destinations:
        section = sections[slugify(dest[0])]
        wanted = plan.statuses.get(dest)
        existing = {
            lst.slug: lst
            for lst in lists_svc.list_lists(session, section_id=section.id, include_archived=True)
        }
        lst = existing.get(slugify(dest[1]))
        if lst is None:
            if wanted is None and not any(pi.dest == dest for pi in plan.items):
                continue  # nothing goes there; do not make an empty list
            lst = lists_svc.create_list(
                session, actor, section_id=section.id, name=dest[1], statuses=wanted
            )
            result.lists_created += 1
        elif wanted:
            current = lists_svc.statuses_of(session, lst.id)
            names = {s.name.lower() for s in current}
            missing = [s for s in wanted if s["name"].lower() not in names]
            if missing:
                specs = [
                    {"id": s.id, "name": s.name, "kind": s.kind.value, "color": s.color}
                    for s in current
                ]
                lists_svc.set_statuses(session, actor, lst.id, specs + missing)
                result.statuses_added += len(missing)
        out[dest] = lst.id
    return out


def owner_handle(session: Session) -> str:
    """The handle of the workspace owner (the first owner account), the default import actor."""
    handle = session.scalar(
        select(Account.handle)
        .where(Account.role == Role.owner, Account.kind == AccountKind.person)
        .order_by(Account.created_at, Account.handle)
        .limit(1)
    )
    if handle is None:
        raise Invalid("no owner account in Slate; create one or pass --as HANDLE")
    return handle


def apply_plan(
    session: Session,
    handle: str,
    plan: Plan,
    *,
    today: date | None = None,
    session_ref: str = SESSION_REF,
    progress: Progress = _no_progress,
) -> ApplyResult:
    """Write the plan as account `handle` (source `system`). Safe to run again."""
    account = session.scalar(select(Account).where(Account.handle == handle.strip().lower()))
    if account is None:
        raise Invalid(f"no Slate account '{handle}'; create it before importing")
    needed = {a for pi in plan.items for a in pi.assignees} | {
        a for r in plan.rules for a in r.assignees
    }
    known = set(session.scalars(select(Account.handle).where(Account.handle.in_(needed))))
    if needed - known:
        raise Invalid(f"assignee account(s) missing in Slate: {', '.join(sorted(needed - known))}")
    actor = Actor(account.id, EventSource.system, session_ref=session_ref)
    today = today or plan.now.astimezone(TZ).date()
    result = ApplyResult()
    before_events = _events(session)

    progress("Creating sections and lists", 0, len(plan.items))
    list_ids = _ensure_structure(session, actor, plan, result)

    # Rules that already exist own their dates from their dtstart on: those tasks are not
    # imported again (the rule spawns them), or a later re-run would duplicate them.
    existing_rules: dict[tuple[str, str], Any] = {}
    for rule in recurrences_svc.list_recurrences(session, include_ended=True):
        existing_rules[(rule.list_id, collapse.family_key(rule.title_template))] = rule
    owned_from: dict[tuple[Dest, str], date] = {}
    for pr in plan.rules:
        rule = existing_rules.get((list_ids[pr.dest], pr.key))
        if rule is not None:
            owned_from[(pr.dest, pr.key)] = rule.dtstart.astimezone(ZoneInfo(rule.timezone)).date()

    by_ref = {
        i.external_ref: i
        for i in session.scalars(select(Item).where(Item.external_ref.like("clickup:%")))
    }
    for n, pi in enumerate(plan.items):
        progress("Importing tasks", n, len(plan.items))
        start = owned_from.get((pi.dest, pi.family)) if pi.family else None
        if start is not None and _local_day(pi.due_at) >= start and pi.external_ref not in by_ref:
            result.owned_by_rule.append(pi.ref)
            continue
        item = by_ref.get(pi.external_ref)
        fields = {
            "title": pi.title,
            "body_md": _body(pi, item.body_md if item else None, today),
            "status": pi.status or None,
            "priority": pi.priority,
            "start_at": pi.start_at,
            "due_at": pi.due_at,
            "due_date_only": pi.due_date_only,
            "tags": pi.tags,
            "assignees": pi.assignees,
        }
        if fields["status"] is None:
            del fields["status"]
        if item is None:
            parent = by_ref.get(f"clickup:{pi.parent_ref}") if pi.parent_ref else None
            if parent is not None:
                item = items_svc.create_item(
                    session, actor, parent_id=parent.id, external_ref=pi.external_ref, **fields
                )
            else:
                item = items_svc.create_item(
                    session,
                    actor,
                    list_id=list_ids[pi.dest],
                    external_ref=pi.external_ref,
                    **fields,
                )
            by_ref[pi.external_ref] = item
            result.created += 1
        else:
            mark = _events(session)
            items_svc.update_item(session, actor, item.id, fields)
            if _events(session) > mark:
                result.updated += 1
            else:
                result.unchanged += 1
        if pi.comments:
            have = " ".join(c.body_md for c in items_svc.comments_of(session, item.id))
            for comment in pi.comments:
                if COMMENT_MARK.format(comment["id"]) not in have:
                    comments_svc.add_comment(
                        session, actor, item.id, body_md=_comment_body(comment)
                    )
                    result.comments_added += 1
        if pi.checklists:
            have_names = {c["name"].lower() for c in checklists_svc.checklists_of(session, item.id)}
            for cl in pi.checklists:
                if cl["name"].lower() in have_names:
                    continue
                made = checklists_svc.create_checklist(
                    session, actor, item.id, name=cl["name"], items=[e["name"] for e in cl["items"]]
                )
                done = [e["name"] for e in cl["items"] if e["resolved"]]
                if done:
                    entries = next(
                        c
                        for c in checklists_svc.checklists_of(session, item.id)
                        if c["id"] == made.id
                    )["items"]
                    for entry, spec in zip(entries, cl["items"], strict=True):
                        if spec["resolved"]:
                            checklists_svc.update_entry(session, actor, entry["id"], {"done": True})
                result.checklists_added += 1

    progress("Archiving closed tasks", len(plan.items), len(plan.items))
    for pi in plan.items:  # after every item exists, so a closed parent never blocks a child
        item = by_ref.get(pi.external_ref)
        if pi.closed and item is not None and item.archived_at is None:
            items_svc.archive_item(session, actor, item.id)
            result.archived += 1

    for pr in plan.rules:
        if (pr.dest, pr.key) in owned_from:
            result.rules_existing.append(pr.title_template)
            continue
        recurrences_svc.create_recurrence(
            session,
            actor,
            list_id=list_ids[pr.dest],
            title_template=pr.title_template,
            rrule=pr.rrule,
            dtstart=pr.dtstart.isoformat(),
            now=plan.now,
            body_template_md=pr.body,
            priority=pr.priority,
            tags=pr.tags,
            assignees=pr.assignees,
        )
        result.rules_created += 1

    result.events = _events(session) - before_events
    return result


# --- The report ----------------------------------------------------------------------------


def _task_line(task: dict) -> str:
    due = _ms(task.get("due_date"))
    when = f" · due {_day(_local_day(due))}" if due else ""
    return f"clickup:{task['id']}  {task['name'].strip()}{when}"


def render_report(plan: Plan, result: ApplyResult | None = None) -> str:
    """The plain-text report: what goes where, every rule in plain words, everything left out."""
    out: list[str] = []
    w = out.append
    local = plan.now.astimezone(TZ)
    mode = "APPLIED" if result else "DRY RUN — nothing written"
    w(f"ClickUp import ({mode})")
    w(f"Workspace {plan.team_id}, read {local:%Y-%m-%d %H:%M} {local:%Z}.")
    w(
        f"Read {plan.task_count} tasks ({plan.closed_count} closed) from "
        f"{len(plan.source_lists)} lists; {sum(len(pi.comments) for pi in plan.items)} comments; "
        f"{plan.requests} ClickUp requests."
    )
    w("")

    w("Destinations")
    counts: dict[Dest, Counter] = defaultdict(Counter)
    for pi in plan.items:
        c = counts[pi.dest]
        c["items"] += 1
        c["closed"] += pi.closed
        c["subtasks"] += bool(pi.parent_ref)
        c["overdue"] += (not pi.closed) and pi.due_at is not None and pi.due_at < plan.now
    for dest in plan.destinations:
        c = counts.get(dest)
        if not c:
            continue
        extra = [f"{c['closed']} closed → archived"] if c["closed"] else []
        extra += [f"{c['subtasks']} subtasks"] if c["subtasks"] else []
        extra += [f"{c['overdue']} overdue"] if c["overdue"] else []
        tail = f" ({', '.join(extra)})" if extra else ""
        w(f"  {dest_label(dest):<26} {c['items']:>4} items{tail}")
        sts = ", ".join(
            s["name"] + ("" if s["kind"] == "open" else f" [{s['kind']}]")
            for s in plan.statuses.get(dest, [])
        )
        w(f"  {'':<26}      statuses: {sts}")
    w(f"  {'Total':<26} {len(plan.items):>4} items")
    w("")

    w(f"Recurrence rules ({len(plan.rules)})")
    if not plan.rules:
        w("  none")
    for pr in plan.rules:
        w(f"  {dest_label(pr.dest)}: “{pr.title_template}”")
        w(f"      {pr.words}  [{pr.rrule}]")
        due = {pi.ref: pi.due_at for pi in plan.items}
        overdue = sum(1 for ref in pr.imported if due.get(ref) and due[ref] < plan.now)
        w(
            f"      replaces {len(pr.members)} ClickUp tasks: {len(pr.imported)} imported"
            f" ({overdue} overdue), {len(pr.dropped)} dropped (the rule makes them)"
        )
    w("")

    w(f"Not collapsed — dates are irregular ({len(plan.irregular)})")
    if not plan.irregular:
        w("  none")
    for ir in plan.irregular:
        gaps = ", ".join(str(g) for g in ir.gaps)
        w(f"  {dest_label(ir.dest)}: “{ir.title}” — {len(ir.members)} tasks, gaps {gaps} days;")
        w("      imported as separate dated tasks")
    w("")

    w(f"Lapsed — a regular schedule, but every copy is closed; no rule made ({len(plan.lapsed)})")
    if not plan.lapsed:
        w("  none")
    for pr in plan.lapsed:
        last = _day(_local_day(_ms(pr.members[-1].get("due_date"))))
        w(f"  {dest_label(pr.dest)}: “{pr.title_template}” — {collapse.describe(pr.rrule)},")
        w(f"      {len(pr.members)} closed copies, last due {last}; copies imported as history")
    w("")

    w(f"Twins — closed exact copies of a task in another list; skipped ({len(plan.twins)})")
    if not plan.twins:
        w("  none")
    else:
        pairs = Counter(
            (
                (t.get("list") or {}).get("name") or "?",
                (k.get("list") or {}).get("name") or "?",
            )
            for t, k in plan.twins
        )
        for (src, kept), n in pairs.most_common():
            w(f"  {n} tasks in “{src}” are copies of tasks in “{kept}”")
    w("")

    end = plan.window_end
    w(f"Dropped — recurrence copies due after {_day(end)} ({len(plan.dropped)})")
    if not plan.dropped:
        w("  none")
    for task, rule in plan.dropped:
        w(f"  {_task_line(task)}   → rule “{rule}”")
    w("")

    w(f"Flattened subtasks — deeper than four levels ({len(plan.flattened)})")
    if not plan.flattened:
        w("  none")
    for task, top in plan.flattened:
        w(f"  {_task_line(task)}   → now under “{top['name'].strip()}”")
    for task, why in plan.orphans:
        w(f"  {_task_line(task)}   → top-level task: {why}")
    w("")

    w(f"Unassigned — ClickUp assignee has no Slate account ({len(plan.unassigned)})")
    if not plan.unassigned:
        w("  none")
    for task, names in plan.unassigned:
        w(f"  {_task_line(task)}   (was: {', '.join(names)})")
    w("")

    w(f"Unmapped — ClickUp lists not imported ({len(plan.unmapped_lists)})")
    if not plan.unmapped_lists:
        w("  none")
    for entry in plan.unmapped_lists:
        where = entry["space"]["name"] + (
            f" / {entry['folder']['name']}" if entry["folder"] else ""
        )
        count = entry["list"].get("task_count")
        tail = f", {count} tasks" if count not in (None, "") else ""
        w(f"  “{entry['list']['name']}” in {where} (list {entry['list']['id']}{tail})")
    no_status = [pi for pi in plan.items if not pi.status]
    for pi in no_status:
        w(f"  task clickup:{pi.ref} has no status; it takes the list's default")
    w("")

    if plan.notes:
        w("Notes")
        for note in plan.notes:
            w(f"  {note}")
        w("")

    if result is not None:
        w("Applied")
        w(
            f"  sections created {result.sections_created}, lists created {result.lists_created},"
            f" statuses added {result.statuses_added}"
        )
        w(
            f"  items created {result.created}, updated {result.updated},"
            f" unchanged {result.unchanged}, archived {result.archived}"
        )
        w(f"  comments added {result.comments_added}, checklists added {result.checklists_added}")
        w(f"  rules created {result.rules_created}, already there {len(result.rules_existing)}")
        if result.owned_by_rule:
            w(
                f"  {len(result.owned_by_rule)} task(s) skipped: an existing rule already makes"
                " those dates"
            )
        w(f"  {result.events} events written")
    else:
        w("Nothing was written. Run again with --apply to import.")
    return "\n".join(out) + "\n"
