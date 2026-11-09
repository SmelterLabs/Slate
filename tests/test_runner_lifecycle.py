"""Deterministic runner lifecycle races; all HTTP goes to the isolated test app."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from datetime import timedelta

import httpx
import pytest

from test_clients import _assign, _runner


class Child:
    """A controllable Popen boundary: no real process or timing-dependent sleeps."""

    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.returncode = None
        self.exited = threading.Event()
        self.waiting = threading.Event()
        self.timeouts = 0

    def wait(self, timeout=None):
        if self.timeouts:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired("test child", timeout)
        self.waiting.set()
        assert self.exited.wait(5), "test must release the child"
        return self.returncode

    def poll(self):
        return self.returncode

    def finish(self, code=0):
        self.returncode = code
        self.exited.set()

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


def _thread(fn):
    errors = []
    done = threading.Event()

    def run():
        try:
            fn()
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, errors


@pytest.mark.parametrize("source", ["periodic", "stdout"])
def test_heartbeat_transport_failure_keeps_child_reserved_and_recovers(
    client, alex, todo, make_account, tmp_path, monkeypatch, source
):
    _, headers = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, headers, tmp_path)
    task = runner.take(item["id"])
    child = Child()
    if source == "periodic":
        child.stdout = io.StringIO('{"outcome":"done"}\n')
        child.timeouts = 2
    else:
        child.stdout = io.StringIO('{"note": "First"}\n{"note": "Recovered"}\n{"outcome":"done"}\n')
    monkeypatch.setattr("slate_runner.subprocess.Popen", lambda *a, **kw: child)
    call = runner.call
    beats = []
    recovered = threading.Event()

    def flaky(method, path, body=None):
        if path.endswith("/heartbeat"):
            beats.append(path)
            if len(beats) == 1:
                raise httpx.ReadError("temporary transport failure")
        result = call(method, path, body)
        if path.endswith("/heartbeat"):
            recovered.set()
        return result

    monkeypatch.setattr(runner, "call", flaky)
    thread, done, errors = _thread(lambda: runner.run(task))
    try:
        # Either the run dies (the bug) or it recovers and reaches its next child wait.
        assert child.waiting.wait(1) or done.is_set()
        assert not done.is_set(), errors
        assert item["id"] in runner._claimed
        assert runner.take(item["id"]) is None
        assert recovered.wait(2)
        assert len(beats) == 2
    finally:
        child.finish()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert not runner.running and not runner._claimed
    assert client.get(f"/api/v1/items/{item['id']}", headers=alex).json()["completed_at"]


def test_unexpected_run_failure_does_not_make_a_live_child_takeable_again(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    _, headers = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, headers, tmp_path)
    task = runner.take(item["id"])
    child = Child()

    def fail(task):
        runner.running[item["id"]] = child
        raise httpx.ReadError("unexpected failure with the child still alive")

    monkeypatch.setattr(runner, "_run", fail)
    monkeypatch.setattr("slate_runner.STOP_GRACE", 0)
    try:
        with pytest.raises(httpx.ReadError):
            runner.run(task)
        assert item["id"] in runner._claimed
        assert runner.take(item["id"]) is None
        runner._claimed.clear()  # running is also an independent last-resort duplicate guard
        assert runner.take(item["id"]) is None
        assert runner.take() is None
    finally:
        runner._claimed.add(item["id"])
        child.finish(-15)
        runner.stop()
    assert not runner.running and not runner._claimed


def test_hint_queued_during_empty_take_is_handed_off_before_worker_retires(
    client, make_account, tmp_path, monkeypatch
):
    _, headers = make_account("agent")
    runner = _runner(client, headers, tmp_path)
    empty_take = threading.Event()
    return_empty = threading.Event()
    ran = []

    def take(item_id=None):
        if item_id is None:
            empty_take.set()
            assert return_empty.wait(5)
            return None
        return {"item": {"id": item_id}}

    def run(task):
        ran.append(task["item"]["id"])
        runner.stopping.set()

    monkeypatch.setattr(runner, "take", take)
    monkeypatch.setattr(runner, "run", run)
    runner._workers = 1
    thread, _, errors = _thread(lambda: runner._work(None))
    try:
        assert empty_take.wait(2)
        runner.dispatch("just-became-due")
        assert list(runner.pending) == ["just-became-due"]
        return_empty.set()
        thread.join(3)
        with runner._state_lock:
            runner._state_lock.wait_for(lambda: runner._workers == 0, timeout=3)
        assert ran == ["just-became-due"]
        assert not runner.pending and runner._workers == 0
        assert not errors
    finally:
        return_empty.set()
        runner.stopping.set()
        thread.join(5)


@pytest.mark.parametrize("preheld", [False, True])
def test_startup_fills_free_slots_but_never_adopts_unproven_leases(
    client, alex, todo, make_account, tmp_path, monkeypatch, preheld
):
    _, headers = make_account("agent")
    items = [_assign(client, alex, todo, title=f"Held {i}") for i in range(4)]
    for item in items if preheld else []:
        response = client.post("/api/v1/work/next", json={"item_id": item["id"]}, headers=headers)
        assert response.status_code == 200 and response.json()["item"]["id"] == item["id"]
    runner = _runner(client, headers, tmp_path, parallel=4)
    children = []
    launched = threading.Condition()
    stream_ready = threading.Event()
    stream_end = threading.Event()

    def spawn(*args, **kwargs):
        child = Child()
        with launched:
            children.append(child)
            launched.notify_all()
        return child

    def stream():
        stream_ready.set()
        assert stream_end.wait(5)
        return iter(())

    monkeypatch.setattr("slate_runner.subprocess.Popen", spawn)
    monkeypatch.setattr(runner, "stream", stream)
    thread, _, errors = _thread(runner.forever)
    try:
        assert stream_ready.wait(2)
        if preheld:
            with runner._state_lock:
                assert runner._state_lock.wait_for(lambda: runner._workers == 0, timeout=2)
            assert children == [] and not runner._claimed
        else:
            with launched:
                assert launched.wait_for(lambda: len(children) == 4, timeout=2), len(children)
            assert {i["id"] for i in items} == runner._claimed
            assert all(child.returncode is None for child in children)
    finally:
        runner.stopping.set()
        stream_end.set()
        for child in children:
            child.finish()
        thread.join(5)
        runner.stop()
    assert not errors


@pytest.mark.parametrize("pause_at", ["take", "before_run"])
def test_stop_waits_for_inflight_take_without_spawning_or_unassigning(
    client, alex, todo, make_account, tmp_path, monkeypatch, pause_at
):
    _, headers = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, headers, tmp_path)
    taken = threading.Event()
    return_take = threading.Event()
    call = runner.call
    spawned = []

    def delayed(method, path, body=None):
        result = call(method, path, body)
        if path == "/work/next" and pause_at == "take":
            taken.set()
            assert return_take.wait(5)
        return result

    run = runner.run

    def delayed_run(task):
        if pause_at == "before_run":
            taken.set()
            assert return_take.wait(5)
        return run(task)

    monkeypatch.setattr(runner, "call", delayed)
    monkeypatch.setattr(runner, "run", delayed_run)
    monkeypatch.setattr("slate_runner.STOP_GRACE", 0)
    monkeypatch.setattr(runner, "_run", lambda task: spawned.append(task))
    runner._workers = 1
    worker, _, errors = _thread(lambda: runner._work(item["id"]))
    stopper = None
    try:
        assert taken.wait(2)
        stopper, stopped, stop_errors = _thread(runner.stop)
        assert runner.stopping.wait(2)
        assert not stopped.wait(0.05), "stop must wait for the outstanding take"
        return_take.set()
        stopper.join(3)
        worker.join(3)
        assert stopped.is_set() and not stop_errors and not errors
        assert spawned == []
        runner.dispatch(item["id"])
        assert runner.take(item["id"]) is None
        assert not runner.pending
        assert runner._workers == 0 and not runner._claimed and not runner.running
        held = client.get("/api/v1/work", headers=headers).json()
        assert [lease["item_id"] for lease in held] == [item["id"]]
        view = client.get(f"/api/v1/items/{item['id']}", headers=alex).json()
        assert [a["handle"] for a in view["assignees"]] == ["agent"]
        assert view["agent"]["state"] == "working" and not view["completed_at"]
        assert _runner(client, headers, tmp_path).take(item["id"]) is None
    finally:
        return_take.set()
        worker.join(5)
        if stopper:
            stopper.join(5)


@pytest.mark.parametrize("takeover", [False, True])
def test_forced_stop_leaves_lease_and_assignment_untouched(
    client, alex, todo, make_account, tmp_path, monkeypatch, takeover
):
    from slate.db import SessionLocal
    from slate.models import utcnow
    from slate.service import work

    _, headers = make_account("agent")
    item = _assign(client, alex, todo, who=("agent", "alex"))
    runner = _runner(client, headers, tmp_path)
    task = runner.take(item["id"])
    child = Child()
    child.pid = -12345
    monkeypatch.setattr("slate_runner.subprocess.Popen", lambda *a, **kw: child)
    monkeypatch.setattr("slate_runner.os.killpg", lambda *a: child.terminate(), raising=False)
    monkeypatch.setattr("slate_runner.subprocess.run", lambda *a, **kw: child.terminate())
    monkeypatch.setattr("slate_runner.STOP_GRACE", 0)
    thread, _, errors = _thread(lambda: runner.run(task))
    try:
        assert child.waiting.wait(2)
        owner_headers = headers
        if takeover:
            # Expire the former holder deterministically while its local child still runs.
            _, owner_headers = make_account("replacement")
            with SessionLocal() as session:
                assert work.expire_leases(session, utcnow() + timedelta(hours=1)) == 1
            response = client.patch(
                f"/api/v1/items/{item['id']}",
                json={"assignees": ["agent", "alex", "replacement"]},
                headers=alex,
            )
            assert response.status_code == 200
            replacement = _runner(client, owner_headers, tmp_path)
            assert replacement.take(item["id"]) is None
        before = client.get(f"/api/v1/items/{item['id']}", headers=alex).json()
        held = client.get("/api/v1/work", headers=owner_headers).json()
        events = client.get(f"/api/v1/items/{item['id']}/events", headers=alex).json()
        runner.stop()
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert child.poll() is not None
        assert not runner.running and not runner._claimed
        assert client.get("/api/v1/work", headers=owner_headers).json() == held
        view = client.get(f"/api/v1/items/{item['id']}", headers=alex).json()
        assert view == before
        assert {"agent", "alex"} <= {a["handle"] for a in view["assignees"]}
        assert view["agent"]["state"] == ("blocked" if takeover else "working")
        assert not view["completed_at"]
        assert client.get(f"/api/v1/items/{item['id']}/events", headers=alex).json() == events
        assert client.get(f"/api/v1/items/{item['id']}/comments", headers=alex).json() == []
        if takeover:
            assert _runner(client, headers, tmp_path).take(item["id"]) is None
        resumed = _runner(client, owner_headers, tmp_path).take(item["id"])
        assert resumed is None
    finally:
        child.finish(-15)
        thread.join(5)


def test_exited_parent_with_sleeping_descendant_has_bounded_drain_and_no_late_heartbeat(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    _, headers = make_account("agent")
    item = _assign(client, alex, todo)
    gate = tmp_path / "descendant-exit"
    script = tmp_path / "inherited_pipes.py"
    script.write_text(
        "import json, pathlib, subprocess, sys, time\n"
        "if len(sys.argv) == 2:\n"
        "    json.load(sys.stdin)\n"
        "    subprocess.Popen([sys.executable, __file__, sys.argv[1], 'descendant'],\n"
        "                     stdin=subprocess.DEVNULL)\n"
        "    print(json.dumps({'outcome': 'review', 'note': 'Parent finished'}), flush=True)\n"
        "else:\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.02)\n"
        "    print(json.dumps({'note': 'Late heartbeat'}), flush=True)\n"
        "    print(json.dumps({'outcome': 'done'}), flush=True)\n"
        "    print('Late plain output', flush=True)\n"
        "    print('Late error', file=sys.stderr, flush=True)\n"
    )
    runner = _runner(client, headers, tmp_path, f'"{sys.executable}" "{script}" "{gate}"')
    task = runner.take(item["id"])
    popen = subprocess.Popen
    thread_type = threading.Thread
    spawned = threading.Event()
    parents = []
    readers = []
    heartbeats = []

    def spawn(*args, **kwargs):
        proc = popen(*args, **kwargs)
        parents.append(proc)
        spawned.set()
        return proc

    def track_thread(*args, **kwargs):
        thread = thread_type(*args, **kwargs)
        if getattr(kwargs.get("target"), "__name__", "") in ("read_stdout", "read_stderr"):
            readers.append(thread)
        return thread

    heartbeat = runner.heartbeat

    def progress(*args, **kwargs):
        heartbeats.append(kwargs)
        heartbeat(*args, **kwargs)

    monkeypatch.setattr("slate_runner.subprocess.Popen", spawn)
    monkeypatch.setattr("slate_runner.threading.Thread", track_thread)
    monkeypatch.setattr("slate_runner.STOP_GRACE", 0)
    monkeypatch.setattr(runner, "heartbeat", progress)
    outcomes = []
    worker, done, errors = _thread(lambda: outcomes.append(runner.run(task)))
    stopper = None
    try:
        assert spawned.wait(3)
        assert parents[0].wait(timeout=3) == 0
        stopper, stopped, stop_errors = _thread(runner.stop)
        assert done.wait(3), "an exited parent must not wait for descendant pipe EOF"
        assert stopped.wait(1), "stop must not wait for descendant pipe EOF"
        assert not errors and not stop_errors
        assert outcomes == ["review"]
        assert not runner.running and not runner._claimed
        assert any(reader.is_alive() for reader in readers), "descendant must still hold pipes"
        before = client.get(f"/api/v1/items/{item['id']}", headers=alex).json()
        events = client.get(f"/api/v1/items/{item['id']}/events", headers=alex).json()
        assert before["agent"]["state"] == "review"
        gate.touch()
        for reader in readers:
            reader.join(3)
            assert not reader.is_alive()
        assert heartbeats == [], "late progress must not recreate the finished lease"
        assert client.get("/api/v1/work", headers=headers).json() == []
        assert client.get(f"/api/v1/items/{item['id']}", headers=alex).json() == before
        assert client.get(f"/api/v1/items/{item['id']}/events", headers=alex).json() == events
    finally:
        gate.touch()
        worker.join(5)
        if stopper:
            stopper.join(5)
        for reader in readers:
            reader.join(5)


def test_forced_stop_reaps_real_shell_command_before_returning(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    _, headers = make_account("agent")
    item = _assign(client, alex, todo)
    gate = tmp_path / "exit"
    script = tmp_path / "wait_for_stop.py"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'note': 'Child started'}), flush=True)\n"
        "deadline = time.monotonic() + 10\n"
        "while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n"
    )
    runner = _runner(client, headers, tmp_path, f'"{sys.executable}" "{script}" "{gate}"')
    ready = threading.Event()
    heartbeat = runner.heartbeat

    def progress(*args, **kwargs):
        heartbeat(*args, **kwargs)
        ready.set()

    monkeypatch.setattr(runner, "heartbeat", progress)
    monkeypatch.setattr("slate_runner.STOP_GRACE", 0)
    task = runner.take(item["id"])
    worker, _, errors = _thread(lambda: runner.run(task))
    stopper = None
    try:
        assert ready.wait(3)
        stopper, stopped, stop_errors = _thread(runner.stop)
        assert stopped.wait(3), "the shell's child kept stdout/stderr open after termination"
        worker.join(3)
        assert not errors and not stop_errors and not worker.is_alive()
        view = client.get(f"/api/v1/items/{item['id']}", headers=alex).json()
        assert [a["handle"] for a in view["assignees"]] == ["agent"]
        assert view["agent"]["state"] == "working" and not view["completed_at"]
    finally:
        gate.touch()
        worker.join(5)
        if stopper:
            stopper.join(5)
