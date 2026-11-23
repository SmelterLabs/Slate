"""A card closed while an agent runs it: the agent's own close lets the run report back, and a
run nobody settles leaves no expired-lease badge on a closed card."""

from datetime import timedelta

from conftest import API
from slate.models import Event, WorkLease, utcnow
from slate.service import work
from test_run_safety import setup_task
from test_work import _next


def _close(client, h, item_id, status="Done"):
    r = client.patch(f"{API}/items/{item_id}", json={"status": status}, headers=h)
    assert r.status_code == 200, r.text


def _beat(client, h, item_id, run_id):
    return client.post(f"{API}/work/{item_id}/heartbeat", headers=h, json={"run_id": run_id})


def _finish(client, h, item_id, run_id, **body):
    return client.post(f"{API}/work/{item_id}/finish", headers=h, json={"run_id": run_id, **body})


def test_an_agent_that_closes_its_own_card_still_reports_back(client, alex, todo, make_account, db):
    _, h, item = setup_task(client, alex, todo, make_account)
    run_id = _next(client, h)["lease"]["run_id"]
    _close(client, h, item["id"])

    assert _beat(client, h, item["id"], run_id).status_code == 200
    r = _finish(
        client,
        h,
        item["id"],
        run_id,
        outcome="review",
        status="Check the result",
        reply="Fixed and closed.",
    )
    assert r.status_code == 200, r.text

    card = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert card["completed_at"] is not None
    assert card["agent"] is None  # a closed card carries no flag, whatever the outcome said
    assert card["waiting_on_account_id"] is None
    bodies = [
        c["body_md"] for c in client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    ]
    assert "Fixed and closed." in bodies
    db.expire_all()
    assert db.get(WorkLease, item["id"]) is None


def test_a_card_someone_else_closes_still_stops_the_run(client, alex, todo, make_account):
    _, h, item = setup_task(client, alex, todo, make_account)
    run_id = _next(client, h)["lease"]["run_id"]
    _close(client, alex, item["id"])

    assert _beat(client, h, item["id"], run_id).status_code == 409
    assert _finish(client, h, item["id"], run_id, outcome="done").status_code == 409


def test_a_lease_that_runs_out_on_a_closed_card_is_settled_not_quarantined(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    _close(client, alex, item["id"])

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()

    assert db.get(WorkLease, item["id"]) is None
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["agent"] is None
    ev = db.query(Event).filter_by(item_id=item["id"], type="item.work_finished").one()
    assert ev.after_json["reconciled"] is True
    assert ev.after_json["expired"] is True
    assert ev.after_json["runtime_state"] == "unknown"
    assert client.get(f"{API}/work", headers=h).json() == []


def test_a_lease_that_runs_out_on_an_open_card_is_still_quarantined(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()

    assert db.get(WorkLease, item["id"]).quarantined
    card = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert card["agent"]["state"] == "blocked"


def test_an_ask_turn_on_a_done_card_is_still_quarantined_when_it_runs_out(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _close(client, alex, item["id"])
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        headers=alex,
        json={"body_md": "One more thing?", "assignee": "worker"},
    )
    assert r.status_code == 201, r.text
    assert _next(client, h)["item"]["id"] == item["id"]

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()

    assert db.get(WorkLease, item["id"]).quarantined


def test_closing_a_card_whose_lease_already_ran_out_settles_that_lease(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()
    assert db.get(WorkLease, item["id"]).quarantined
    _close(client, alex, item["id"])

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()

    assert db.get(WorkLease, item["id"]) is None
    assert client.get(f"{API}/work", headers=h).json() == []
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["agent"] is None


def test_a_lease_that_runs_out_after_the_holder_was_unassigned_frees_the_card(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    run_id = _next(client, h)["lease"]["run_id"]
    _, other = make_account("other-agent")
    r = client.patch(
        f"{API}/items/{item['id']}", json={"assignees": ["other-agent"]}, headers=alex
    )
    assert r.status_code == 200, r.text
    assert _beat(client, h, item["id"], run_id).status_code == 409
    assert _next(client, other)["item"] is None  # the first run may still be going

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()

    assert db.get(WorkLease, item["id"]) is None
    ev = db.query(Event).filter_by(item_id=item["id"], type="item.work_finished").one()
    assert ev.after_json["reconciled"] is True and "unassigned" in ev.after_json["evidence"]
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["agent"] is None
    assert _next(client, other)["item"]["id"] == item["id"]


def test_a_lease_that_runs_out_on_an_archived_card_is_settled(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    r = client.post(f"{API}/items/{item['id']}/archive", headers=alex)
    assert r.status_code in (200, 204), r.text

    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()

    assert db.get(WorkLease, item["id"]) is None
    assert client.get(f"{API}/work", headers=h).json() == []
