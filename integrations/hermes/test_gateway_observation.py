"""Gateway regressions: controlled streams and disposable state, never live runtimes."""

import json

import httpx
import pytest
from test_hermes_adapter import Gateway, go, run_task, sse_body
from test_hermes_chat_adapter import Slate, task


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("failure", ["health", "messages", 400, 401, 403, 404, 409])
def test_definitely_unsubmitted_failure_allows_explicit_retry(tmp_path, chat, failure):
    settings = {"HERMES_STATE": str(tmp_path / "state.json")}
    gw = Gateway(events=SUCCESS)
    original = gw.handle
    broken = True

    def handle(req):
        if broken and (
            (failure == "health" and req.url.path == "/health")
            or (failure == "messages" and req.url.path.endswith("/messages"))
        ):
            raise httpx.ConnectError("preflight down", request=req)
        if req.url.path.endswith("/chat/stream"):
            pending = (
                json.loads(
                    run_task.chat_session_path(settings, task()["chat"], "agent-one").read_text()
                )
                if chat
                else run_task.load_pending(settings, "it1")
            )
            assert pending  # atomic fence already persisted at POST
            if broken and isinstance(failure, int):
                gw.turns.append(json.loads(req.content))
                return httpx.Response(failure)
        return original(req)

    gw.handle = handle
    kwargs = {"task": task(), "slate": Slate().client()} if chat else {}
    out, _, _ = go(gw, env=settings, **kwargs)
    assert out["outcome"] != "done"
    refused = 1 if isinstance(failure, int) else 0
    assert len(gw.turns) == refused
    broken = False
    out, _, _ = go(gw, env=settings, **kwargs)
    assert out["outcome"] == "done"
    assert len(gw.turns) == refused + 1


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("failure", ["transport", 422, 500, 502])
def test_uncertain_submission_never_retries(tmp_path, chat, failure):
    settings = {"HERMES_STATE": str(tmp_path / "state.json")}
    gw = Gateway(events=SUCCESS)
    original = gw.handle

    def handle(req):
        if req.url.path.endswith("/chat/stream"):
            gw.turns.append(json.loads(req.content))
            if failure == "transport":
                raise httpx.ReadError("response lost after submit", request=req)
            return httpx.Response(failure)
        return original(req)

    gw.handle = handle
    kwargs = {"task": task(), "slate": Slate().client()} if chat else {}
    first, _, _ = go(gw, env=settings, **kwargs)
    gw.handle = original
    second, _, _ = go(gw, env=settings, **kwargs)
    assert first["outcome"] != "done" and second["outcome"] != "done"
    assert len(gw.turns) == 1


def test_approval_read_timeout_keeps_identity_and_blocks_retry(tmp_path):
    settings = {"HERMES_STATE": str(tmp_path / "state.json")}

    class TimeoutStream(httpx.SyncByteStream):
        def __iter__(self):
            yield sse_body(("approval.request", CONTROL))
            raise httpx.ReadTimeout("native UI has not answered")

    gw = Gateway()
    original = gw.handle

    def handle(req):
        if req.url.path.endswith("/chat/stream"):
            gw.turns.append(json.loads(req.content))
            return httpx.Response(200, stream=TimeoutStream())
        return original(req)

    gw.handle = handle
    first, _, _ = go(gw, env=settings)
    assert "unconfirmed" in first["reply"]
    assert run_task.load_pending(settings, "it1")["request_id"] == "p1"
    second, _, _ = go(gw, env=settings)
    assert second["outcome"] == "blocked" and len(gw.turns) == 1


def test_completed_approval_before_nudge_gets_fresh_observation_fence(tmp_path):
    settings = {"HERMES_STATE": str(tmp_path / "state.json")}
    gw = Gateway(
        script=[
            [
                ("approval.request", CONTROL),
                ("assistant.completed", {"content": "Still working"}),
                ("run.completed", {"run_id": "r1"}),
            ],
            [],
        ]
    )
    original = gw.handle

    def handle(req):
        if req.url.path.endswith("/chat/stream") and gw.turns:
            assert run_task.load_pending(settings, "it1")["state"] == "unconfirmed_turn"
        return original(req)

    gw.handle = handle
    out, _, _ = go(gw, env=settings)
    assert out["outcome"] == "blocked" and len(gw.turns) == 2


CONTROL = {
    "prompt_id": "p1",
    "run_id": "r1",
    "execution_generation": 7,
    "command": "PRIVATE_FAKE_COMMAND",
}
SUCCESS = [
    ("assistant.completed", {"content": "OK\nSLATE: done"}),
    ("run.completed", {"run_id": "r1"}),
]


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize(
    "terminal", ["run.completed", "run.failed", "run.cancelled", "failed-status", None]
)
def test_native_approval_keeps_observing_until_terminal(tmp_path, chat, terminal):
    settings = {"HERMES_STATE": str(tmp_path / "state.json")}
    payload = task() if chat else None
    path = run_task.chat_session_path(settings, payload["chat"], "agent-one") if chat else None
    lines = []
    reached_after_approval = False

    def stored():
        return json.loads(path.read_text()) if chat else run_task.load_pending(settings, "it1")

    class ControlledStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            nonlocal reached_after_approval
            assert stored()  # durable observation fence before submission
            yield sse_body(("approval.request", CONTROL))
            reached_after_approval = True
            assert not self.closed
            pending = stored()["pending_approval"] if chat else stored()
            assert pending["request_id"] == "p1" and pending["run_id"] == "r1"
            yield sse_body(SUCCESS[0])  # closing text alone must not clear control
            assert stored()
            if terminal == "failed-status":
                yield sse_body(("run.completed", {"run_id": "r1", "status": "failed"}))
            elif terminal:
                yield sse_body((terminal, {"run_id": "r1"}))
            yield sse_body(("done", {}))

        def close(self):
            self.closed = True

    stream = ControlledStream()
    gw = Gateway()
    original = gw.handle

    def handle(req):
        if req.url.path.endswith("/chat/stream"):
            gw.turns.append(json.loads(req.content))
            return httpx.Response(200, stream=stream)
        return original(req)

    gw.handle = handle
    out, lines, _ = go(gw, task=payload, env=settings, slate=Slate().client() if chat else None)
    assert reached_after_approval and stream.closed
    assert not gw.settled and len(gw.turns) == 1
    assert any("approval" in line.get("note", "").lower() for line in lines)
    assert "PRIVATE_FAKE_COMMAND" not in json.dumps(lines)
    assert out["outcome"] == (
        "done" if terminal == "run.completed" else "release" if chat else "blocked"
    )
    if terminal:
        assert (stored() == {"session_id": "sess-new"}) if chat else stored() is None
    else:
        pending = stored()["pending_approval"] if chat else stored()
        assert pending["request_id"] == "p1"
        gw.events = SUCCESS
        again, _, _ = go(gw, task=payload, env=settings, slate=Slate().client() if chat else None)
        assert again["outcome"] != "done" and len(gw.turns) == 1
