"""Recurring rules and the materializer that turns them into items.

A rule is an RFC 5545 RRULE plus a `dtstart` and an IANA `timezone`. Occurrences are computed
in that zone's wall time (so "17:00" stays 17:00 across DST) and stored as UTC instants.

`materialize(session, now)` spawns one item per occurrence that falls in
`[next_run_at, now + lead_days]`, then moves `next_run_at` (the rule's cursor: the first
occurrence not yet dealt with) past them. Each item carries `recurrence_id` + `occurrence_at`;
the `(recurrence_id, occurrence_at)` unique constraint plus a lookup make a double run or a
restart harmless. The three missed policies decide what happens while an instance is open:

- `spawn_always`: spawn regardless.
- `skip_if_open`: wait; if the occurrence's own due time arrives while an instance is still
  open, skip it (`recurrence.skipped`) and move on.
- `roll_forward`: wait; once the open instance is overdue, move its `occurrence_at`/`due_at`
  to the occurrence (`item.updated` on `due_at`, plus `recurrence.rolled_forward`).

A rule never back-fills occurrences from before the day it was created, resumed or re-anchored.

ClickUp's options (on every rule):

- `trigger`: `schedule` (above) or `on_done`: the scheduler only makes the first instance;
  after that, marking the current instance done makes the next one, dated from the day it was
  done (`on_item_done`, called from `lists.move_item_to_status`). "Every 3 days" then means
  three days after you last did it.
- `skip_weekends`: an occurrence on Saturday or Sunday moves to the Monday after (several that
  land on one Monday count once).
- `reopen` (`on_done` only): instead of a new item, the done one goes back to the list's
  default status with the next dates (`recurrence.reopened`), checklists unticked.
- `copy_checklists`: each new instance gets the previous instance's checklists, unticked.
- Before-due alerts always carry over from the previous instance.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import MAXYEAR, UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule, rrulestr
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.models import (
    Checklist,
    ChecklistItem,
    Item,
    MissedPolicy,
    Priority,
    Recurrence,
    RecurTrigger,
    Source,
    Status,
    StatusKind,
    utcnow,
)
from slate.rrule_text import describe
from slate.service import items as items_svc
from slate.service import lists as lists_svc
from slate.service import reminders as reminders_svc
from slate.service import tags as tags_svc
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Invalid, NotFound
from slate.settings import settings

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "UTC"
DUE_AT = time(17, 0)  # local due time when the rule carries no time of its own
MAX_LEAD_DAYS = 366
# A rule never spawns more than this in one tick (one transaction under the write lock); the
# rest follows next tick from the cursor.
MAX_SPAWN_PER_TICK = 100
MIN_DTSTART_YEAR = 1970
NEXT_SHOWN = 5
FREQS = {YEARLY: "YEARLY", MONTHLY: "MONTHLY", WEEKLY: "WEEKLY", DAILY: "DAILY"}

FIELDS = frozenset(
    {
        "list_id",
        "title_template",
        "body_template_md",
        "priority",
        "tags",
        "assignees",
        "rrule",
        "dtstart",
        "timezone",
        "lead_days",
        "missed_policy",
        "trigger",
        "skip_weekends",
        "reopen",
        "copy_checklists",
    }
)
# Changing any of these re-anchors the cursor.
_SHAPE = frozenset({"rrule", "dtstart", "timezone", "trigger", "skip_weekends"})
MAX_UPCOMING = 1000
MAX_UPCOMING_DAYS = 400
_COUNT = re.compile(r"(?:^|;)COUNT=\d+")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNTIL = re.compile(r"(?:^|;)UNTIL=([0-9TZ]+)")


@dataclass(frozen=True)
class Occurrence:
    at: datetime  # the occurrence instant, UTC
    due_at: datetime  # UTC
    local: datetime  # the occurrence in the rule's timezone (aware)
    date_only: bool = False  # the rule carries no time: due 17:00, flagged `due_date_only`


# --- Validation and the occurrence engine ---------------------------------------------------


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip())
    except Exception:  # ZoneInfoNotFoundError, ValueError for odd strings
        raise Invalid(f"'{name}' is not an IANA timezone (e.g. America/New_York)") from None


def _local_naive(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz).replace(tzinfo=None)


def _parse_until(value: str, tz: ZoneInfo) -> datetime:
    """UNTIL as local naive wall time: `...Z` is UTC; a bare date runs to the end of that day."""
    if value.endswith("Z"):
        return _local_naive(datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), tz)
    if "T" in value:
        return datetime.strptime(value, "%Y%m%dT%H%M%S")
    return datetime.combine(datetime.strptime(value, "%Y%m%d").date(), time(23, 59, 59))


def _build(text: str, dtstart_local: datetime, tz: ZoneInfo) -> rrule:
    """The dateutil rule generating naive local wall times from `dtstart_local`."""
    until = _UNTIL.search(text)
    body = _UNTIL.sub("", text).strip(";") if until else text
    rule = rrulestr(body, dtstart=dtstart_local)
    if until:
        rule = rule.replace(until=_parse_until(until.group(1), tz))
    return rule


# The Gregorian calendar repeats exactly every 400 years (146,097 days, a whole number of
# weeks), so a rule's pattern from a start moved by 400-year steps is the same pattern.
_CYCLE_YEARS = 400


@lru_cache(maxsize=1024)
def _occurs(text: str, dtstart_local: datetime) -> bool:
    """Whether the rule's pattern (COUNT and UNTIL aside) matches any day at all.

    dateutil walks a rule that matches nothing (February 30) one period at a time until year
    9999 before it gives up, which costs seconds. The check walks the same pattern from a start
    moved close to that end, so dateutil's own stop bounds the walk to at most two calendar
    cycles; a pattern that matches nothing in a whole cycle never matches."""
    years = (MAXYEAR - _CYCLE_YEARS - dtstart_local.year) // _CYCLE_YEARS * _CYCLE_YEARS
    start = dtstart_local.replace(year=dtstart_local.year + max(years, 0))
    body = _UNTIL.sub("", text).strip(";")
    rule = rrulestr(_COUNT.sub("", body).strip(";"), dtstart=start)
    return rule.after(start, inc=True) is not None


def _require_occurs(rule: Recurrence) -> None:
    if not _occurs(rule.rrule, _local_naive(rule.dtstart, ZoneInfo(rule.timezone))):
        raise Invalid(
            f"rrule '{rule.rrule}' never occurs (no day matches all its BY... parts, "
            "e.g. February 30)"
        )


def _clean_rrule(value: str | None) -> str:
    text = (value or "").strip().upper()
    if text.startswith("RRULE:"):
        text = text[len("RRULE:") :]
    if not text:
        raise Invalid("rrule is required, e.g. FREQ=WEEKLY;INTERVAL=2;BYDAY=WE")
    if "\n" in text or "\r" in text or ":" in text:
        raise Invalid("rrule is a single RRULE line (FREQ=...;...); set dtstart separately")
    if "FREQ=" not in text:
        raise Invalid("rrule needs a FREQ (DAILY, WEEKLY, MONTHLY or YEARLY)")
    try:
        rule = _build(text, datetime(2026, 1, 1), ZoneInfo(DEFAULT_TIMEZONE))
    except Exception as exc:
        raise Invalid(f"rrule '{value}' is not a valid RFC 5545 RRULE: {exc}") from None
    if not isinstance(rule, rrule) or rule._freq not in FREQS:
        raise Invalid("rrule FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY")
    if rule._interval < 1:
        # dateutil accepts INTERVAL=0 and then yields the same instant for ever: the
        # scheduler would loop inside the write lock and every write in Slate would hang.
        raise Invalid("rrule INTERVAL must be at least 1")
    if "BYMINUTE=" in text or "BYSECOND=" in text or (rule._byhour and len(rule._byhour) > 1):
        # Sub-daily is refused as a FREQ; these are the same thing through the back door
        # (BYHOUR=0..23;BYMINUTE=0..59 is 1,440 items a day, spawned in one transaction).
        raise Invalid("rrule may name one BYHOUR at most and no BYMINUTE or BYSECOND")
    return text


def _dtstart(value: str | datetime | None, tz: ZoneInfo, now: datetime) -> datetime:
    """ISO 8601 or YYYY-MM-DD (local midnight); naive means the rule's timezone. -> UTC."""
    if value is None:
        dt = datetime.combine(now.astimezone(tz).date(), time(0))
    elif isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        try:
            if _DATE_ONLY.match(text):
                dt = datetime.combine(date.fromisoformat(text), time(0))
            else:
                dt = datetime.fromisoformat(text)
        except ValueError:
            raise Invalid(f"dtstart '{value}' is not a date: use YYYY-MM-DD or ISO 8601") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    try:
        dt = dt.astimezone(UTC)
    except OverflowError:
        raise Invalid(f"dtstart '{value}' is out of range") from None
    if dt.year < MIN_DTSTART_YEAR:
        # Every walk of a rule starts at dtstart (dateutil iterates from there), so an
        # ancient start makes each preview, view and tick cost seconds.
        raise Invalid(f"dtstart must be {MIN_DTSTART_YEAR} or later")
    return dt


