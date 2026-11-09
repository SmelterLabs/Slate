"""Configured defaults affect new work, never rewrite a stored recurrence zone."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from slate import dates
from slate.service import recurrences
from slate.settings import Settings, settings

API = "/api/v1"


@pytest.fixture
def tokyo(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Asia/Tokyo")
    now = datetime(2030, 1, 1, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(recurrences, "utcnow", lambda: now)
    # dates.LOCAL is intentionally initialized once at process startup.
    monkeypatch.setattr(dates, "LOCAL", ZoneInfo("Asia/Tokyo"))


def test_settings_reject_invalid_iana_zone(monkeypatch):
    monkeypatch.setenv("SLATE_TIMEZONE", "Not/A_Zone")
    with pytest.raises(ValidationError, match="IANA timezone"):
        Settings(_env_file=None)


def test_invalid_zone_fails_process_startup():
    result = subprocess.run(
        [sys.executable, "-c", "import slate.app"],
        env={**os.environ, "SLATE_TIMEZONE": "Not/A_Zone"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "valid IANA timezone" in result.stderr


def test_settings_default_to_utc(monkeypatch):
    monkeypatch.delenv("SLATE_TIMEZONE", raising=False)
    assert Settings(_env_file=None).timezone == "UTC"


def test_recurrence_preview_defaults_to_workspace(tokyo):
    now = datetime(2030, 1, 1, 23, 30, tzinfo=UTC)
    preview = recurrences.preview("FREQ=DAILY", now=now)
    assert preview["timezone"] == "Asia/Tokyo"
    assert preview["dtstart"] == datetime(2030, 1, 1, 15, tzinfo=UTC)
    assert preview["occurrences"][0]["local"].startswith("2030-01-02")
    explicit = recurrences.preview("FREQ=DAILY", timezone="Europe/London", now=now)
    assert explicit["timezone"] == "Europe/London"


def test_new_rule_default_and_existing_explicit_rule_survive(
    client, alex, todo, tokyo, monkeypatch
):
    body = {"list_id": todo["id"], "title_template": "Daily", "rrule": "FREQ=DAILY"}
    made = client.post(f"{API}/recurrences", headers=alex, json=body)
    assert made.status_code == 201, made.text
    assert made.json()["timezone"] == "Asia/Tokyo"
    assert made.json()["dtstart"] == "2030-01-01T15:00:00Z"
    explicit = client.post(
        f"{API}/recurrences", headers=alex, json={**body, "timezone": "America/Chicago"}
    ).json()
    assert explicit["timezone"] == "America/Chicago"
    monkeypatch.setattr(settings, "timezone", "Europe/London")
    changed = client.patch(
        f"{API}/recurrences/{explicit['id']}", headers=alex, json={"title_template": "Renamed"}
    ).json()
    for key in ("timezone", "dtstart", "next_run_at", "next_occurrences"):
        assert changed[key] == explicit[key]
