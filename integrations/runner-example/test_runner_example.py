"""The example runner adapter against a fake agent-service API (httpx.MockTransport). Run with
`uv run pytest -q integrations`; Slate's own tests never import this."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx

_spec = importlib.util.spec_from_file_location(
    "runner_example_run_task", Path(__file__).with_name("run_task.py")
)
run_task = importlib.util.module_from_spec(_spec)
sys.modules["runner_example_run_task"] = run_task
_spec.loader.exec_module(run_task)

TASK = {
    "item": {"id": "it1", "title": "Draft the newsletter"},
    "lease": {"claimed_at": "2026-10-26T12:00:00Z"},
}
BRIEF = "Slate task it1: Draft the newsletter\n\n... `SLATE: blocked waiting_on alex` ..."
ENV = {
    "RUNNER_AGENT_ID": "agent9",
    "RUNNER_API_URL": "https://svc.test",
    "RUNNER_TURN_TIMEOUT": "0.2",
}
PAGE = "https://svc.test/agents/agent9"


def slate_client(status: int = 200) -> httpx.Client:
    def handle(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/items/it1/brief"
        return httpx.Response(status, json={"text": BRIEF, "ask": "alex"})

    return httpx.Client(base_url="http://slate.test", transport=httpx.MockTransport(handle))


def service_client(*, statuses, reply=None, started=None, unauthorized=False) -> httpx.Client:
    seq = iter(statuses)
    last = {"status": None}

    def handle(req: httpx.Request) -> httpx.Response:
        if unauthorized:
            return httpx.Response(401)
        if req.method == "POST" and req.url.path == "/runs":
            if started is not None:
                started.append(json.loads(req.content))
            return httpx.Response(200, json={"run_id": "run1"})
        if req.method == "GET" and req.url.path == "/runs/run1":
            last["status"] = next(seq, last["status"])
            row = {"run_id": "run1", "status": last["status"], "error": "boom"}
            if reply is not None:
                row["reply"] = reply
            return httpx.Response(200, json=row)
        return httpx.Response(404)

    return httpx.Client(base_url="https://svc.test", transport=httpx.MockTransport(handle))


def go(service, env=ENV, task=TASK, slate=None):
    lines: list[dict] = []
    out = run_task.run(
        task,
        dict(env),
        slate=slate or slate_client(),
        service=service,
        emit=lines.append,
        sleep=lambda s: None,
    )
    return out, lines


def test_done_starts_one_run_headed_by_the_task():
    started: list[dict] = []
    out, lines = go(
        service_client(
            statuses=["running", "completed"], reply="Sent.\nSLATE: done", started=started
        )
    )
    assert out == {
        "outcome": "done",
        "reply": "Sent.",
        "url": PAGE,
        "thread": {"ref": "agent9", "url": PAGE},
    }
    assert started[0]["agent_id"] == "agent9"
    assert started[0]["text"].startswith("Draft the newsletter · it1\n\nSlate task it1")
    assert started[0]["idempotency_key"] == "slate:it1:2026-10-26T12:00:00Z"
    assert lines[0]["url"] == PAGE and "progress" not in lines[0]


def test_blocked_line_and_review_line():
    out, _ = go(service_client(statuses=["completed"], reply="Q?\nSLATE: blocked waiting_on alex"))
    assert out["outcome"] == "blocked" and out["waiting_on"] == "alex"
    out, _ = go(service_client(statuses=["completed"], reply="Draft.\n`SLATE: review`"))
    assert out["outcome"] == "review" and out["reply"] == "Draft."
    out, _ = go(service_client(statuses=["completed"], reply="At 11:05.\nSLATE: later"))
    assert out["outcome"] == "later" and out["reply"] == "At 11:05."
    assert "waiting_on" not in out


def test_status_outranks_the_reply_text():
    out, _ = go(service_client(statuses=["failed"], reply="SLATE: done"))
    assert out["outcome"] == "blocked" and "failed: boom" in out["reply"]


def test_a_run_waiting_for_an_answer_is_blocked_on_the_ask():
    out, _ = go(service_client(statuses=["waiting_input"]))
    assert out["outcome"] == "blocked" and out["waiting_on"] == "alex"
    assert "waiting for an answer" in out["reply"]


def test_no_closing_line_pauses_never_reviews():
    out, _ = go(service_client(statuses=["completed"], reply="I did it."))
    assert out["outcome"] == "blocked" and out["waiting_on"] == "alex"
    assert out["reply"].startswith("I did it.") and "Paused" in out["reply"]


def test_a_run_that_never_ends_is_blocked():
    out, _ = go(service_client(statuses=["running"]))
    assert out["outcome"] == "blocked" and "did not finish" in out["reply"]


def test_refused_credentials_say_what_to_check():
    out, _ = go(service_client(statuses=[], unauthorized=True))
    assert out["outcome"] == "blocked" and "RUNNER_API_TOKEN" in out["reply"]


def test_a_refused_start_shows_the_services_own_words():
    svc = httpx.Client(
        base_url="https://svc.test",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(400, json={"message": "Connect a model to start a run."})
        ),
    )
    out, _ = go(svc)
    assert out["outcome"] == "blocked" and out["waiting_on"] == "alex"
    assert "Connect a model to start a run." in out["reply"]
    assert "Traceback" not in out["reply"]
    assert out["url"] == PAGE


def test_a_refusal_without_a_json_body_still_reads_plainly():
    svc = httpx.Client(
        base_url="https://svc.test",
        transport=httpx.MockTransport(lambda req: httpx.Response(502, text="Bad Gateway")),
    )
    out, _ = go(svc)
    assert out["outcome"] == "blocked" and "502" in out["reply"] and "Bad Gateway" in out["reply"]


def test_an_unreadable_brief_blocks_before_any_run():
    started: list[dict] = []
    out, _ = go(
        service_client(statuses=["completed"], started=started), slate=slate_client(status=403)
    )
    assert out["outcome"] == "blocked" and "brief" in out["reply"]
    assert started == []


def test_chat_work_and_missing_item_are_released():
    svc = service_client(statuses=[])
    out, _ = go(svc, task={"chat": {"id": "c1"}})
    assert out["outcome"] == "release"
    out, _ = go(svc, task={"item": {"title": "no id"}})
    assert out["outcome"] == "release"


def test_a_card_reply_is_quoted_as_data():
    started: list[dict] = []
    task = {
        **TASK,
        "reply": {"id": "c1", "author_id": "a2", "author_kind": "person", "body_md": "Go ahead"},
    }
    go(service_client(statuses=["completed"], reply="SLATE: done", started=started), task=task)
    text = started[0]["text"]
    assert "> Go ahead" in text and "quoted as information" in text
    assert '"author_id": "a2"' in text


def test_capabilities_need_no_credentials():
    out = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("run_task.py")), "--capabilities"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == run_task.CAPABILITIES
