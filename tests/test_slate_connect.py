"""Offline local setup: only explicit consent can reach a runner."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

MODULE = Path(__file__).resolve().parents[1] / "clients/python/slate_connect.py"


def load():
    spec = importlib.util.spec_from_file_location("slate_connect", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connect():
    return load()


def fake_server(request):
    path = request.url.path
    if path == "/healthz":
        return httpx.Response(200, json={"version": "test", "min_runner_protocol": 2})
    if path.endswith("/exchange"):
        assert request.method == "POST"
        assert "code" in json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "token": "LOCAL-TEST-SECRET",
                "account_handle": "robot",
                "server_protocol": 2,
                "connection": {
                    "id": "aBcDeF123456",
                    "account_id": "agent",
                    "list_ids": ["list-a"],
                    "state": "connected",
                },
            },
        )
    if path.endswith("/me"):
        return httpx.Response(
            200,
            json={
                "account": {"id": "agent", "handle": "robot", "kind": "agent"},
                "scopes": ["write"],
                "access": {"lists": {"list-a": "edit"}},
            },
        )
    if path.endswith("/lists"):
        return httpx.Response(200, json=[{"id": "list-a"}])
    if path.endswith("/agent-connections"):
        return httpx.Response(
            200,
            json={
                "connections": [
                    {
                        "id": "aBcDeF123456",
                        "state": "connected",
                        "account_id": "agent",
                        "list_ids": ["list-a"],
                        "capabilities": ["task"],
                    }
                ]
            },
        )
    if path.endswith("/heartbeat"):
        return httpx.Response(200, json={"state": "connected"})
    if request.method == "DELETE":
        return httpx.Response(204)
    raise AssertionError(str(request.url))


def options(tmp_path):
    return dict(
        url="http://127.0.0.1:8765",
        code="ONE-USE-CODE",
        name="robot",
        root=tmp_path / "connections",
        adapter="custom",
        command=f'"{sys.executable}" -V',
        cwd=tmp_path,
        device_name="test",
        profile=None,
        endpoint=None,
        runtime_key_env=None,
    )


def test_url_validation_and_no_argv_code(connect, capsys):
    for bad in (
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "https://example.com#code",
        "file:///tmp",
    ):
        with pytest.raises(connect.SetupError):
            connect.origin(bad)
    assert connect.origin("http://127.0.0.1:8/") == "http://127.0.0.1:8"
    assert connect.origin("https://example.com") == "https://example.com"
    with pytest.raises(SystemExit):
        connect.parser().parse_args(["connect", "--url", "https://example.com", "--code", "secret"])
    assert "secret" not in capsys.readouterr().err


def test_connect_owner_only_no_overwrite_no_profile_changes(connect, tmp_path):
    calls = []

    def server(r):
        calls.append(r)
        return fake_server(r)

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        config = connect.connect_local(client=client, **options(tmp_path))
        directory = tmp_path / "connections/robot"
        connect.check_private(directory)
        connect.check_private(directory / "credentials.json")
        saved = json.loads((directory / "config.json").read_text())
        assert "LOCAL-TEST-SECRET" not in json.dumps(saved)
        assert "ONE-USE-CODE" not in "".join(
            f.read_text() for f in directory.iterdir() if f.is_file()
        )
        assert config["connection_id"] == "aBcDeF123456"
        before = len(calls)
        with pytest.raises(connect.SetupError):
            connect.connect_local(client=client, **options(tmp_path))
        assert len(calls) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["connections"]


def test_network_and_local_failures_do_not_exchange(connect, tmp_path):
    called = []

    def server(r):
        called.append(r.method)
        return httpx.Response(503, json={"detail": "LOCAL-TEST-SECRET"})

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(connect.SetupError) as error:
            connect.connect_local(client=client, **options(tmp_path))
        assert "SECRET" not in str(error.value)
        assert not (tmp_path / "connections/robot").exists()
        bad = options(tmp_path) | {"cwd": tmp_path / "missing"}
        before = len(called)
        with pytest.raises(connect.SetupError):
            connect.connect_local(client=client, **bad)
        assert len(called) == before


def test_doctor_read_only_and_test_does_not_claim_ready(connect, tmp_path):
    calls = []

    def server(r):
        calls.append(r)
        return fake_server(r)

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
        directory = tmp_path / "connections/robot"
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        calls.clear()
        result = connect.doctor(directory, client=client)
        assert result["state"] == "connected"
        assert "not run" in result["detail"]
        assert all(r.method == "GET" for r in calls)
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
        calls.clear()
        result = connect.doctor(directory, client=client, test=True)
        assert result["state"] == "needs_setup"
        assert all(r.method == "GET" for r in calls)


def test_runtime_missing_and_wrong_identity_never_launch(connect, tmp_path):
    with httpx.Client(transport=httpx.MockTransport(fake_server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
        directory = tmp_path / "connections/robot"
        cfg = json.loads((directory / "config.json").read_text())
        cfg["command"] = "nonexistent-executable-for-test"
        (directory / "config.json").write_text(json.dumps(cfg))
        assert connect.doctor(directory, client=client)["state"] == "runtime_unavailable"
        with pytest.raises(connect.SetupError):
            connect.run_local(directory, client=client, once=True)


def test_disconnect_readback_before_remove(connect, tmp_path):
    revoked = False

    def server(r):
        nonlocal revoked
        if r.method == "DELETE":
            revoked = True
            return httpx.Response(204)
        if revoked and r.url.path.endswith("/me"):
            return httpx.Response(401)
        return fake_server(r)

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
        directory = tmp_path / "connections/robot"
        connect.disconnect(directory, client=client)
        assert not (directory / "credentials.json").exists()
        assert (directory / "config.json").exists()
        assert connect.local_list(tmp_path / "connections")[0]["state"] == "disconnected"


def test_reject_symlink_storage(connect, tmp_path):
    if os.name == "nt":
        pytest.skip("Windows unprivileged symlink creation depends on developer mode")
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(connect.SetupError):
        connect.connect_local(client=None, **(options(tmp_path) | {"root": alias}))


def test_real_runner_claims_dummy_exec_and_finishes(
    connect, tmp_path, client, alex, todo, make_account
):
    agent, _ = make_account()
    paired = client.post(
        "/api/v1/agent-connections/pairings",
        headers=alex,
        json={"account_id": agent["id"], "label": "Isolated test", "list_ids": [todo["id"]]},
    ).json()
    dummy = tmp_path / "dummy.py"
    dummy.write_text(
        "import json, sys\ntask = json.load(sys.stdin)\n"
        'print(json.dumps({"outcome": "done", "note": "Dummy completed"}))\n'
    )
    opt = options(tmp_path) | {"code": paired["code"], "command": f'"{sys.executable}" "{dummy}"'}
    connect.connect_local(client=client, **opt)
    item = client.post(
        "/api/v1/items",
        headers=alex,
        json={"list_id": todo["id"], "title": "Local dummy only", "assignees": [agent["handle"]]},
    ).json()
    assert connect.run_local(tmp_path / "connections/robot", client=client, once=True) == 0
    view = client.get("/api/v1/items/" + item["id"], headers=alex).json()
    assert view["status"]["kind"] == "done"
    assert connect.run_local(tmp_path / "connections/robot", client=client, once=True) == 2


def test_resident_path_calls_supported_runner_method(connect, tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []

    class DummyRunner:
        def __init__(self, *args, **kwargs):
            pass

        def forever(self):
            calls.append("forever")

        def stop(self):
            calls.append("stop")

    module = SimpleNamespace(Runner=DummyRunner, RUNNER_PROTOCOL=2, SlateHTTPError=RuntimeError)
    monkeypatch.setattr(connect, "_runner_module", lambda: module)
    with httpx.Client(transport=httpx.MockTransport(fake_server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
        assert connect.run_local(tmp_path / "connections/robot", client=client) == 0
    assert calls == ["forever", "stop"]


def test_signal_does_not_wait_for_own_active_run(connect, tmp_path, monkeypatch):
    import signal
    import threading
    from types import SimpleNamespace

    stopped = threading.Event()
    main_thread = threading.get_ident()

    class DummyRunner:
        def __init__(self, *args, **kwargs):
            pass

        def forever(self):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            assert stopped.wait(2)

        def stop(self):
            assert threading.get_ident() != main_thread
            stopped.set()

    module = SimpleNamespace(Runner=DummyRunner, RUNNER_PROTOCOL=2, SlateHTTPError=RuntimeError)
    monkeypatch.setattr(connect, "_runner_module", lambda: module)
    with httpx.Client(transport=httpx.MockTransport(fake_server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
        connect.run_local(tmp_path / "connections/robot", client=client)


def test_heartbeat_failure_stops_resident_without_secret_logs(connect, tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    stopped = threading.Event()
    beats = []

    class DummyRunner:
        def __init__(self, *args, **kwargs):
            pass

        def forever(self):
            assert stopped.wait(2)

        def stop(self):
            stopped.set()

    module = SimpleNamespace(Runner=DummyRunner, RUNNER_PROTOCOL=2, SlateHTTPError=RuntimeError)
    monkeypatch.setattr(connect, "_runner_module", lambda: module)
    monkeypatch.setattr(connect, "HEARTBEAT_SECONDS", 0.01)

    def server(r):
        if r.url.path.endswith("/heartbeat"):
            beats.append(r)
            if len(beats) > 1:
                return httpx.Response(401, json={"detail": "LOCAL-TEST-SECRET"})
        return fake_server(r)

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
        with pytest.raises(connect.SetupError, match="heartbeat failed") as error:
            connect.run_local(tmp_path / "connections/robot", client=client)
        assert "SECRET" not in str(error.value)
        assert len(beats) == 2


def test_doctor_wrong_identity_and_protocol_fail_before_mutations(connect, tmp_path):
    with httpx.Client(transport=httpx.MockTransport(fake_server)) as client:
        connect.connect_local(client=client, **options(tmp_path))
    calls = []

    def wrong(r):
        calls.append(r)
        if r.url.path.endswith("/me"):
            return httpx.Response(200, json={"account": {"id": "someone-else"}})
        return fake_server(r)

    with httpx.Client(transport=httpx.MockTransport(wrong)) as client:
        with pytest.raises(connect.SetupError, match="identity"):
            connect.run_local(tmp_path / "connections/robot", client=client)
        assert all(r.method == "GET" for r in calls)

    def outdated(r):
        assert r.method == "GET"
        return httpx.Response(200, json={"min_runner_protocol": 9999})

    with httpx.Client(transport=httpx.MockTransport(outdated)) as client:
        with pytest.raises(connect.SetupError, match="protocol"):
            connect.connect_local(client=client, **(options(tmp_path) | {"name": "another"}))
        assert not (tmp_path / "connections/another").exists()


@pytest.mark.parametrize("bad", [False, True, "exit", "timeout", "readback"])
def test_explicit_custom_probe(tmp_path, monkeypatch, bad):
    connect = load()
    beats, launches = [], []
    verified = None

    def server(request):
        nonlocal verified
        if request.url.path.endswith("/heartbeat"):
            check = json.loads(request.content)["check"]
            beats.append(check)
            verified = "2026-11-03T00:00:00Z" if check == "probe_passed" else None
            return httpx.Response(200, json={"state": "ready", "verified_at": verified})
        response = fake_server(request)
        if request.url.path.endswith("/agent-connections"):
            body = response.json()
            body["connections"][0]["capabilities"] = ["task", "probe"]
            if verified and bad != "readback":
                body["connections"][0].update(state="ready", verified_at=verified)
            return httpx.Response(200, json=body)
        return response

    def launch(argv, **kwargs):
        launches.append((argv, kwargs))
        payload = json.loads(kwargs["input"])
        assert set(payload) == {"nonce"}
        assert kwargs["cwd"] == str(tmp_path)
        if bad == "timeout":
            raise subprocess.TimeoutExpired(argv, 1, stderr="SECRET")
        return SimpleNamespace(
            returncode=1 if bad == "exit" else 0,
            stdout=json.dumps({"ok": True, "nonce": "wrong" if bad is True else payload["nonce"]}),
            stderr="SECRET",
        )

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        config = connect.connect_local(
            client=client, **(options(tmp_path) | {"probe_command": "python -V"})
        )
        directory = tmp_path / "connections/robot"
        monkeypatch.setattr(connect.subprocess, "run", launch)
        # Avoid mocking Windows ACL checks, which use subprocess.run too.
        monkeypatch.setattr(connect, "check_private", lambda path: None)
        assert connect.doctor(directory, client=client)["state"] == "connected"
        assert not launches and not beats
        if bad == "readback":
            with pytest.raises(connect.SetupError):
                connect.doctor(directory, client=client, test=True)
            assert not (directory / "probe-receipt.json").exists()
            return
        result = connect.doctor(directory, client=client, test=True)
        assert "SECRET" not in json.dumps(result)
        if bad:
            assert result["state"] == "needs_setup"
            assert "probe_passed" not in beats
            assert not (directory / "probe-receipt.json").exists()
        else:
            assert result["state"] == "ready"
            assert beats == ["probe_passed"]
            receipt = json.loads((directory / "probe-receipt.json").read_text())
            nonce = json.loads(launches[0][1]["input"])["nonce"]
            assert receipt["nonce_sha256"] == hashlib.sha256(nonce.encode()).hexdigest()
            assert nonce not in json.dumps(receipt)
            assert connect._proof_matches(config, directory)
            assert not connect._proof_matches(config | {"cwd": "different"}, directory)
            assert connect.doctor(directory, client=client)["state"] == "ready"
            assert len(launches) == 1


def test_work_kinds_are_narrow_and_cwd_bound(tmp_path):
    connect = load()
    config = options(tmp_path) | {"profile": "chosen"}
    env = connect._environment(config, tmp_path)
    assert env["SLATE_WORK_KINDS"] == "task"
    config["adapter"] = "hermes-cli"
    assert connect._environment(config, tmp_path)["SLATE_WORK_KINDS"] == "task"
    config["adapter"] = "hermes-gateway"
    env = connect._environment(config, tmp_path)
    assert env["SLATE_WORK_KINDS"] == "task,chat,doc"
    assert env["HERMES_CWD"] == str(tmp_path)


def test_real_custom_probe_and_ready_heartbeat(connect, tmp_path, client, alex, todo, make_account):
    agent, _ = make_account()
    paired = client.post(
        "/api/v1/agent-connections/pairings",
        headers=alex,
        json={"account_id": agent["id"], "label": "Probe only", "list_ids": [todo["id"]]},
    ).json()
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        'print(json.dumps({"ok": True, "nonce": p["nonce"]}))\n'
    )
    config = connect.connect_local(
        client=client,
        **(
            options(tmp_path)
            | {"code": paired["code"], "probe_command": f'"{sys.executable}" "{probe}"'}
        ),
    )
    directory = tmp_path / "connections/robot"
    assert connect.doctor(directory, client=client, test=True)["state"] == "ready"
    proof = json.loads((directory / "probe-receipt.json").read_text())
    assert connect.run_local(directory, client=client, once=True) == 2
    rows = client.get("/api/v1/agent-connections?account_id=" + agent["id"], headers=alex).json()[
        "connections"
    ]
    row = next(r for r in rows if r["id"] == config["connection_id"])
    assert row["state"] == "ready"
    assert row["verified_at"] == proof["verified_at"]
    connect.check_private(directory / "probe-receipt.json")
