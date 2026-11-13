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


@pytest.mark.parametrize(
    "value", ["9999-12-31T23:00:00", "9999-12-31", "0001-01-01T00:00:00+05:00", 12345, ["x"]]
)
def test_out_of_range_and_non_string_dates_are_invalid_not_a_crash(value):
    with pytest.raises(Invalid):
        parse_when(value)


def test_an_out_of_range_due_date_is_a_422_on_every_door(client, alex, todo):
    item = client.post(
        "/api/v1/items", json={"list_id": todo["id"], "title": "t"}, headers=alex
    ).json()
    for due in ("9999-12-31T23:00:00", "0001-01-01T00:00:00+05:00"):
        made = client.post(
            "/api/v1/items", json={"list_id": todo["id"], "title": "t", "due_at": due}, headers=alex
        )
        assert made.status_code == 422, made.text
        patched = client.patch(f"/api/v1/items/{item['id']}", json={"due_at": due}, headers=alex)
        assert patched.status_code == 422, patched.text
        bulk = client.post(
            "/api/v1/items/bulk",
            json={"ids": [item["id"]], "changes": {"due_at": due}},
            headers=alex,
        )
        assert bulk.status_code == 422, bulk.text
