"""Date input rules shared by REST and MCP.

`due_at` (and `start_at`) accept ISO 8601 or a date-only `YYYY-MM-DD`, which means 17:00
workspace time that day. A naive ISO datetime is read in the workspace timezone too
(`SLATE_TIMEZONE`, the display timezone), never as UTC. Everything is stored as aware UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from slate.service.errors import Invalid
from slate.settings import settings

LOCAL = ZoneInfo(settings.timezone)
DATE_ONLY_AT = time(17, 0)
# An agent's task with a date-only due date runs at 06:00 local time that morning, so "due
# today" means done by the time you look. A due date with a time runs at that time.
AGENT_RUN_AT = time(6, 0)
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# The last year a date may fall in: one year short of datetime's limit, so the day, week and
# reminder arithmetic done on a stored date never overflows.
MAX_YEAR = 9998


def parse_when(value: str | datetime | None) -> datetime | None:
    """ISO 8601 or date-only string -> aware UTC datetime. None passes through. Anything else,
    or a date outside years 1-9998, is `Invalid`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            if _DATE_ONLY.match(text):
                dt = datetime.combine(date.fromisoformat(text), DATE_ONLY_AT)
            else:
                dt = datetime.fromisoformat(text)
        except ValueError:
            raise _not_a_date(value) from None
    else:
        raise _not_a_date(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL)
    try:
        out = dt.astimezone(UTC)
    except OverflowError:
        out = None
    if out is None or out.year > MAX_YEAR:
        raise Invalid(f"'{value}' is out of range: dates run from year 1 to {MAX_YEAR}")
    return out


def _not_a_date(value: object) -> Invalid:
    return Invalid(
        f"'{value}' is not a date: use ISO 8601 (2026-10-23T17:00:00-05:00) "
        "or a date-only YYYY-MM-DD (means 17:00 workspace time)"
    )


def local_today(now: datetime | None = None) -> date:
    """The workspace-timezone calendar day containing `now` (default: now)."""
    return (now or datetime.now(UTC)).astimezone(LOCAL).date()


def local_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the workspace-timezone calendar day containing `now`, in UTC."""
    local = now.astimezone(LOCAL).date()
    start = datetime.combine(local, time(0), tzinfo=LOCAL)
    end = datetime.combine(local + timedelta(days=1), time(0), tzinfo=LOCAL)
    return start.astimezone(UTC), end.astimezone(UTC)


def is_date_only_input(value: object) -> bool:
    """Was this due value given without a time (a bare `YYYY-MM-DD`)? Sets `due_date_only`."""
    return isinstance(value, str) and bool(_DATE_ONLY.match(value.strip()))


def run_at(due_at: datetime, date_only: bool) -> datetime:
    """When an agent's task with this due date runs by itself: 06:00 workspace time on the due
    day for a date-only due date (`items.due_date_only`), else the due time itself."""
    if not date_only:
        return due_at
    day = due_at.astimezone(LOCAL).date()
    return datetime.combine(day, AGENT_RUN_AT, tzinfo=LOCAL).astimezone(UTC)


def early_date_only_due(now: datetime) -> datetime | None:
    """The one date-only due instant (17:00 workspace time) whose run time (06:00 the same day)
    has come although the due time has not: today's 17:00 between 06:00 and 17:00, else None.
    Lets SQL test "run time has come" as `due_at <= now OR (due_date_only AND due_at == this)`
    without a timezone function."""
    local = now.astimezone(LOCAL)
    if not AGENT_RUN_AT <= local.time() < DATE_ONLY_AT:
        return None
    return datetime.combine(local.date(), DATE_ONLY_AT, tzinfo=LOCAL).astimezone(UTC)