def _carries_time(rule: Recurrence, dtstart_local: datetime) -> bool:
    return "BYHOUR=" in rule.rrule or dtstart_local.time() != time(0)


def occurrences(rule: Recurrence, start: datetime) -> Iterator[Occurrence]:
    """The rule's occurrences with `at >= start`, in order. Infinite unless COUNT/UNTIL.

    With `skip_weekends`, a Saturday or Sunday occurrence moves to the following Monday (same
    wall time); occurrences that land on a day already taken are dropped."""
    tz = ZoneInfo(rule.timezone)
    local_start = _local_naive(rule.dtstart, tz)
    if not _occurs(rule.rrule, local_start):
        return  # nothing to walk; dateutil would search to year 9999 (see `_occurs`)
    timed = _carries_time(rule, local_start)
    gen = _build(rule.rrule, local_start, tz)
    skip = bool(rule.skip_weekends)
    last_day: date | None = None
    # A day early (three when weekends move to Monday), then filter in UTC: robust to the
    # repeated hour when clocks fall back.
    back = timedelta(days=3 if skip else 1)
    prev: datetime | None = None
    for naive in gen.xafter(_local_naive(start, tz) - back, inc=True):
        if prev is not None and naive <= prev:
            return  # a rule that stopped advancing (INTERVAL=0) would otherwise loop for ever
        prev = naive
        if skip:
            if naive.weekday() >= 5:
                naive += timedelta(days=7 - naive.weekday())
            if naive.date() == last_day:
                continue
            last_day = naive.date()
        local = naive.replace(tzinfo=tz)
        at = local.astimezone(UTC)
        if at < start:
            continue
        due = at if timed else datetime.combine(naive.date(), DUE_AT, tzinfo=tz).astimezone(UTC)
        yield Occurrence(at=at, due_at=due, local=local, date_only=not timed)


