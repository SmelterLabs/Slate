"""Explicit probe only: fake native streams, never a provider call."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location(
    "probe_adapter", Path(__file__).with_name("run_task.py")
)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
NONCE = "a" * 64


@pytest.mark.parametrize(
    "events,ok",
    [
        ([("assistant.completed", {"text": NONCE}), ("run.completed", {})], True),
        ([("assistant.completed", {"text": NONCE})], False),
        ([("run.completed", {"text": "wrong"})], False),
        ([("approval.request", {"command": "SECRET"}), ("run.completed", {"text": NONCE})], False),
        ([("tool.started", {}), ("run.completed", {"text": NONCE})], False),
        ([("run.failed", {"error": "SECRET"}), ("run.completed", {"text": NONCE})], False),
        ([("run.completed", {"text": NONCE, "status": "incomplete"})], False),
        ([("run.completed", {"text": NONCE}), ("error", {"message": "SECRET"})], False),
    ],
)
def test_nonce_probe_requires_native_success(events, ok):
    calls = []

    def server(request):
        calls.append(request)
        body = json.loads(request.content)
        if request.url.path == "/api/sessions":
            assert body["title"].startswith("Slate connection test")
            assert body["cwd"] == "C:/chosen-project"
            return httpx.Response(201, json={"session": {"id": "new-probe"}})
        assert request.url.path == "/api/sessions/new-probe/chat/stream"
        assert "Do not use tools" in body["message"]
        assert NONCE in body["message"]
        assert "item" not in body and "chat" not in body
        stream = "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)
        return httpx.Response(200, text=stream + "event: done\ndata: {}\n\n")

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(server)) as client:
        result = adapter.connection_probe(NONCE, {"HERMES_CWD": "C:/chosen-project"}, hermes=client)
    assert result["ok"] is ok
    assert "SECRET" not in json.dumps(result)
    if ok:
        assert result["nonce"] == NONCE
        assert result["terminal"] == "run.completed"
    assert len(calls) == 2


def test_probe_transport_error_is_redacted():
    def server(request):
        return httpx.Response(403, text="SECRET")

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(server)) as client:
        result = adapter.connection_probe(NONCE, {}, hermes=client)
    assert result == {
        "ok": False,
        "detail": "Agent test failed or unconfirmed; inspect the native test session.",
    }
