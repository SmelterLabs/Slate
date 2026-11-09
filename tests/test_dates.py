"""The one date convenience Slate provides: date-only means 17:00 America/Chicago."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slate.dates import parse_when
from slate.service.errors import Invalid


def test_date_only_in_cdt_lands_at_22z():
    assert parse_when("2026-10-23") == datetime(2026, 10, 23, 22, 0, tzinfo=UTC)


def test_date_only_in_cst_lands_at_23z():
    assert parse_when("2026-12-01") == datetime(2026, 12, 1, 23, 0, tzinfo=UTC)


def test_iso_with_offset_is_converted_to_utc():
    assert parse_when("2026-10-23T09:30:00-05:00") == datetime(2026, 10, 23, 14, 30, tzinfo=UTC)
    assert parse_when("2026-10-23T14:30:00Z") == datetime(2026, 10, 23, 14, 30, tzinfo=UTC)


def test_naive_iso_is_read_as_chicago_time():
    assert parse_when("2026-10-23T09:30") == datetime(2026, 10, 23, 14, 30, tzinfo=UTC)


def test_none_and_datetimes_pass_through():
    assert parse_when(None) is None
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_when(aware) == aware


@pytest.mark.parametrize("bad", ["", "friday", "2026-13-01", "23/10/2026"])
def test_garbage_is_invalid(bad):
    with pytest.raises(Invalid, match="ISO 8601"):
        parse_when(bad)
