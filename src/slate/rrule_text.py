"""One RRULE line in plain English, for people: "every month on the 2nd and 4th Wednesday".

Covers what the board's recurrence editor and agents write (DAILY/WEEKLY/MONTHLY/YEARLY with
INTERVAL, BYDAY with or without ordinals, BYSETPOS, BYMONTHDAY, BYMONTH, BYHOUR/BYMINUTE,
COUNT, UNTIL). Anything it does not recognise is left out of the sentence rather than guessed;
an unknown FREQ returns the rule text unchanged. Pure: no database, no dateutil.
"""

from __future__ import annotations

import re
from datetime import date

DAYS = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}
WEEKDAYS = {"MO", "TU", "WE", "TH", "FR"}
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
UNITS = {
    "DAILY": ("day", "days"),
    "WEEKLY": ("week", "weeks"),
    "MONTHLY": ("month", "months"),
    "YEARLY": ("year", "years"),
}
_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")


def ordinal(n: int) -> str:
    """1 -> 1st, 22 -> 22nd, -1 -> last, -2 -> 2nd to last."""
    if n == -1:
        return "last"
    if n < 0:
        return f"{ordinal(-n)} to last"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def join_words(words: list[str]) -> str:
    if len(words) < 2:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _ints(value: str | None) -> list[int]:
    out = []
    for part in (value or "").split(","):
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _parts(rrule: str) -> dict[str, str]:
    text = rrule.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    out = {}
    for piece in text.split(";"):
        key, _, value = piece.partition("=")
        if key:
            out[key.strip().upper()] = value.strip().upper()
    return out


def _days(byday: str) -> list[tuple[int | None, str]]:
    out = []
    for token in byday.split(","):
        m = _BYDAY.match(token.strip())
        if m:
            out.append((int(m.group(1)) if m.group(1) else None, m.group(2)))
    return out


def _day_list(codes: list[str]) -> str:
    if set(codes) == WEEKDAYS and len(codes) == 5:
        return "weekdays"
    if set(codes) == {"SA", "SU"} and len(codes) == 2:
        return "weekends"
    return join_words([DAYS[c] for c in codes])


def _nth_days(days: list[tuple[int | None, str]], setpos: list[int]) -> str | None:
    """The 'Nth weekday' phrase: "the 2nd and 4th Wednesday", "the last weekday", or None."""
    with_n = [(n, c) for n, c in days if n is not None]
    plain = [c for n, c in days if n is None]
    if with_n and not plain:
        codes = {c for _, c in with_n}
        if len(codes) == 1:  # 2WE,4WE -> the 2nd and 4th Wednesday
            ns = [n for n, _ in with_n]
            return f"the {join_words([ordinal(n) for n in ns])} {DAYS[with_n[0][1]]}"
        return "the " + join_words([f"{ordinal(n)} {DAYS[c]}" for n, c in with_n])
    if plain and setpos:  # BYDAY=WE;BYSETPOS=2,4 or BYDAY=MO..FR;BYSETPOS=-1
        if set(plain) == WEEKDAYS:
            what = "weekday"
        elif set(plain) == {"SA", "SU"}:
            what = "weekend day"
        elif len(plain) == 1:
            what = DAYS[plain[0]]
        else:
            what = f"of {join_words([DAYS[c] for c in plain])}"
        return f"the {join_words([ordinal(n) for n in setpos])} {what}"
    return None


def _month_days(values: list[int]) -> str:
    words = []
    for n in values:
        words.append("last day" if n == -1 else f"{ordinal(n)} day" if n < 0 else ordinal(n))
    return f"the {join_words(words)}"


def _time(parts: dict[str, str]) -> str:
    hours = _ints(parts.get("BYHOUR"))
    if not hours:
        return ""
    minute = (_ints(parts.get("BYMINUTE")) or [0])[0]
    words = []
    for h in hours:
        h12 = h % 12 or 12
        words.append(f"{h12}:{minute:02d} {'AM' if h < 12 else 'PM'}")
    return f" at {join_words(words)}"


def _until(value: str) -> str:
    digits = value[:8]
    try:
        d = date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return ""
    return f", until {d:%b} {d.day}, {d.year}"


def describe(rrule: str) -> str:
    """Plain-English sentence for one RRULE line. Unknown FREQ: the rule text unchanged."""
    parts = _parts(rrule)
    freq = parts.get("FREQ", "")
    if freq not in UNITS:
        return rrule
    one, many = UNITS[freq]
    n = (_ints(parts.get("INTERVAL")) or [1])[0]
    every = f"every {one}" if n <= 1 else f"every {n} {many}"
    days = _days(parts.get("BYDAY", ""))
    setpos = _ints(parts.get("BYSETPOS"))
    monthdays = _ints(parts.get("BYMONTHDAY"))
    months = [m for m in _ints(parts.get("BYMONTH")) if 1 <= m <= 12]
    month_words = join_words([MONTHS[m - 1] for m in months])

    text = every
    if freq in ("DAILY", "WEEKLY") and days and all(k is None for k, _ in days):
        codes = [c for _, c in days]
        label = _day_list(codes)
        if n <= 1 and label in ("weekdays", "weekends"):
            text = f"every {label[:-1]}"  # every weekday / every weekend
        else:
            text = f"{every} on {label}"
    elif freq == "MONTHLY":
        nth = _nth_days(days, setpos) if days else None
        if nth:
            text = f"{every} on {nth}"
        elif monthdays:
            text = f"{every} on {_month_days(monthdays)}"
        elif days:
            text = f"{every} on {_day_list([c for _, c in days])}"
    elif freq == "YEARLY":
        nth = _nth_days(days, setpos) if days else None
        if nth and month_words:
            text = f"{every} on {nth} of {month_words}"
        elif monthdays and month_words:
            if len(months) == 1 and len(monthdays) == 1 and monthdays[0] > 0:
                text = f"{every} on {month_words} {monthdays[0]}"
            else:
                text = f"{every} on {_month_days(monthdays)} of {month_words}"
        elif month_words:
            text = f"{every} in {month_words}"
        elif nth:
            text = f"{every} on {nth}"
    if months and freq in ("DAILY", "WEEKLY", "MONTHLY"):
        text += f" in {month_words}"

    text += _time(parts)
    count = _ints(parts.get("COUNT"))
    if count:
        text += ", once" if count[0] == 1 else f", {count[0]} times"
    if parts.get("UNTIL"):
        text += _until(parts["UNTIL"])
    return text
