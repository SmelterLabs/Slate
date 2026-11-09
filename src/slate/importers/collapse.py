"""Recurrence collapse: spot "the same chore copied out on a schedule" and name its rule.

A chore that repeats is often filed in ClickUp as dated copies ("Bulk trash pickup — Nov 11",
"— Nov 25", ...). `family_key()` normalises a title so the copies group;
`infer_rrule()` reads the gaps between their due dates and returns an RRULE only when that rule
regenerates every member date exactly. Anything else is irregular and stays separate tasks.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from dateutil.rrule import rrulestr

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_WEEKDAY = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?"
_DATES = [
    # "Wed Nov 11", "Nov 11", "November 11th, 2026"
    re.compile(
        rf"\b(?:{_WEEKDAY},?\s+)?{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?\b", re.I
    ),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # 2026-11-11
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),  # 11/11, 11/11/26
]
_MARKER = re.compile(
    r"\(\s*(?:bi-?weekly|weekly|monthly|quarterly|yearly|annual|daily)?\s*(?:recurring)?\s*\)",
    re.I,
)
_TRAILING = " \t—–-:|,;/"

WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def strip_dates(title: str) -> tuple[str, bool]:
    """The title without date fragments and recurrence markers; and whether a date was found."""
    text, dated = title, False
    for pattern in _DATES:
        text, n = pattern.subn(" ", text)
        dated = dated or n > 0
    text = _MARKER.sub(" ", text)
    text = " ".join(text.split()).strip(_TRAILING)
    return text, dated


def family_key(title: str) -> str:
    """Grouping key: dates, markers, numbers and punctuation removed, lowercase.

    `{date}`/`{iso_date}` placeholders are dropped too, so a rule's title template has the
    same key as the tasks it replaced (that is how a re-run finds an existing rule).
    """
    text = title.replace("{iso_date}", " ").replace("{date}", " ")
    text, _ = strip_dates(text)
    text = re.sub(r"\d+", " ", text.lower())
    text = re.sub(r"[^a-z\[\]]+", " ", text)
    return " ".join(text.split())


def title_template(titles: Sequence[str]) -> str:
    """The rule's title: the commonest date-stripped title, plus ` — {date}` if they were dated."""
    bases = [strip_dates(t) for t in titles]
    base = Counter(b for b, _ in bases).most_common(1)[0][0]
    return f"{base} — {{date}}" if any(d for _, d in bases) else base


@dataclass(frozen=True)
class Inference:
    rrule: str | None  # None: irregular
    gaps: tuple[int, ...]  # days between consecutive sorted dates


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _candidate(days: list[date], gaps: list[int]) -> str | None:
    first = days[0]
    weekday = WEEKDAYS[first.weekday()]
    dom = Counter(d.day for d in days).most_common(1)[0][0]
    if all(g == 7 for g in gaps):
        return f"FREQ=WEEKLY;BYDAY={weekday}"
    if all(g == 14 for g in gaps):
        return f"FREQ=WEEKLY;INTERVAL=2;BYDAY={weekday}"
    if all(28 <= g <= 31 for g in gaps):
        return f"FREQ=MONTHLY;BYMONTHDAY={dom}"
    if all(89 <= g <= 92 for g in gaps):
        return f"FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY={dom}"
    if all(181 <= g <= 184 for g in gaps):
        return f"FREQ=MONTHLY;INTERVAL=6;BYMONTHDAY={dom}"
    if all(365 <= g <= 366 for g in gaps):
        return f"FREQ=YEARLY;BYMONTH={first.month};BYMONTHDAY={first.day}"
    # "2nd and 4th Wednesday" style: same weekday, every date is the nth of its month and the
    # set of n values is small. Gaps of 14 and 21 days alternate, so the interval rules miss it.
    if len(days) >= 4 and len({d.weekday() for d in days}) == 1:
        positions = sorted({((d.day - 1) // 7) + 1 for d in days})
        months = {(d.year, d.month) for d in days}
        if len(positions) <= 2 and len(months) >= 2 and all(g in (14, 21, 28) for g in gaps):
            return f"FREQ=MONTHLY;BYDAY={weekday};BYSETPOS={','.join(map(str, positions))}"
    return None


def infer_rrule(days: Sequence[date]) -> Inference:
    """An RRULE for these due dates, or None if they are not one clean schedule.

    The rule must regenerate exactly the given dates between the first and the last, so a
    monthly family that drifts (the 5th, then the 7th) or a weekly one with a hole stays
    irregular instead of being silently "fixed".
    """
    ordered = sorted(days)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:], strict=False)]
    if len(ordered) < 2:
        return Inference(None, tuple(gaps))
    rule = _candidate(ordered, gaps)
    if rule is not None:
        start = datetime.combine(ordered[0], datetime.min.time())
        end = datetime.combine(ordered[-1], datetime.min.time())
        made = [d.date() for d in rrulestr(rule, dtstart=start).between(start, end, inc=True)]
        if made != ordered:
            rule = None
    return Inference(rule, tuple(gaps))


def next_after(rule: str, anchor: date, after: date) -> date:
    """The first date the rule (anchored on `anchor`) produces strictly after `after`."""
    start = datetime.combine(anchor, datetime.min.time())
    nxt = rrulestr(rule, dtstart=start).after(datetime.combine(after, datetime.max.time()))
    return nxt.date()


def describe(rule: str) -> str:
    """Plain words: `FREQ=WEEKLY;INTERVAL=2;BYDAY=WE` -> "every 2 weeks on Wednesday"."""
    parts = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq, n = parts.get("FREQ"), int(parts.get("INTERVAL", "1"))
    every = {"WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year", "DAILY": "day"}[freq]
    head = f"every {every}" if n == 1 else f"every {n} {every}s"
    if "BYDAY" in parts:
        names = [DAY_NAMES[WEEKDAYS.index(d)] for d in parts["BYDAY"].split(",")]
        if "BYSETPOS" in parts:
            nths = [_ordinal(int(p)) for p in parts["BYSETPOS"].split(",")]
            return f"{head} on the {' and '.join(nths)} {', '.join(names)}"
        return f"{head} on {', '.join(names)}"
    if freq == "YEARLY" and "BYMONTH" in parts:
        month = date(2000, int(parts["BYMONTH"]), 1).strftime("%b")
        return f"{head} on {month} {int(parts['BYMONTHDAY'])}"
    if "BYMONTHDAY" in parts:
        return f"{head} on the {_ordinal(int(parts['BYMONTHDAY']))}"
    return head
