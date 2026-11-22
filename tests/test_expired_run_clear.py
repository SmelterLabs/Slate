"""An expired, quarantined run must not strand the card: a person's Clear confirms the stop, and
an agent's own reconcile wakes its runner for the Asks still waiting. No live/runtime calls."""

from datetime import timedelta
from types import SimpleNamespace

from conftest import API
from slate.models import Event, WorkLease, utcnow
from slate.service import work
from test_run_safety import setup_task
from test_work import _next


def _expire(db, item_id):
    work.expire_leases(db, utcnow() + timedelta(hours=1))
    db.expire_all()
    row = db.get(WorkLease, item_id)
    assert row is not None and row.quarantined


def _ask(client, alex, item_id, text="What now?"):
    r = client.post(
        f"{API}/items/{item_id}/comments",
        headers=alex,
        json={"body_md": text, "assignee": "worker"},
    )
    assert r.status_code == 201, r.text


def test_person_clear_on_expired_run_confirms_stop_and_frees_the_card(
    client, alex, todo, make_account, db
):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    _expire(db, item["id"])
    _ask(client, alex, item["id"])
    assert _next(client, h)["item"] is None  # still fenced: nobody confirmed the stop

    r = client.post(
        f"{API}/items/{item['id']}/report", headers=alex, json={"state": "idle", "comment": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent"] is None
    db.expire_all()
    assert db.get(WorkLease, item["id"]) is None
    ev = db.query(Event).filter_by(item_id=item["id"], type="item.work_finished").one()
    assert ev.after_json["reconciled"] is True
    assert ev.after_json["runtime_state"] == "stopped"
    assert "cleared" in ev.after_json["evidence"]

    # The Ask that arrived while the card was fenced is answered on the next take.
    taken = _next(client, h)
    assert taken["item"]["id"] == item["id"]


def test_person_clear_never_overrides_a_live_lease(client, alex, todo, make_account, db):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    r = client.post(
        f"{API}/items/{item['id']}/report", headers=alex, json={"state": "idle", "comment": False}
    )
    assert r.status_code == 409
    db.expire_all()
    assert db.get(WorkLease, item["id"]) is not None


def test_agent_cannot_clear_its_own_expired_run_without_proof(client, alex, todo, make_account, db):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    _expire(db, item["id"])
    r = client.post(
        f"{API}/items/{item['id']}/report", headers=h, json={"state": "idle", "comment": False}
    )
    assert r.status_code == 409


def test_own_reconcile_wakes_the_runner_for_a_waiting_ask(client, alex, todo, make_account, db):
    from slate.api import checkin

    account, h, item = setup_task(client, alex, todo, make_account)
    first = _next(client, h)
    _expire(db, item["id"])
    _ask(client, alex, item["id"])
    r = client.post(
        f"{API}/work/{item['id']}/reconcile",
        headers=h,
        json={"run_id": first["lease"]["run_id"], "state": "stopped", "evidence": "confirmed"},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    ev = db.query(Event).filter_by(item_id=item["id"], type="item.work_finished").one()
    payload = {
        "id": str(ev.id),
        "type": ev.type,
        "item_id": ev.item_id,
        "subject_id": ev.subject_id,
        "actor_id": ev.actor_id,
        "field": ev.field,
        "before": ev.before_json,
        "after": ev.after_json,
    }
    me = SimpleNamespace(id=account["id"], handle="worker")
    out = checkin._judge(SimpleNamespace(access=None), me, False, payload, set())
    assert any(m.event == "work" for m in out), out

    assert payload["after"]["expired"] is True

    # An ordinary finish, or a reconcile giving back a live grant, never wakes it (no loop).
    gate = SimpleNamespace(access=None)
    for after in ({"reconciled": False}, {"expired": False}):
        other = {**payload, "after": {**payload["after"], **after}}
        assert not any(m.event == "work" for m in checkin._judge(gate, me, False, other, set()))


def test_runner_takes_a_card_again_once_its_lost_attempt_is_settled(
    client, alex, todo, make_account, tmp_path, db
):
    import json

    from test_clients import _assign, _runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    first = runner.take(item["id"])
    runner._lost_execution(item["id"])  # heartbeat refused: local fence, remote unknown
    runner._claimed.discard(item["id"])
    _expire(db, item["id"])
    assert runner.take(item["id"]) is None and runner.take() is None  # server still fences

    r = client.post(
        f"{API}/items/{item['id']}/report", headers=alex, json={"state": "idle", "comment": False}
    )
    assert r.status_code == 200, r.text
    again = runner.take()
    assert again and again["item"]["id"] == item["id"]
    assert again["lease"]["run_id"] != first["lease"]["run_id"]
    saved = json.loads(runner.state_path.read_text())["runs"][item["id"]]
    assert saved == {
        "run_id": again["lease"]["run_id"],
        "state": "taken",
        "remote_state": "not_started",
    }
    # The new grant is a new execution: its result is delivered. The fence the lost attempt
    # left on this card must not outlive that attempt.
    assert runner.run(again) != "reconciliation_required"
    assert runner._records[item["id"]]["state"] == "delivered"


def test_runner_gives_back_a_grant_it_will_not_run(client, alex, todo, make_account, tmp_path, db):
    from test_clients import _assign, _runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    # This process is still busy with the key (a take on the wire, a run winding down).
    runner._claimed.add(item["id"])
    runner._record(item["id"], run_id="old", state="running", remote_state="unknown")
    assert runner.take() is None
    db.expire_all()
    assert db.get(WorkLease, item["id"]) is None, "the grant must not be left to expire"
    assert runner._records[item["id"]]["run_id"] == "old"


def test_runner_forgets_a_lost_attempt_the_server_has_settled(
    client, alex, todo, make_account, tmp_path, db
):
    """A card nobody grants again (it is done) must not keep its record for ever: the record
    blocks the drain receipt, and an undelivered result is resent without end."""
    from test_clients import _assign, _runner

    _, h = make_account("agent")
    lost, undelivered = _assign(client, alex, todo), _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    runner.take(lost["id"])
    runner._lost_execution(lost["id"])
    runner._claimed.discard(lost["id"])
    grant = runner.take(undelivered["id"])
    runner._claimed.discard(undelivered["id"])
    _expire(db, lost["id"])
    path = f"/work/{undelivered['id']}/finish"
    body = {"outcome": "done", "run_id": grant["lease"]["run_id"]}
    assert runner._deliver(undelivered["id"], path, body) == "delivery_failed"  # 409, expired

    # Both attempts are still held on the server (quarantined): the run proof is still needed.
    assert runner.settle_released() == []
    assert set(runner._records) == {lost["id"], undelivered["id"]}

    r = client.post(
        f"{API}/items/{lost['id']}/report", headers=alex, json={"state": "idle", "comment": False}
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"{API}/work/{undelivered['id']}/reconcile",
        headers=h,
        json={"run_id": grant["lease"]["run_id"], "state": "completed", "evidence": "run ended"},
    )
    assert r.status_code == 200, r.text
    # Keep the cleared card from being granted again, as a done card never is.
    assert sorted(runner.settle_released()) == sorted([lost["id"], undelivered["id"]])
    assert runner._records == {}
    assert runner.retry_due(now=1e9) == []
    runner.drain_path.touch()
    assert runner.drain_status() == {"admission_closed": True, "drained": True}


def test_runner_keeps_an_undelivered_result_while_its_lease_is_live(
    client, alex, todo, make_account, tmp_path
):
    from test_clients import _assign, _runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    grant = runner.take(item["id"])
    runner._claimed.discard(item["id"])
    runner._record(
        item["id"],
        state="delivery_failed",
        remote_state="terminal",
        path=f"/work/{item['id']}/finish",
        result={
            "outcome": "review",
            "status": "Check the result",
            "run_id": grant["lease"]["run_id"],
        },
    )
    assert runner.settle_released() == []
    runner.retry_due(now=0.0)  # schedules the first resend
    assert runner.retry_due(now=1e9) == [item["id"]]  # which delivers it
    assert runner._records[item["id"]]["state"] == "delivered"
