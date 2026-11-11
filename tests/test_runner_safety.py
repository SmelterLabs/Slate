"""Deterministic proof, delivery, capability and drain contract tests."""

import io
import json

import httpx
import pytest

from test_clients import _assign, _runner
from test_runner_lifecycle import Child, _thread


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize(
    "code,result",
    [(0, None), (7, {"outcome": "done", "reply": "Looks done"}), (7, {"outcome": "review"})],
)
def test_incomplete_or_failed_command_never_succeeds(
    client, make_account, tmp_path, chat, code, result
):
    _, h = make_account("worker")
    runner = _runner(client, h, tmp_path)
    task = {"chat": {}} if chat else {"item": {"assignees": []}}
    body = (
        runner._chat_result(code, result, "plain stdout", "failed")
        if chat
        else runner._result(task, code, result, "stdout", "failed")
    )
    assert body["outcome"] == ("release" if chat else "blocked")
    assert not body.get("reply")


@pytest.mark.parametrize("status", [401, 403, 409, 426, 500, "transport"])
def test_finish_failure_is_not_success_or_unsafe_fallback(
    client, alex, todo, make_account, tmp_path, monkeypatch, status
):
    import slate_runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    task = runner.take(item["id"])
    child = Child()
    child.stdout = io.StringIO('{"outcome":"done","reply":"Finished"}\n')
    child.finish()
    monkeypatch.setattr(slate_runner.subprocess, "Popen", lambda *a, **k: child)
    original = runner.call
    attempts = []

    def fail(method, path, body=None):
        if path.endswith("/finish"):
            attempts.append(body)
            if status == "transport":
                raise httpx.ReadError("response lost")
            raise slate_runner.SlateHTTPError(status, "test refusal")
        return original(method, path, body)

    monkeypatch.setattr(runner, "call", fail)
    assert runner.run(task) == "delivery_failed"
    assert attempts and all(p == attempts[0] for p in attempts)
    assert all(p["run_id"] == task["lease"]["run_id"] for p in attempts)
    assert not client.get(f"/api/v1/items/{item['id']}", headers=alex).json()["completed_at"]
    saved = json.loads(runner.state_path.read_text())
    assert saved["runs"][item["id"]]["state"] == "delivery_failed"


@pytest.mark.parametrize("chat", [False, True])
def test_delivery_receipt_survives_response_loss_and_runner_restart(
    client, alex, todo, make_account, tmp_path, monkeypatch, chat
):
    import slate_runner

    _, h = make_account("agent")
    if chat:
        client.post(
            "/api/v1/channels/general/messages",
            headers=alex,
            json={"body_md": "@agent hello"},
        )
    else:
        _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    task = runner.take()
    key = slate_runner.task_key(task)
    child = Child()
    child.stdout = io.StringIO('{"outcome":"done","reply":"Exactly once"}\n')
    child.finish()
    monkeypatch.setattr(slate_runner.subprocess, "Popen", lambda *a, **k: child)
    original = runner.call

    def lose_response(method, path, body=None):
        result = original(method, path, body)
        if path.endswith("/finish"):
            raise httpx.ReadError("response lost after server committed")
        return result

    monkeypatch.setattr(runner, "call", lose_response)
    assert runner.run(task) == "delivery_failed"
    restarted = _runner(client, h, tmp_path)
    restarted.drain_path.write_text("upgrade")
    assert not restarted.drain_status()["drained"]
    monkeypatch.setattr(
        slate_runner.subprocess, "Popen", lambda *a, **k: pytest.fail("retry relaunched command")
    )
    saved = json.loads(restarted.state_path.read_text())["runs"][key]["result"]
    attempts = []
    replay = restarted.call

    def capture(method, path, body=None):
        attempts.append(body)
        return replay(method, path, body)

    monkeypatch.setattr(restarted, "call", capture)
    assert restarted.retry_delivery(key) == "done"
    assert attempts == [saved]
    assert restarted.drain_status()["drained"]
    if chat:
        messages = client.get("/api/v1/channels/general/messages", headers=alex).json()
        assert sum(m["body_md"] == "Exactly once" for m in messages) == 1
    else:
        events = client.get(f"/api/v1/items/{key}/events", headers=alex).json()
        assert sum(e["type"] == "item.work_finished" for e in events) == 1


