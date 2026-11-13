"""Execution proofs are per-attempt, not per-account. No live/runtime calls."""

from datetime import timedelta

import pytest

from conftest import API
from slate.models import WorkLease, utcnow
from slate.service import work
from test_work import _item, _next


def setup_task(client, alex, todo, make_account, *, asked=False):
    account, headers = make_account("worker")
    item = _item(client, alex, todo["id"], "Safety", assignees=[] if asked else ["worker"])
    if asked:
        r = client.post(
            f"{API}/items/{item['id']}/comments",
            headers=alex,
            json={"body_md": "Question", "assignee": "worker"},
        )
        assert r.status_code == 201
    return account, headers, item


def test_fenced_finish_rejects_combined_next_without_side_effects(client, alex, todo, make_account):
    _, h, item = setup_task(client, alex, todo, make_account)
    first = _next(client, h)
    r = client.post(
        f"{API}/work/{item['id']}/finish",
        headers=h,
        json={"run_id": first["lease"]["run_id"], "outcome": "done", "next": {}},
    )
    assert r.status_code == 422
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["completed_at"] is None


def test_work_kind_capability_filter_prevents_unsupported_take(client, alex, todo, make_account):
    _, h, item = setup_task(client, alex, todo, make_account)
    client.post(f"{API}/channels/general/messages", headers=alex, json={"body_md": "@worker hi"})
    chat = _next(client, h, kinds=["chat"])
    assert chat["item"] is None and chat["chat"] is not None
    task = _next(client, h, kinds=["task"])
    assert task["item"]["id"] == item["id"] and task["chat"] is None


def test_same_account_cannot_resume_without_proof(client, alex, todo, make_account):
    _, h, item = setup_task(client, alex, todo, make_account)
    first = _next(client, h)
    assert _next(client, h)["item"] is None
    proof = first["lease"]["run_id"]
    assert proof
    resumed = _next(client, h, item_id=item["id"], run_id=proof)
    assert resumed["resumed"] and resumed["lease"]["run_id"] == proof
    held = client.get(f"{API}/work", headers=h).json()
    assert all(not row.get("run_id") for row in held), "listing must not mint resume authority"


@pytest.mark.parametrize("operation", ["heartbeat", "finish", "report"])
@pytest.mark.parametrize("proof", [None, "wrong-proof"])
def test_mutations_require_current_proof(client, alex, todo, make_account, operation, proof):
    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    body = {"heartbeat": {}, "finish": {"outcome": "done"}, "report": {"state": "done"}}[operation]
    if proof:
        body["run_id"] = proof
    prefix = "items" if operation == "report" else "work"
    r = client.post(f"{API}/{prefix}/{item['id']}/{operation}", headers=h, json=body)
    assert r.status_code == 409, r.text
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["completed_at"] is None


@pytest.mark.parametrize("asked", [False, True])
def test_finish_receipt_is_repeat_safe_and_semantic(client, alex, todo, make_account, asked):
    _, h, item = setup_task(client, alex, todo, make_account, asked=asked)
    taken = _next(client, h)
    body = {"outcome": "done", "reply": "Answer", "run_id": taken["lease"]["run_id"]}
    path = f"{API}/work/{item['id']}/finish"
    first = client.post(path, headers=h, json=body)
    assert first.status_code == 200, first.text
    events = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    second = client.post(path, headers=h, json=body)
    assert second.status_code == 200, second.text
    assert client.get(f"{API}/items/{item['id']}/events", headers=alex).json() == events
    assert bool(second.json()["item"]["completed_at"]) is (not asked)
    assert client.post(path, headers=h, json={**body, "reply": "Different"}).status_code == 409


def test_stale_finish_cannot_touch_new_attempt(client, alex, todo, make_account):
    _, h, item = setup_task(client, alex, todo, make_account)
    first = _next(client, h)
    path = f"{API}/work/{item['id']}"
    old = first["lease"]["run_id"]
    assert (
        client.post(
            path + "/finish", headers=h, json={"run_id": old, "outcome": "review"}
        ).status_code
        == 200
    )
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    second = _next(client, h)
    assert second["lease"]["run_id"] != old
    assert (
        client.post(
            path + "/finish", headers=h, json={"run_id": old, "outcome": "done"}
        ).status_code
        == 409
    )
    assert client.post(path + "/heartbeat", headers=h, json={"run_id": old}).status_code == 409


@pytest.mark.parametrize("action", ["archive", "close", "unassign"])
def test_control_changes_fence_current_work(client, alex, todo, make_account, action):
    _, h, item = setup_task(client, alex, todo, make_account)
    proof = _next(client, h)["lease"]["run_id"]
    if action == "archive":
        r = client.post(f"{API}/items/{item['id']}/archive", headers=alex)
    else:
        r = client.patch(
            f"{API}/items/{item['id']}",
            headers=alex,
            json={"status": "Done"} if action == "close" else {"assignees": []},
        )
    assert r.is_success
    for suffix, body in [("heartbeat", {}), ("finish", {"outcome": "review"})]:
        r = client.post(
            f"{API}/work/{item['id']}/{suffix}", headers=h, json={"run_id": proof, **body}
        )
        assert r.status_code == 409, r.text


def test_manual_report_remains_available_without_lease(client, alex, todo, make_account):
    _, h, item = setup_task(client, alex, todo, make_account)
    r = client.post(f"{API}/items/{item['id']}/report", headers=h, json={"state": "done"})
    assert r.status_code == 200 and r.json()["completed_at"]


