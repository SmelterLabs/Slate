"""Reminders and the daily digest: set, fire once, reach the inbox and check-in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from slate.models import Event
from slate.service import reminders

API = "/api/v1"
NOW = datetime(2030, 1, 9, 13, 0, tzinfo=UTC)  # 07:00 in Chicago


def new_item(client, headers, todo, **extra):
    r = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Pay rent", **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def remind(client, headers, item_id, **body):
    return client.post(f"{API}/items/{item_id}/reminders", json=body, headers=headers)


def inbox_reasons(client, headers):
    return [e["reason"] for e in client.get(f"{API}/inbox", headers=headers).json()]


def fired(db) -> list[Event]:
    db.expire_all()
    return list(db.scalars(select(Event).where(Event.type == "item.reminder").order_by(Event.id)))


def test_a_reminder_at_a_time_fires_once_into_the_inbox(client, alex, todo, db):
    item = new_item(client, alex, todo)
    r = remind(client, alex, item["id"], at="2030-01-09T15:00:00Z", note="Before the bank shuts")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["at"].startswith("2030-01-09T15:00") and body["fired"] is False
    assert (
        client.get(f"{API}/items/{item['id']}/reminders", headers=alex).json()[0]["id"]
        == (body["id"])
    )

    assert reminders.fire_reminders(db, now=NOW) == 0
    later = NOW + timedelta(hours=3)
    assert reminders.fire_reminders(db, now=later) == 1
    assert reminders.fire_reminders(db, now=later) == 0  # once

    (ev,) = fired(db)
    assert ev.source.value == "system" and ev.item_id == item["id"]
    assert ev.after_json["note"] == "Before the bank shuts"
    assert ev.after_json["account"] == "alex"
    # A reminder is news even though nobody else changed anything.
    assert inbox_reasons(client, alex) == ["reminder"]
    assert client.get(f"{API}/reminders", headers=alex).json() == []  # fired: not upcoming


def test_before_the_due_date_follows_the_date(client, alex, todo, db):
    undated = new_item(client, alex, todo)
    r = remind(client, alex, undated["id"], before_minutes=60)
    assert r.status_code == 422 and "no due date" in r.text

    item = new_item(client, alex, todo, due_at="2030-01-09T16:00:00Z")
    r = remind(client, alex, item["id"], before_minutes=60)
    assert r.json()["at"].startswith("2030-01-09T15:00")
    assert reminders.fire_reminders(db, now=datetime(2030, 1, 9, 15, 1, tzinfo=UTC)) == 1

    # Pushed back a day: the same reminder goes off again before the new date.
    client.patch(f"{API}/items/{item['id']}", json={"due_at": "2030-01-10T16:00:00Z"}, headers=alex)
    assert reminders.fire_reminders(db, now=datetime(2030, 1, 9, 15, 2, tzinfo=UTC)) == 0
    assert reminders.fire_reminders(db, now=datetime(2030, 1, 10, 15, 1, tzinfo=UTC)) == 1


def test_done_items_stay_quiet(client, alex, todo, db):
    item = new_item(client, alex, todo)
    remind(client, alex, item["id"], at="2030-01-09T15:00:00Z")
    client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=alex)
    assert reminders.fire_reminders(db, now=NOW + timedelta(days=1)) == 0


def test_reminding_someone_else(client, alex, todo, db, make_account):
    agent_acct, agent = make_account("agent")
    item = new_item(client, alex, todo)
    r = remind(client, alex, item["id"], at="2030-01-09T15:00:00Z", account="agent")
    assert r.json()["account"] == "agent"
    rid = r.json()["id"]
    assert [x["id"] for x in client.get(f"{API}/reminders", headers=agent).json()] == [rid]
    assert client.get(f"{API}/reminders", headers=alex).json() == []

    reminders.fire_reminders(db, now=NOW + timedelta(hours=3))
    assert inbox_reasons(client, agent) == ["reminder"]
    assert "reminder" not in inbox_reasons(client, alex)
    news = client.get(f"{API}/checkin", headers=agent).json()["news"]
    assert [n["why"] for n in news if n["type"] == "item.reminder"] == ["reminder"]


def test_only_the_target_or_setter_removes(client, alex, todo, make_account):
    _, agent = make_account("agent")
    _, agent2 = make_account("agent2")
    item = new_item(client, alex, todo)
    rid = remind(client, alex, item["id"], at="2030-01-09T15:00:00Z", account="agent").json()["id"]
    assert client.delete(f"{API}/reminders/{rid}", headers=agent2).status_code == 403
    assert client.delete(f"{API}/reminders/{rid}", headers=agent).status_code == 204
    assert client.get(f"{API}/items/{item['id']}/reminders", headers=alex).json() == []
    assert client.delete(f"{API}/reminders/{rid}", headers=alex).status_code == 404


def test_bad_reminders_are_refused(client, alex, todo):
    item = new_item(client, alex, todo, due_at="2030-01-09")
    assert remind(client, alex, item["id"]).status_code == 422
    assert (
        remind(client, alex, item["id"], at="2030-01-09T15:00:00Z", before_minutes=5).status_code
        == 422
    )
    assert remind(client, alex, item["id"], at="not a time").status_code == 422
    assert remind(client, alex, item["id"], at="2030-01-09", account="nobody").status_code in (
        404,
        422,
    )


def digests(db) -> list[Event]:
    db.expire_all()
    return list(db.scalars(select(Event).where(Event.type == "account.digest")))


def test_daily_digest_once_a_day_at_the_hour(client, alex, todo, db):
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    new_item(client, alex, todo, due_at="2030-01-08", assignees=["alex"])  # overdue
    new_item(client, alex, todo, due_at="2030-01-09T20:00:00Z", assignees=["alex"])  # today
    new_item(client, alex, todo, due_at="2030-01-09T20:00:00Z")  # not mine

    assert reminders.send_digests(db, now=NOW - timedelta(hours=1)) == 0  # 6 am: too early
    assert reminders.send_digests(db, now=NOW) == 1
    assert reminders.send_digests(db, now=NOW + timedelta(hours=5)) == 0  # once a day
    (ev,) = digests(db)
    assert ev.subject_id == me["id"]
    assert ev.after_json["counts"] == {"overdue": 1, "today": 1, "waiting": 0}
    assert inbox_reasons(client, alex) == ["digest"]

    assert reminders.send_digests(db, now=NOW + timedelta(days=1)) == 1  # next morning

    preview = client.get(f"{API}/me/digest", headers=alex).json()
    assert preview["enabled"] is True and preview["hour"] == 7


def test_digest_choices(client, alex, todo, db, make_account):
    make_account("agent")  # an agent: digest off unless it asks
    new_item(client, alex, todo, due_at="2030-01-08", assignees=["alex", "agent"])
    r = client.patch(f"{API}/me/prefs", json={"digest_hour": 9}, headers=alex)
    assert r.status_code == 200, r.text
    assert reminders.send_digests(db, now=NOW) == 0  # 7 am, Alex wants 9
    assert reminders.send_digests(db, now=NOW + timedelta(hours=2)) == 1
    client.patch(f"{API}/me/prefs", json={"digest": False}, headers=alex)
    assert reminders.send_digests(db, now=NOW + timedelta(days=1, hours=2)) == 0

    assert client.patch(f"{API}/me/prefs", json={"digest_hour": 24}, headers=alex).status_code == (
        422
    )
    assert client.patch(f"{API}/me/prefs", json={"digest": "yes"}, headers=alex).status_code == (
        422
    )


def test_an_empty_day_sends_nothing(client, alex, db):
    assert reminders.send_digests(db, now=NOW) == 0


# --- Alerts before the due date ------------------------------------------------------------


def test_many_alerts_before_due_each_fire_once(client, alex, todo, db):
    item = new_item(client, alex, todo, due_at="2030-01-16T18:00:00Z")
    leads = [5, 15, 30, 60, 120, 1440, 2880, 10080]
    for m in leads:
        r = remind(client, alex, item["id"], before_minutes=m)
        assert r.status_code == 201, r.text
    rows = client.get(f"{API}/items/{item['id']}/reminders", headers=alex).json()
    assert sorted(r["before_minutes"] for r in rows) == leads
    assert min(r["at"] for r in rows).startswith("2030-01-09T18:00")  # a week before

    # Walk the clock to the due time: each alert goes off exactly once, in order.
    due = datetime(2030, 1, 16, 18, 0, tzinfo=UTC)
    total = 0
    for m in sorted(leads, reverse=True):
        total += reminders.fire_reminders(db, now=due - timedelta(minutes=m))
    assert total == len(leads)
    assert reminders.fire_reminders(db, now=due) == 0
    assert len(fired(db)) == len(leads)


def test_the_same_alert_twice_is_one_alert(client, alex, todo, db, make_account):
    make_account("agent")
    item = new_item(client, alex, todo, due_at="2030-01-16T18:00:00Z")
    first = remind(client, alex, item["id"], before_minutes=60).json()
    again = remind(client, alex, item["id"], before_minutes=60)
    assert again.status_code == 201 and again.json()["id"] == first["id"]
    # Someone else's alert at the same lead is theirs, not a duplicate.
    other = remind(client, alex, item["id"], before_minutes=60, account="agent").json()
    assert other["id"] != first["id"]
    db.expire_all()
    sets = db.scalars(select(Event).where(Event.type == "item.reminder_set")).all()
    assert len(sets) == 2


def test_alerts_on_a_date_only_due_date_count_back_from_5_pm(client, alex, todo):
    item = new_item(client, alex, todo, due_at="2030-01-16")  # 17:00 Chicago = 23:00 UTC
    hour = remind(client, alex, item["id"], before_minutes=60).json()
    day = remind(client, alex, item["id"], before_minutes=1440).json()
    assert hour["at"].startswith("2030-01-16T22:00")  # 4 pm Chicago that day
    assert day["at"].startswith("2030-01-15T23:00")  # 5 pm Chicago the day before


def test_none_by_default(client, alex, todo):
    item = new_item(client, alex, todo, due_at="2030-01-16T18:00:00Z")
    assert client.get(f"{API}/items/{item['id']}/reminders", headers=alex).json() == []