@pytest.mark.parametrize("status", [401, 403, 409, 426])
def test_definitive_heartbeat_loss_stops_local_child_and_quarantines_remote_unknown(
    client, alex, todo, make_account, tmp_path, monkeypatch, status
):
    import slate_runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    task = runner.take(item["id"])
    child = Child()
    child.pid = -12345
    child.timeouts = 1
    monkeypatch.setattr(slate_runner.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(slate_runner.os, "killpg", lambda *a: child.terminate(), raising=False)
    monkeypatch.setattr(slate_runner.subprocess, "run", lambda *a, **k: child.terminate())
    original = runner.call

    def refuse(method, path, body=None):
        if path.endswith("/heartbeat"):
            raise slate_runner.SlateHTTPError(status, "permission/ownership lost")
        return original(method, path, body)

    monkeypatch.setattr(runner, "call", refuse)
    assert runner.run(task) == "reconciliation_required"
    assert child.poll() is not None
    assert runner.take(item["id"]) is None
    state = json.loads(runner.state_path.read_text())["runs"][item["id"]]
    assert state["state"] == "reconciliation_required"
    assert state["remote_state"] == "unknown"


def test_runner_does_not_adopt_another_same_account_lease(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    one = _runner(client, h, tmp_path / "one")
    two = _runner(client, h, tmp_path / "two")
    assert one.take(item["id"])
    assert two.take(item["id"]) is None


def test_generic_take_fills_distinct_chat_slots(client, alex, todo, make_account, tmp_path):
    _, h = make_account("agent")
    for ch in ["general", f"list:{todo['id']}"]:
        assert client.post(
            f"/api/v1/channels/{ch}/messages", headers=alex, json={"body_md": "@agent hello"}
        ).is_success
    runner = _runner(client, h, tmp_path, parallel=2)
    first, second = runner.take(), runner.take()
    assert first and second and first["chat"]["channel"] != second["chat"]["channel"]


def test_old_server_without_proof_fails_closed(client, make_account, tmp_path, monkeypatch):
    _, h = make_account("agent")
    runner = _runner(client, h, tmp_path)
    monkeypatch.setattr(runner, "call", lambda *a, **kw: {"item": {"id": "legacy"}, "lease": {}})
    with pytest.raises(Exception, match="fenc|proof|protocol"):
        runner.take()


@pytest.mark.parametrize("pause_at", ["before_lock", "on_wire", "before_spawn"])
def test_drain_admission_race_never_launches_after_close(
    client, alex, todo, make_account, tmp_path, monkeypatch, pause_at
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    original = runner.call

    def take_call(method, path, body=None):
        result = original(method, path, body)
        if path == "/work/next" and pause_at == "on_wire":
            runner.drain_path.write_text("upgrade")
        return result

    monkeypatch.setattr(runner, "call", take_call)
    monkeypatch.setattr(
        "slate_runner.subprocess.Popen", lambda *a, **kw: pytest.fail("closed admission spawned")
    )
    if pause_at == "before_lock":
        runner.drain_path.write_text("upgrade")
    task = runner.take(item["id"])
    if task:
        runner.drain_path.write_text("upgrade")
        runner.run(task)
    assert runner.drain_status()["drained"]
    runner.drain_path.unlink()
    assert runner.take() is None, "admission must stay closed until process replacement"


def test_runner_work_kind_hook_is_sent_before_taking(client, alex, todo, make_account, tmp_path):
    _, h = make_account("agent")
    _assign(client, alex, todo)
    client.post(
        f"/api/v1/channels/list:{todo['id']}/messages", headers=alex, json={"body_md": "@agent hi"}
    )
    runner = _runner(client, h, tmp_path, env={"SLATE_WORK_KINDS": "chat"})
    assert runner.take()["chat"] is not None


@pytest.mark.parametrize("generic", [False, True])
def test_hint_arriving_during_empty_take_is_not_lost(
    client, alex, todo, make_account, tmp_path, monkeypatch, generic
):
    import threading

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    checking, release, launched = threading.Event(), threading.Event(), threading.Event()
    original = runner.take
    first = True

    def delayed_take(key=None):
        nonlocal first
        if first:
            first = False
            checking.set()
            assert release.wait(3)
            return None
        return original(key)

    monkeypatch.setattr(runner, "take", delayed_take)
    monkeypatch.setattr(runner, "run", lambda task: launched.set())
    runner.dispatch(None)
    assert checking.wait(2)
    runner.dispatch(None if generic else item["id"])
    release.set()
    assert launched.wait(3), "a hint dropped while an empty take was in flight"
    with runner._state_lock:
        assert runner._state_lock.wait_for(lambda: runner._workers == 0, timeout=3)


def test_stream_ready_refills_all_slots(client, make_account, tmp_path, monkeypatch):
    _, h = make_account("agent")
    runner = _runner(client, h, tmp_path, parallel=3)
    dispatched = []
    monkeypatch.setattr(runner, "dispatch", dispatched.append)
    runner.on_message({"event": "ready", "data": {}})
    assert dispatched == [None, None, None]


def test_restart_with_unstarted_take_cannot_claim_drained(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    original = _runner(client, h, tmp_path)
    assert original.take(item["id"])
    restarted = _runner(client, h, tmp_path)
    restarted.drain_path.write_text("upgrade")
    assert restarted.drain_status() == {"admission_closed": True, "drained": False}


def test_drain_closes_admission_but_never_kills_existing_work(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    import slate_runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    task = runner.take(item["id"])
    child = Child()
    child.stdout = io.StringIO('{"outcome":"done"}\n')
    monkeypatch.setattr(slate_runner.subprocess, "Popen", lambda *a, **kw: child)
    worker, _, errors = _thread(lambda: runner.run(task))
    try:
        assert child.waiting.wait(2)
        runner.drain_path.write_text("upgrade")
        assert runner.take() is None
        assert not runner.drain_status()["drained"]
        assert child.poll() is None
        child.finish()
        worker.join(3)
        assert not errors
        assert runner.drain_status() == {"admission_closed": True, "drained": True}
        assert runner.take() is None
    finally:
        child.finish()
        worker.join(5)


def _later_run(client, alex, todo, make_account, tmp_path, monkeypatch, due_at=None):
    import slate_runner

    _, h = make_account("agent")
    make_account("member", kind="person")
    body = {"list_id": todo["id"], "title": "Post hours", "assignees": ["agent", "member"]}
    if due_at:
        body["due_at"] = due_at
    item = client.post("/api/v1/items", json=body, headers=alex).json()
    if due_at:
        assert client.post(f"/api/v1/items/{item['id']}/run", headers=alex).status_code == 202
    runner = _runner(client, h, tmp_path)
    task = runner.take(item["id"])
    child = Child()
    child.stdout = io.StringIO('{"outcome":"later","reply":"Posting at the due time."}\n')
    child.finish()
    monkeypatch.setattr(slate_runner.subprocess, "Popen", lambda *a, **k: child)
    outcome = runner.run(task)
    return outcome, client.get(f"/api/v1/items/{item['id']}", headers=alex).json()


def test_runner_delivers_later(client, alex, todo, make_account, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    due = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    outcome, view = _later_run(client, alex, todo, make_account, tmp_path, monkeypatch, due)
    assert outcome == "later"
    assert view["agent"] is None and view["waiting_on_account_id"] is None
    assert view["completed_at"] is None


def test_runner_turns_a_refused_later_into_a_visible_block(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    outcome, view = _later_run(client, alex, todo, make_account, tmp_path, monkeypatch)
    assert outcome == "blocked"  # no due time: nothing would run it again, so a person must
    assert view["agent"]["state"] == "blocked"
    assert view["waiting_on_account_id"] is not None
    comments = client.get(f"/api/v1/items/{view['id']}/comments", headers=alex).json()
    assert comments[-1]["body_md"].startswith("Posting at the due time.")
    assert "no due time" in comments[-1]["body_md"]
