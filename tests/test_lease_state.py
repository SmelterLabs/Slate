"""A lease says what state it is in (`working` or `expired`) wherever it is shown, from one
rule (`work.lease_state`), so an agent or the board never has to compare `until` with a clock."""

from datetime import timedelta

from conftest import API
from slate.models import WorkLease, utcnow
from slate.service import agents, work
from test_run_safety import setup_task
from test_work import _next


def _held(client, h):
    r = client.get(f"{API}/work", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_held_task_is_working_then_expired(client, alex, todo, make_account, db):
    _, h, item = setup_task(client, alex, todo, make_account)
    taken = _next(client, h)
    assert taken["lease"]["state"] == "working"
    (lease,) = _held(client, h)
    assert lease["state"] == "working"
    (me,) = agents.plan(db)
    assert [x["state"] for x in me["leases"]] == ["working"]

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()
    (lease,) = _held(client, h)
    assert lease["item_id"] == item["id"] and lease["state"] == "expired"
    (me,) = agents.plan(db)
    assert [x["state"] for x in me["leases"]] == ["expired"]


def test_a_lease_past_its_time_is_expired_before_the_sweep_marks_it(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    row = db.get(WorkLease, item["id"])
    now = utcnow()
    assert work.lease_state(row, now) == "working"
    assert work.lease_state(row, row.until + timedelta(seconds=1)) == "expired"
    assert not row.quarantined  # the scheduler has not run: the clock alone decides
    assert set(work.LEASE_STATES) == {"working", "expired"}
