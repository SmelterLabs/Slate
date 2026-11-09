"""Plain-English RRULE descriptions (slate.rrule_text), shown by the recurrence editor."""

from __future__ import annotations

import pytest

from slate.rrule_text import describe, ordinal


@pytest.mark.parametrize(
    ("rrule", "text"),
    [
        ("FREQ=DAILY", "every day"),
        ("FREQ=DAILY;INTERVAL=3", "every 3 days"),
        ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "every weekday"),
        ("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", "every weekday"),
        ("FREQ=WEEKLY;BYDAY=SA,SU", "every weekend"),
        ("FREQ=WEEKLY;BYDAY=MO,WE,FR", "every week on Monday, Wednesday and Friday"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=WE", "every 2 weeks on Wednesday"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TU,WE,TH,FR", "every 2 weeks on weekdays"),
        ("FREQ=MONTHLY;BYMONTHDAY=15", "every month on the 15th"),
        ("FREQ=MONTHLY;BYMONTHDAY=1,15", "every month on the 1st and 15th"),
        ("FREQ=MONTHLY;BYMONTHDAY=-1", "every month on the last day"),
        ("FREQ=MONTHLY;BYMONTHDAY=-2", "every month on the 2nd to last day"),
        ("FREQ=MONTHLY;BYDAY=2WE,4WE", "every month on the 2nd and 4th Wednesday"),
        ("FREQ=MONTHLY;BYDAY=WE;BYSETPOS=2,4", "every month on the 2nd and 4th Wednesday"),
        ("FREQ=MONTHLY;BYDAY=-1FR", "every month on the last Friday"),
        ("FREQ=MONTHLY;BYDAY=1MO,3TH", "every month on the 1st Monday and 3rd Thursday"),
        ("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1", "every month on the last weekday"),
        ("FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=2", "every 3 months on the 2nd"),
        ("FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=15", "every year on March 15"),
        ("FREQ=YEARLY;BYMONTH=5;BYDAY=2SU", "every year on the 2nd Sunday of May"),
        (
            "FREQ=YEARLY;BYMONTH=11;BYDAY=TH;BYSETPOS=4",
            "every year on the 4th Thursday of November",
        ),
        ("FREQ=YEARLY", "every year"),
        ("FREQ=DAILY;BYHOUR=9;BYMINUTE=30", "every day at 9:30 AM"),
        ("FREQ=WEEKLY;BYDAY=TU;BYHOUR=17", "every week on Tuesday at 5:00 PM"),
        ("FREQ=DAILY;COUNT=10", "every day, 10 times"),
        ("FREQ=WEEKLY;BYDAY=WE;UNTIL=20261231", "every week on Wednesday, until Dec 31, 2026"),
        (
            "FREQ=WEEKLY;BYDAY=WE;UNTIL=20261231T235959Z",
            "every week on Wednesday, until Dec 31, 2026",
        ),
        ("RRULE:freq=weekly;byday=we", "every week on Wednesday"),
        ("FREQ=HOURLY", "FREQ=HOURLY"),
    ],
)
def test_describe(rrule, text):
    assert describe(rrule) == text


def test_ordinals():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 31, -1, -2)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
        "31st",
        "last",
        "2nd to last",
    ]