def render(template: str | None, local_day: date) -> str:
    """`{date}` -> "Wed Nov 11", `{iso_date}` -> "2026-11-11"; everything else literal."""
    if not template:
        return ""
    pretty = f"{local_day:%a} {local_day:%b} {local_day.day}"
    return template.replace("{iso_date}", local_day.isoformat()).replace("{date}", pretty)


def _title_template(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise Invalid("title_template is required")
    if len(render(text, date(2026, 9, 30))) > 500:
        raise Invalid("title_template renders longer than 500 characters")
    return text


def _priority(value: str | None) -> Priority | None:
    if value is None:
        return None
    try:
        return Priority(value)
    except ValueError:
        raise Invalid(f"priority must be one of {[p.value for p in Priority]} or null") from None


def _policy(value: str | None) -> MissedPolicy:
    try:
        return MissedPolicy(value or MissedPolicy.spawn_always)
    except ValueError:
        raise Invalid(f"missed_policy must be one of {[p.value for p in MissedPolicy]}") from None


def _trigger(value: str | None) -> RecurTrigger:
    try:
        return RecurTrigger(value or RecurTrigger.schedule)
    except ValueError:
        raise Invalid(f"trigger must be one of {[t.value for t in RecurTrigger]}") from None


def _flag(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise Invalid(f"{name} must be true or false")
    return value


def _lead_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise Invalid("lead_days must be a whole number of days") from None
    if not 0 <= days <= MAX_LEAD_DAYS:
        raise Invalid(f"lead_days must be between 0 and {MAX_LEAD_DAYS}")
    return days


def _clean(session: Session, fields: Mapping[str, Any], tz: ZoneInfo, now: datetime) -> dict:
    """Validate the given FIELDS into column values (only the keys present)."""
    unknown = set(fields) - FIELDS
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    if "list_id" in fields:
        out["list_id"] = lists_svc.get_list(session, fields["list_id"]).id
    if "title_template" in fields:
        out["title_template"] = _title_template(fields["title_template"])
    if "body_template_md" in fields:
        out["body_template_md"] = fields["body_template_md"] or None
    if "priority" in fields:
        out["priority"] = _priority(fields["priority"])
    if "tags" in fields:
        out["tags_json"] = sorted({tags_svc.normalize(n) for n in fields["tags"] or []})
    if "assignees" in fields:
        ids = [items_svc.resolve_account(session, r).id for r in fields["assignees"] or []]
        out["assignee_ids_json"] = sorted(set(ids))
    if "rrule" in fields:
        out["rrule"] = _clean_rrule(fields["rrule"])
    if "timezone" in fields:
        out["timezone"] = _zone(fields["timezone"]).key
    if "dtstart" in fields:
        out["dtstart"] = _dtstart(fields["dtstart"], tz, now)
    if "lead_days" in fields:
        out["lead_days"] = _lead_days(fields["lead_days"])
    if "missed_policy" in fields:
        out["missed_policy"] = _policy(fields["missed_policy"])
    if "trigger" in fields:
        out["trigger"] = _trigger(fields["trigger"])
    for flag in ("skip_weekends", "reopen", "copy_checklists"):
        if flag in fields:
            out[flag] = _flag(fields[flag], flag)
    return out


def _check_combination(trigger: RecurTrigger, reopen: bool) -> None:
    if reopen and trigger != RecurTrigger.on_done:
        raise Invalid("reopen needs trigger on_done: a task can only reopen once it is done")


def _anchor(session: Session, rule: Recurrence, now: datetime) -> datetime | None:
    """The cursor for a new, resumed or re-shaped rule: its first occurrence on or after today
    (in the rule's zone) and after the latest instance already spawned. None if exhausted."""
    tz = ZoneInfo(rule.timezone)
    floor = datetime.combine(now.astimezone(tz).date(), time(0), tzinfo=tz).astimezone(UTC)
    floor = max(floor, rule.dtstart)
    if rule.id is not None:
        latest = session.scalar(
            select(func.max(Item.occurrence_at)).where(Item.recurrence_id == rule.id)
        )
        if latest is not None:
            if rule.trigger == RecurTrigger.on_done:
                return None  # the next one comes when the current one is done
            floor = max(floor, latest + timedelta(microseconds=1))
    first = next(occurrences(rule, floor), None)
    return first.at if first else None


# --- Reads ---------------------------------------------------------------------------------


def get_recurrence(session: Session, rule_id: str) -> Recurrence:
    rule = session.get(Recurrence, rule_id)
    if rule is None:
        raise NotFound(f"recurrence {rule_id} not found")
    return rule


def list_recurrences(
    session: Session, *, list_id: str | None = None, include_ended: bool = False
) -> list[Recurrence]:
    q = select(Recurrence)
    if list_id is not None:
        q = q.where(Recurrence.list_id == list_id)
    if not include_ended:
        q = q.where(Recurrence.ended_at.is_(None))
    return list(session.scalars(q.order_by(Recurrence.title_template, Recurrence.id)))


def next_occurrences(
    rule: Recurrence, now: datetime | None = None, n: int = NEXT_SHOWN
) -> list[Occurrence]:
    """The next `n` occurrences whose due time is still ahead. Empty once the rule has ended."""
    if rule.ended_at is not None:
        return []
    now = now or utcnow()
    # Start a day back so an occurrence whose instant passed but whose due time has not is kept.
    out = []
    for occ in occurrences(rule, max(rule.dtstart, now - timedelta(days=1))):
        if occ.due_at < now:
            continue
        out.append(occ)
        if len(out) == n:
            break
    return out


def next_after_done(
    rule: Recurrence, done_at: datetime, after: datetime | None = None
) -> Occurrence | None:
    """For an `on_done` rule: the occurrence after a day's completion. The rule is re-anchored
    on the local day it was done (keeping its time of day), so "every 3 days" is three days
    after that day and "weekly on Monday" the next Monday. COUNT does not apply; UNTIL does.
    `after` (the finished instance's occurrence) keeps the next one later than it, so a task
    done early never makes a twin of itself."""
    tz = ZoneInfo(rule.timezone)
    day = done_at.astimezone(tz).date()
    clock = _local_naive(rule.dtstart, tz).time() if rule.dtstart else time(0)
    # Weekends move after picking the occurrence, not before: a rule re-anchored on a Saturday
    # has that Saturday as an occurrence, and moving it to Monday first would bring a task done
    # on Saturday back two days later instead of one interval later.
    shifted = Recurrence(
        rrule=_COUNT.sub("", rule.rrule).strip(";"),
        timezone=rule.timezone,
        dtstart=datetime.combine(day, clock, tzinfo=tz).astimezone(UTC),
        skip_weekends=False,
    )
    start = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    for occ in occurrences(shifted, start):
        if rule.skip_weekends:
            occ = _to_weekday(occ, tz)
        if after is None or occ.at > after:
            return occ
    return None


def _to_weekday(occ: Occurrence, tz: ZoneInfo) -> Occurrence:
    """A Saturday or Sunday occurrence moved to the following Monday, same wall time."""
    naive = occ.local.replace(tzinfo=None)
    if naive.weekday() < 5:
        return occ
    naive += timedelta(days=7 - naive.weekday())
    local = naive.replace(tzinfo=tz)
    at = local.astimezone(UTC)
    timed = not occ.date_only
    due = at if timed else datetime.combine(naive.date(), DUE_AT, tzinfo=tz).astimezone(UTC)
    return Occurrence(at=at, due_at=due, local=local, date_only=occ.date_only)


def _done_chain(rule: Recurrence, now: datetime, n: int) -> list[Occurrence]:
    """An `on_done` rule's dates if each one is done on its due day, starting today."""
    out: list[Occurrence] = []
    at = now
    while len(out) < n:
        occ = next_after_done(rule, at)
        if occ is None:
            break
        out.append(occ)
        at = occ.due_at
    return out


def describe_rule(rule: Recurrence) -> str:
    """`describe()` plus the options, e.g. "every 3 days, counted from when each is done"."""
    words = describe(rule.rrule)
    if rule.trigger == RecurTrigger.on_done:
        words += ", counted from when each is done"
    if rule.skip_weekends:
        words += ", weekends moved to Monday"
    return words


def preview(
    rrule: str,
    *,
    dtstart: str | datetime | None = None,
    timezone: str | None = None,
    count: int = NEXT_SHOWN,
    trigger: str | None = None,
    skip_weekends: bool = False,
    now: datetime | None = None,
) -> dict:
    """What a rule would do, without saving it: the cleaned rule, a plain-English description,
    and the next `count` occurrences (same shape as `next_occurrences` in `recurrence_view`).
    For `trigger=on_done` the dates assume each one is done on its due day, starting today."""
    now = now or utcnow()
    tz = _zone(timezone or settings.timezone)
    text = _clean_rrule(rrule)
    rule = Recurrence(
        rrule=text,
        timezone=tz.key,
        dtstart=_dtstart(dtstart, tz, now),
        trigger=_trigger(trigger),
        skip_weekends=bool(skip_weekends),
    )
    _require_occurs(rule)
    occs = (
        _done_chain(rule, now, count)
        if rule.trigger == RecurTrigger.on_done
        else next_occurrences(rule, now, count)
    )
    return {
        "rrule": text,
        "description": describe_rule(rule),
        "timezone": tz.key,
        "dtstart": rule.dtstart,
        "occurrences": [_occurrence_out(o) for o in occs],
    }


def _occurrence_out(o: Occurrence) -> dict:
    return {"local": o.local.isoformat(), "utc": _iso_utc(o.at), "due_at": _iso_utc(o.due_at)}


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def recurrence_view(session: Session, rule: Recurrence, now: datetime | None = None) -> dict:
    """The rule as a plain dict, with `next_occurrences` for the board's badge (empty for an
    `on_done` rule: its next date depends on when the current one is done)."""
    on_done = rule.trigger == RecurTrigger.on_done
    return {
        "id": rule.id,
        "list_id": rule.list_id,
        "title_template": rule.title_template,
        "body_template_md": rule.body_template_md,
        "priority": rule.priority.value if rule.priority else None,
        "tags": list(rule.tags_json or []),
        "assignee_ids": list(rule.assignee_ids_json or []),
        "rrule": rule.rrule,
        "description": describe_rule(rule),
        "dtstart": rule.dtstart,
        "timezone": rule.timezone,
        "lead_days": rule.lead_days,
        "missed_policy": rule.missed_policy.value,
        "next_run_at": rule.next_run_at,
        "created_by": rule.created_by,
        "paused_at": rule.paused_at,
        "ended_at": rule.ended_at,
        "trigger": rule.trigger.value,
        "skip_weekends": rule.skip_weekends,
        "reopen": rule.reopen,
        "copy_checklists": rule.copy_checklists,
        "next_occurrences": []
        if on_done
        else [_occurrence_out(o) for o in next_occurrences(rule, now)],
    }


def upcoming(
    session: Session,
    *,
    start: date,
    end: date,
    list_id: str | None = None,
) -> list[dict]:
    """Occurrences active `schedule` rules will make between `start` and `end` (local days,
    inclusive) that are not items yet: what the calendar and timeline draw as "coming".
    Ordered by due time; at most MAX_UPCOMING."""
    if end < start:
        raise Invalid("end is before start")
    if (end - start).days > MAX_UPCOMING_DAYS:
        raise Invalid(f"ask for at most {MAX_UPCOMING_DAYS} days at a time")
    q = select(Recurrence).where(
        Recurrence.paused_at.is_(None),
        Recurrence.ended_at.is_(None),
        Recurrence.trigger == RecurTrigger.schedule,
        Recurrence.next_run_at.is_not(None),
    )
    if list_id is not None:
        q = q.where(Recurrence.list_id == list_id)
    out: list[dict] = []
    for rule in session.scalars(q):
        tz = ZoneInfo(rule.timezone)
        lo = datetime.combine(start, time(0), tzinfo=tz).astimezone(UTC)
        hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
        for n, occ in enumerate(occurrences(rule, max(lo, rule.next_run_at))):
            if occ.at >= hi or n >= MAX_UPCOMING:
                break
            day = occ.local.date()
            out.append(
                {
                    "recurrence_id": rule.id,
                    "list_id": rule.list_id,
                    "title": render(rule.title_template, day),
                    "occurrence_at": _iso_utc(occ.at),
                    "due_at": _iso_utc(occ.due_at),
                    "local_date": day.isoformat(),
                    "priority": rule.priority.value if rule.priority else None,
                    "tags": list(rule.tags_json or []),
                    "assignee_ids": list(rule.assignee_ids_json or []),
                }
            )
    out.sort(key=lambda o: (o["due_at"], o["title"]))
    return out[:MAX_UPCOMING]


# --- Writes --------------------------------------------------------------------------------


def create_recurrence(
    session: Session,
    actor: Actor,
    *,
    list_id: str,
    title_template: str,
    rrule: str,
    dtstart: str | datetime | None = None,
    timezone: str | None = None,
    adopt_item_id: str | None = None,
    now: datetime | None = None,
    **fields: Any,
) -> Recurrence:
    """Create a rule. Nothing is spawned until the next scheduler tick (or `run_now`).

    `dtstart` is ISO 8601 or YYYY-MM-DD (midnight in `timezone`; default today). `tags` are
    names, `assignees` handles or ids (stored as ids). Optional: `body_template_md`,
    `priority`, `lead_days` (default 14), `missed_policy` (default spawn_always), `trigger`
    (schedule | on_done), `skip_weekends`, `reopen` (on_done only), `copy_checklists`.

    `adopt_item_id` makes an existing item the rule's current occurrence in the same change
    (see `adopt_instance`); `dtstart` then defaults to that item's due day.
    """
    now = now or utcnow()
    timezone = settings.timezone if timezone is None else timezone
    tz = _zone(timezone)
    if adopt_item_id is not None and dtstart is None:
        adopted = items_svc.get_item(session, adopt_item_id)
        if adopted.due_at is not None:
            dtstart = adopted.due_at.astimezone(tz).date().isoformat()
    values = _clean(
        session,
        {
            **fields,
            "list_id": list_id,
            "title_template": title_template,
            "rrule": rrule,
            "timezone": timezone,
            "dtstart": dtstart,
        },
        tz,
        now,
    )
    rule = Recurrence(
        lead_days=14,
        missed_policy=MissedPolicy.spawn_always,
        trigger=RecurTrigger.schedule,
        skip_weekends=False,
        reopen=False,
        copy_checklists=True,
        created_by=actor.account_id,
    )
    for key, value in values.items():
        setattr(rule, key, value)
    _check_combination(rule.trigger, rule.reopen)
    _require_occurs(rule)
    rule.next_run_at = _anchor(session, rule, now)
    with apply_change(session, actor) as cx:
        cx.create(rule, "recurrence.created")
        if adopt_item_id is not None:
            adopt_instance(session, actor, rule.id, adopt_item_id, now=now)
    return rule


def adopt_instance(
    session: Session, actor: Actor, rule_id: str, item_id: str, *, now: datetime | None = None
) -> Item:
    """Make an existing item the rule's current occurrence (it gets the ↻ and counts as the
    open instance for `skip_if_open`/`roll_forward`); the rule spawns from the next one on.

    A dated item keeps its due date: its `occurrence_at` is the rule's occurrence on that local
    day, or the due instant itself when the rule has none that day. An undated item takes the
    rule's next occurrence (due date included). Adopting an item twice is no change; an item of
    another rule, or an ended rule, is refused.
    """
    now = now or utcnow()
    rule = get_recurrence(session, rule_id)
    item = items_svc.get_item(session, item_id)
    if rule.ended_at is not None:
        raise Invalid("this rule has ended; it cannot take on items")
    if item.recurrence_id == rule.id:
        return item
    if item.recurrence_id is not None:
        raise Invalid(f"item {item.id} already belongs to recurrence {item.recurrence_id}")
    tz = ZoneInfo(rule.timezone)
    due, date_only = item.due_at, item.due_date_only
    if due is not None:
        day = due.astimezone(tz).date()
        start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
        occ = next(occurrences(rule, max(start, rule.dtstart)), None)
        at = occ.at if occ and occ.local.date() == day else due
    else:
        upcoming = next_occurrences(rule, now, 1)
        if not upcoming:
            raise Invalid("this rule has no occurrences left to give the item")
        at, due, date_only = upcoming[0].at, upcoming[0].due_at, upcoming[0].date_only
    taken = session.scalar(
        select(Item.id).where(Item.recurrence_id == rule.id, Item.occurrence_at == at)
    )
    if taken is not None:
        raise Conflict(f"item {taken} is already this rule's occurrence on that day")
    with apply_change(session, actor) as cx:
        cx.set(item, "recurrence_id", rule.id, "item.updated")
        cx.set(item, "occurrence_at", at, "item.updated")
        cx.set(item, "due_at", due, "item.updated")
        cx.set(item, "due_date_only", date_only, "item.updated")
        if rule.paused_at is None:
            cx.set(rule, "next_run_at", _anchor(session, rule, now), "recurrence.advanced")
    return item


def update_recurrence(
    session: Session,
    actor: Actor,
    rule_id: str,
    changes: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> Recurrence:
    """Change any subset of FIELDS (one `recurrence.updated` event per changed field).

    Changing `rrule`, `dtstart` or `timezone` re-anchors the cursor at the first new occurrence
    after the latest spawned instance (and not before today). Spawned instances stay as they are.
    """
    now = now or utcnow()
    rule = get_recurrence(session, rule_id)
    tz = _zone(changes.get("timezone", rule.timezone))
    values = _clean(session, changes, tz, now)
    _check_combination(values.get("trigger", rule.trigger), values.get("reopen", rule.reopen))
    # Walk the new shape before taking the write lock: every other writer waits on it.
    reshaped = {k for k in values.keys() & _SHAPE if values[k] != getattr(rule, k)}
    anchor = None
    if reshaped:
        shaped = Recurrence(
            id=rule.id, **{k: values.get(k, getattr(rule, k)) for k in sorted(_SHAPE)}
        )
        _require_occurs(shaped)
        if rule.ended_at is None:
            anchor = _anchor(session, shaped, now)
    with apply_change(session, actor) as cx:
        changed = {k for k, v in values.items() if cx.set(rule, k, v, "recurrence.updated")}
        if changed & _SHAPE and rule.ended_at is None:
            rule.next_run_at = anchor if reshaped else _anchor(session, rule, now)
    return rule


def pause_recurrence(
    session: Session, actor: Actor, rule_id: str, *, now: datetime | None = None
) -> Recurrence:
    """Stop spawning until resumed. Already paused: no change."""
    rule = get_recurrence(session, rule_id)
    if rule.ended_at is not None:
        raise Invalid("this rule has ended; it cannot be paused")
    if rule.paused_at is None:
        with apply_change(session, actor) as cx:
            cx.set(rule, "paused_at", now or utcnow(), "recurrence.paused")
    return rule


def resume_recurrence(
    session: Session, actor: Actor, rule_id: str, *, now: datetime | None = None
) -> Recurrence:
    """Start spawning again from today; occurrences that fell inside the pause are not spawned."""
    now = now or utcnow()
    rule = get_recurrence(session, rule_id)
    if rule.ended_at is not None:
        raise Invalid("this rule has ended; create a new rule instead")
    if rule.paused_at is not None:
        with apply_change(session, actor) as cx:
            cx.set(rule, "paused_at", None, "recurrence.resumed")
            rule.next_run_at = _anchor(session, rule, now)
    return rule


def end_recurrence(
    session: Session, actor: Actor, rule_id: str, *, now: datetime | None = None
) -> Recurrence:
    """End the rule for good. Spawned instances stay. Already ended: no change."""
    rule = get_recurrence(session, rule_id)
    if rule.ended_at is None:
        with apply_change(session, actor) as cx:
            cx.set(rule, "ended_at", now or utcnow(), "recurrence.ended")
            rule.next_run_at = None
    return rule


def run_now(
    session: Session, actor: Actor, rule_id: str, *, now: datetime | None = None
) -> list[Item]:
    """Materialize this rule immediately (same window and policy as the scheduler), as `actor`."""
    rule = get_recurrence(session, rule_id)
    if rule.ended_at is not None:
        raise Invalid("this rule has ended")
    if rule.paused_at is not None:
        raise Invalid("this rule is paused; resume it first")
    session.rollback()  # end the read; the rule is re-read under the write lock
    return _materialize_rule(session, actor, rule_id, now or utcnow())


# --- Materialization -----------------------------------------------------------------------


def _open_instance(session: Session, rule: Recurrence) -> Item | None:
    """The rule's most recent instance that is not done, cancelled or archived."""
    return session.scalar(
        select(Item)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            Item.recurrence_id == rule.id,
            Item.archived_at.is_(None),
            or_(Status.kind.is_(None), Status.kind.not_in(lists_svc.CLOSED_KINDS)),
        )
        .order_by(Item.occurrence_at.desc())
        .limit(1)
    )


def _copy_checklists(cx: Change, source: Item, target: Item) -> None:
    """The source's checklists onto the target, every entry unticked (one event each)."""
    session = cx.session
    lists = session.scalars(
        select(Checklist).where(Checklist.item_id == source.id).order_by(Checklist.position)
    ).all()
    for cl in lists:
        new = cx.create(
            Checklist(item_id=target.id, name=cl.name, position=cl.position), "checklist.created"
        )
        entries = session.scalars(
            select(ChecklistItem)
            .where(ChecklistItem.checklist_id == cl.id)
            .order_by(ChecklistItem.position)
        ).all()
        for e in entries:
            cx.create(
                ChecklistItem(
                    checklist_id=new.id,
                    item_id=target.id,
                    title=e.title,
                    assignee_id=e.assignee_id,
                    position=e.position,
                ),
                "checklist.entry_added",
            )


def _previous_instance(session: Session, rule: Recurrence, before: datetime) -> Item | None:
    return session.scalar(
        select(Item)
        .where(
            Item.recurrence_id == rule.id,
            Item.occurrence_at < before,
            Item.trashed_at.is_(None),
        )
        .order_by(Item.occurrence_at.desc())
        .limit(1)
    )


def _spawn(cx: Change, rule: Recurrence, occ: Occurrence, source: Item | None = None) -> Item:
    if source is None:
        source = _previous_instance(cx.session, rule, occ.at)
    day = occ.local.date()
    fields = {
        "title": render(rule.title_template, day),
        "body_md": render(rule.body_template_md, day),
        "priority": rule.priority.value if rule.priority else None,
        "due_at": occ.due_at,
        "due_date_only": occ.date_only,
        "assignees": list(rule.assignee_ids_json or []),
        "tags": list(rule.tags_json or []),
    }
    item = items_svc._new_item(
        cx.session, cx.actor, list_id=rule.list_id, parent=None, fields=fields
    )
    item.recurrence_id = rule.id
    item.occurrence_at = occ.at
    items_svc._insert(cx, item, fields)
    cx.record(
        "recurrence.spawned",
        item_id=item.id,
        subject_id=rule.id,
        after={"occurrence_at": occ.at, "due_at": occ.due_at},
        covers=[rule],
    )
    if rule.copy_checklists and source is not None:
        _copy_checklists(cx, source, item)
    if source is not None:
        reminders_svc.copy_due_alerts(cx, source, item)
    return item


def _roll(cx: Change, rule: Recurrence, item: Item, occ: Occurrence) -> None:
    before = {"occurrence_at": item.occurrence_at, "due_at": item.due_at}
    cx.set(item, "due_at", occ.due_at, "item.updated")
    cx.set(item, "due_date_only", occ.date_only, "item.updated")
    cx.set(item, "occurrence_at", occ.at, "item.updated")
    cx.record(
        "recurrence.rolled_forward",
        item_id=item.id,
        subject_id=rule.id,
        before=before,
        after={"occurrence_at": occ.at, "due_at": occ.due_at},
        covers=[rule],
    )


def _materialize_rule(session: Session, actor: Actor, rule_id: str, now: datetime) -> list[Item]:
    spawned: list[Item] = []
    with apply_change(session, actor) as cx:
        rule = session.get(Recurrence, rule_id)
        if rule is None or rule.paused_at or rule.ended_at or rule.next_run_at is None:
            return spawned
        # Local days, not 24-hour blocks: on the night clocks fall back a 48 h window ended an
        # hour before the second midnight and that day's instance waited a day.
        tz = ZoneInfo(rule.timezone)
        horizon = (now.astimezone(tz) + timedelta(days=rule.lead_days)).astimezone(UTC)
        cursor: datetime | None = None
        recorded = len(cx.events)
        if rule.trigger == RecurTrigger.on_done:
            # Only the first instance comes from the schedule; done ones make the rest.
            have = session.scalar(select(Item.id).where(Item.recurrence_id == rule.id).limit(1))
            if have is None:
                first = next(occurrences(rule, rule.next_run_at), None)
                if first is None or first.at > horizon:
                    return spawned
                spawned.append(_spawn(cx, rule, first))
                rule.next_run_at = None  # covered by recurrence.spawned
            else:
                cx.set(rule, "next_run_at", None, "recurrence.advanced")
            return spawned
        for occ in occurrences(rule, rule.next_run_at):
            if occ.at > horizon or len(spawned) >= MAX_SPAWN_PER_TICK:
                cursor = occ.at
                break
            taken = session.scalar(
                select(Item.id).where(Item.recurrence_id == rule.id, Item.occurrence_at == occ.at)
            )
            if taken:
                continue
            open_ = None
            if rule.missed_policy != MissedPolicy.spawn_always:
                open_ = _open_instance(session, rule)
            if open_ is None:
                spawned.append(_spawn(cx, rule, occ))
            elif rule.missed_policy == MissedPolicy.skip_if_open and occ.due_at <= now:
                cx.record(
                    "recurrence.skipped",
                    subject_id=rule.id,
                    after={"occurrence_at": occ.at, "open_item_id": open_.id},
                    covers=[rule],
                )
            elif (
                rule.missed_policy == MissedPolicy.roll_forward
                and open_.due_at is not None
                and open_.due_at <= now
            ):
                _roll(cx, rule, open_, occ)
            else:  # an open instance that is not missed yet: decide on a later tick
                cursor = occ.at
                break
        if cursor != rule.next_run_at:
            if len(cx.events) > recorded:
                rule.next_run_at = cursor  # covered by the events above
            else:
                cx.set(rule, "next_run_at", cursor, "recurrence.advanced")
    return spawned


def materialize(session: Session, now: datetime | None = None) -> list[Item]:
    """Spawn every active rule's due instances up to `now + lead_days`. Safe to run any time.

    Each rule is its own short transaction, acted by the rule's creator with source `system`
    and `session_ref` `recurrence:<id>`. A rule that fails is logged and skipped; the others
    still run, and it is retried on the next tick.
    """
    now = now or utcnow()
    rows = session.execute(
        select(Recurrence.id, Recurrence.created_by)
        .where(
            Recurrence.paused_at.is_(None),
            Recurrence.ended_at.is_(None),
            Recurrence.next_run_at.is_not(None),
        )
        .order_by(Recurrence.next_run_at)
    ).all()
    session.rollback()  # end the read; each rule is re-read under the write lock
    spawned: list[Item] = []
    for rule_id, creator in rows:
        actor = Actor(creator, Source.system, session_ref=f"recurrence:{rule_id}")
        try:
            spawned += _materialize_rule(session, actor, rule_id, now)
        except Exception:
            log.exception("recurrence %s failed to materialize; retrying next tick", rule_id)
    return spawned


# --- Recur when done -------------------------------------------------------------------------


def _reopen(cx: Change, rule: Recurrence, item: Item, occ: Occurrence) -> None:
    """The done item comes back as the next occurrence: default status, next dates (a start
    date keeps its distance to the due date), checklists unticked."""
    session = cx.session
    before = {"occurrence_at": item.occurrence_at, "due_at": item.due_at}
    lst = lists_svc.get_list(session, item.list_id)
    status = session.get(Status, lst.default_status_id) if lst.default_status_id else None
    if status is None or status.kind in lists_svc.CLOSED_KINDS:
        status = next(
            (s for s in lists_svc.statuses_of(session, lst.id) if s.kind == StatusKind.open),
            None,
        )
    if item.start_at is not None and item.due_at is not None:
        cx.set(item, "start_at", occ.due_at - (item.due_at - item.start_at), "item.updated")
    cx.set(item, "due_at", occ.due_at, "item.updated")
    cx.set(item, "due_date_only", occ.date_only, "item.updated")
    cx.set(item, "occurrence_at", occ.at, "item.updated")
    if status is not None:
        lists_svc.move_item_to_status(cx, item, status)
    for entry in session.scalars(
        select(ChecklistItem).where(
            ChecklistItem.item_id == item.id, ChecklistItem.done_at.is_not(None)
        )
    ):
        entry.done_at = None
        cx.record(
            "checklist.entry_unchecked",
            item_id=item.id,
            subject_id=entry.id,
            field="done",
            before=True,
            after={"title": entry.title, "done": False},
            covers=[entry],
        )
    cx.record(
        "recurrence.reopened",
        item_id=item.id,
        subject_id=rule.id,
        before=before,
        after={"occurrence_at": occ.at, "due_at": occ.due_at},
        covers=[rule],
    )


def on_item_done(cx: Change, item: Item) -> Item | None:
    """Called in the change that marks `item` done. For an active `on_done` rule whose latest
    instance this is, make the next one (or reopen this one); otherwise nothing. Returns the
    item that now holds the next occurrence, if any. A rule with no date left ends."""
    session = cx.session
    rule = session.get(Recurrence, item.recurrence_id) if item.recurrence_id else None
    if rule is None or rule.trigger != RecurTrigger.on_done:
        return None
    if rule.paused_at is not None or rule.ended_at is not None:
        return None
    latest = session.scalar(
        select(func.max(Item.occurrence_at)).where(Item.recurrence_id == rule.id)
    )
    if item.occurrence_at is None or item.occurrence_at != latest:
        return None  # an older instance: the newest one is the rule's current one
    occ = next_after_done(rule, item.completed_at or utcnow(), item.occurrence_at)
    if occ is None:
        cx.set(rule, "ended_at", utcnow(), "recurrence.ended")
        return None
    taken = session.scalar(
        select(Item.id).where(Item.recurrence_id == rule.id, Item.occurrence_at == occ.at)
    )
    if taken is not None:
        return None
    if rule.reopen:
        _reopen(cx, rule, item, occ)
        return item
    return _spawn(cx, rule, occ, source=item)
