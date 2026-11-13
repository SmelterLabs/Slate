"""A person with nothing to report is not re-checked for a digest every scheduler tick."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from slate.models import Event
from slate.service import reminders

API = "/api/v1"
NOW = datetime(2030, 1, 9, 13, 0, tzinfo=UTC)  # 7 am in the test timezone (America/Chicago)


@pytest.fixture(autouse=True)
def fresh_quiet():
    reminders._quiet.clear()
    yield
    reminders._quiet.clear()


def test_an_empty_digest_is_checked_again_at_most_once_an_hour(client, alex, todo, db, monkeypatch):
    calls = []
    real = reminders.digest_for

    def digest_for(session, account, now=None):
        calls.append(now)
        return real(session, account, now=now)

    monkeypatch.setattr(reminders, "digest_for", digest_for)
    for minute in range(5):
        assert reminders.send_digests(db, now=NOW + timedelta(minutes=minute)) == 0
    assert len(calls) == 1
    # Something lands later that day: the next hourly check still sends the digest.
    r = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Due", "due_at": "2030-01-09", "assignees": ["alex"]},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    assert reminders.send_digests(db, now=NOW + timedelta(minutes=30)) == 0
    assert reminders.send_digests(db, now=NOW + timedelta(minutes=61)) == 1
    assert len(calls) == 2
    db.expire_all()
    assert len(list(db.scalars(select(Event).where(Event.type == "account.digest")))) == 1