def test_expiry_rechecks_under_change_lock(client, alex, todo, make_account, db, monkeypatch):
    from contextlib import contextmanager

    _, h, item = setup_task(client, alex, todo, make_account)
    _next(client, h)
    row = db.get(WorkLease, item["id"])
    now = utcnow() + timedelta(hours=1)
    original = work.apply_change

    @contextmanager
    def renewed_before_lock(session, actor):
        row.until = now + timedelta(hours=1)
        session.commit()
        with original(session, actor) as cx:
            yield cx

    monkeypatch.setattr(work, "apply_change", renewed_before_lock)
    assert work.expire_leases(db, now) == 0
    assert db.get(WorkLease, item["id"]) is not None


def test_chat_proof_retry_and_pending_turn_isolation(client, alex, make_account):
    _, h = make_account("worker")
    channel = "general"

    def mention(text):
        r = client.post(
            f"{API}/channels/{channel}/messages", headers=alex, json={"body_md": "@worker " + text}
        )
        assert r.status_code == 201, r.text

    mention("first")
    first = _next(client, h, channel=channel)
    assert _next(client, h, channel=channel)["chat"] is None
    proof = first["chat"]["lease"]["run_id"]
    mention("second")
    body = {"channel": channel, "run_id": proof, "outcome": "done", "reply": "First answer"}
    r = client.post(f"{API}/work/chat/finish", headers=h, json=body)
    assert r.status_code == 200 and r.json()["pending"], r.text
    second = _next(client, h, channel=channel)
    assert second["chat"]["lease"]["run_id"] != proof
    retry = client.post(f"{API}/work/chat/finish", headers=h, json=body)
    assert retry.status_code == 200 and retry.json() == r.json()
    assert (
        client.post(
            f"{API}/work/chat/heartbeat", headers=h, json={"channel": channel, "run_id": proof}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{API}/work/chat/finish", headers=h, json={**body, "reply": "Changed"}
        ).status_code
        == 409
    )


def test_expired_task_cannot_restart_until_confirmed_stopped(client, alex, todo, make_account, db):
    _, h, item = setup_task(client, alex, todo, make_account)
    first = _next(client, h)
    work.expire_leases(db, utcnow() + timedelta(hours=1))
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 409
    assert _next(client, h)["item"] is None
    path = f"{API}/work/{item['id']}/reconcile"
    assert (
        client.post(
            path, headers=h, json={"run_id": "wrong", "state": "stopped", "evidence": "confirmed"}
        ).status_code
        == 409
    )
    r = client.post(
        path,
        headers=h,
        json={
            "run_id": first["lease"]["run_id"],
            "state": "stopped",
            "evidence": "runtime control confirmed stop",
        },
    )
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, h)["lease"]["run_id"] != first["lease"]["run_id"]


def test_expired_chat_waits_for_reconcile_until_someone_asks_again(client, alex, make_account, db):
    from slate.service import chatwake

    _, h = make_account("worker")
    client.post(f"{API}/channels/general/messages", headers=alex, json={"body_md": "@worker one"})
    first = _next(client, h)
    chatwake.expire(db, utcnow() + timedelta(hours=1))
    # Quarantined: nothing to take until the run is settled.
    assert _next(client, h)["chat"] is None
    r = client.post(
        f"{API}/work/chat/reconcile",
        headers=h,
        json={
            "channel": "general",
            "run_id": first["chat"]["lease"]["run_id"],
            "state": "stopped",
            "evidence": "remote confirmed stop",
        },
    )
    assert r.status_code == 200, r.text
    assert _next(client, h)["chat"] is None  # nothing waited in the turn


def test_a_mention_after_expiry_starts_a_new_fenced_run(client, alex, make_account, db):
    """A person's new mention settles the expired run (it is never swallowed by it), and the
    old run's proof stays fenced out of the new turn."""
    from slate.service import chatwake

    _, h = make_account("worker")
    client.post(f"{API}/channels/general/messages", headers=alex, json={"body_md": "@worker one"})
    first = _next(client, h)
    old = first["chat"]["lease"]["run_id"]
    chatwake.expire(db, utcnow() + timedelta(hours=1))
    client.post(f"{API}/channels/general/messages", headers=alex, json={"body_md": "@worker two"})
    second = _next(client, h)
    assert second["chat"] is not None and second["chat"]["lease"]["run_id"] != old
    for path, extra in (("heartbeat", {}), ("finish", {"outcome": "done", "reply": "late"})):
        r = client.post(
            f"{API}/work/chat/{path}",
            headers=h,
            json={"channel": "general", "run_id": old, **extra},
        )
        assert r.status_code == 409, r.text
    r = client.post(
        f"{API}/work/chat/reconcile",
        headers=h,
        json={"channel": "general", "run_id": old, "state": "stopped", "evidence": "late"},
    )
    assert r.status_code == 409, r.text


def test_plain_chat_post_does_not_settle_fenced_turn(client, alex, make_account):
    _, h = make_account("worker")
    client.post(f"{API}/channels/general/messages", headers=alex, json={"body_md": "@worker hi"})
    taken = _next(client, h, channel="general")
    assert client.post(
        f"{API}/channels/general/messages", headers=h, json={"body_md": "Unrelated"}
    ).is_success
    r = client.post(
        f"{API}/work/chat/heartbeat",
        headers=h,
        json={"channel": "general", "run_id": taken["chat"]["lease"]["run_id"]},
    )
    assert r.status_code == 200, r.text
