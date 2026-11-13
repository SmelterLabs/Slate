"""The Health page reports the deploy pack's backups from data/backup-status.json."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from slate.models import utcnow
from slate.service import health
from slate.settings import settings


@pytest.fixture
def status_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "slate.db")
    return tmp_path / "backup-status.json"


def _write(path, *, ok, at, last_ok_at, detail="slate-2026-11-13.db 4096 bytes"):
    path.write_text(
        json.dumps(
            {
                "ok": ok,
                "at": at.isoformat(),
                "detail": detail,
                "last_ok_at": last_ok_at and last_ok_at.isoformat(),
            }
        )
    )


def _check(db):
    return {c["key"]: c for c in health.report(db)["checks"]}.get("backups")


def test_no_line_without_the_deploy_pack(db, status_file):
    assert _check(db) is None


def test_a_recent_backup_is_ok(db, status_file):
    now = utcnow()
    _write(status_file, ok=True, at=now - timedelta(hours=3), last_ok_at=now - timedelta(hours=3))
    check = _check(db)
    assert check["status"] == "ok" and "3 h ago" in check["detail"]
    assert health.report(db)["backups"]["ok"] is True


def test_a_failed_backup_fails_the_check(db, status_file):
    now = utcnow()
    _write(
        status_file,
        ok=False,
        at=now - timedelta(hours=1),
        last_ok_at=now - timedelta(hours=25),
        detail="no database at /data/slate.db (SLATE_DATABASE_PATH)",
    )
    check = _check(db)
    assert check["status"] == "fail"
    assert "no database at" in check["detail"] and "25 h ago" in check["detail"]
    assert health.summary(db)["status"] == "fail"  # GET /health answers 503


def test_backups_that_stopped_fail_the_check(db, status_file):
    then = utcnow() - timedelta(hours=40)
    _write(status_file, ok=True, at=then, last_ok_at=then)
    check = _check(db)
    assert check["status"] == "fail" and "40 h" in check["detail"]


def test_an_unreadable_status_file_says_so(db, status_file):
    status_file.write_text("{not json")
    assert _check(db)["status"] == "warn"
