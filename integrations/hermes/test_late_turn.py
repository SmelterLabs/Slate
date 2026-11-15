"""A turn that outlives the adapter's watch: the budget counts silence, the marker it leaves
settles on that run's own status, and the answer the card never got is reported. Against the
fake gateway; never a live Hermes."""

from __future__ import annotations

import json

import httpx
import run_task
from test_hermes_adapter import ITEM, Clock, Gateway, slate_client, sse_body
from test_hermes_chat_adapter import Slate as ChatSlate
from test_hermes_chat_adapter import task as chat_task

REPLY = {"author_handle": "alex", "author_kind": "person", "body_md": "Which model was this?"}
DONE = "Sent it.\nSLATE: done"
ASKS = "Three questions for you.\nSLATE: blocked waiting_on alex"


class Runs(Gateway):
    """The fake gateway plus one run's status (GET /v1/runs/{run_id}) and /health/detailed.
    `stream` is what a turn's stream yields: (seconds that pass first, an event or None for a
    keepalive, or an exception to raise)."""

    def __init__(self, *a, clock=None, stream=None, **kw):
        super().__init__(*a, **kw)
        self.clock = clock or Clock()
        self.stream = [list(turn) for turn in stream or []]
        self.runs: dict[str, dict | None] = {}  # None: the gateway forgot the run
        self.active = 1  # another API run is always in flight
        self.run_route = True

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        parts = path.strip("/").split("/")
        if path == "/health/detailed":
            checks = {"background_queues": {"active_api_runs": self.active}}
            return httpx.Response(200, json={"readiness": {"checks": checks}, "pid": 100})
        if parts[:2] == ["v1", "runs"] and req.method == "GET":
            if not self.run_route:
                return httpx.Response(404, text="404: Not Found")
            state = self.runs.get(parts[2])
            if state is None:
                error = {"message": f"Run not found: {parts[2]}", "code": "run_not_found"}
                return httpx.Response(404, json={"error": error})
            return httpx.Response(200, json={"object": "hermes.run", "run_id": parts[2], **state})
        if path.endswith("/chat/stream") and self.stream:
            self.turns.append(json.loads(req.content))
            return httpx.Response(200, stream=Timed(self.clock, self.stream.pop(0)))
        return super().handle(req)


class Timed(httpx.SyncByteStream):
    def __init__(self, clock, steps):
        self.clock, self.steps = clock, steps

    def __iter__(self):
        for wait, step in self.steps:
            self.clock.t += wait
            if isinstance(step, Exception):
                raise step
            yield b": keepalive\n\n" if step is None else sse_body(step)


def go(gw, tmp_path, task=None, **env):
    lines: list[dict] = []
    settings = {
        "HERMES_STATE": str(tmp_path / "state.json"),
        "HERMES_TURN_TIMEOUT": "60",
        **env,
    }
    out = run_task.run(
        task or {"item": ITEM},
        settings,
        slate=slate_client(),
        hermes=gw.client(),
        emit=lines.append,
        sleep=gw.clock.sleep,
        clock=gw.clock,
    )
    return out, lines, settings


def strand(tmp_path, **state):
    """A turn the adapter stopped watching (its stream went quiet past the budget) while run
    `r1` was still going: the marker stays behind."""
    gw = Runs(stream=[[(0, ("run.started", {"run_id": "r1"})), (61, None)]])
    out, _, settings = go(gw, tmp_path)
    assert out["outcome"] == "blocked" and "unconfirmed" in out["reply"]
    gw.runs["r1"] = state or None
    gw.events = [("assistant.completed", {"content": DONE}), ("run.completed", {})]
    return gw, settings


# --- The budget counts silence, not the length of the turn --------------------------------------


def test_a_turn_that_keeps_working_past_the_budget_is_not_cut_off(tmp_path):
    working = [(40, ("tool.started", {"name": "terminal"})) for _ in range(4)]
    done = [(40, ("assistant.completed", {"content": DONE})), (0, ("run.completed", {}))]
    gw = Runs(stream=[[*working, *done]])
    out, _, _ = go(gw, tmp_path)
    assert out["outcome"] == "done" and gw.clock.t == 200  # over three times the 60 s budget


def test_a_turn_that_goes_silent_for_the_budget_is_unconfirmed(tmp_path):
    gw = Runs(stream=[[(0, ("run.started", {})), (30, None), (31, None)]])  # keepalives only
    out, _, _ = go(gw, tmp_path)
    assert out["outcome"] == "blocked" and "unconfirmed" in out["reply"]


def test_a_follow_up_turn_gets_the_whole_budget_again(tmp_path):
    first = [(50, ("assistant.completed", {"content": "Started."})), (0, ("run.completed", {}))]
    nudged = [(50, ("assistant.completed", {"content": DONE})), (0, ("run.completed", {}))]
    gw = Runs(stream=[first, nudged])
    out, _, _ = go(gw, tmp_path)
    assert out["outcome"] == "done" and len(gw.turns) == 2


# --- The marker names its run, and settles on that run alone -------------------------------------


def test_the_marker_names_the_run_it_was_watching(tmp_path):
    _, settings = strand(tmp_path, status="running")
    pending = run_task.load_pending(settings, "it1")
    assert pending["state"] == "unconfirmed_turn" and pending["run_id"] == "r1"


def test_a_run_still_going_keeps_blocking(tmp_path):
    gw, _ = strand(tmp_path, status="running")
    gw.active = 0  # even when the gateway counts nothing in flight
    out, _, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}, "reply": REPLY})
    assert out["outcome"] == "blocked" and "not been reconciled" in out["reply"]
    assert len(gw.turns) == 1


def test_a_finished_run_clears_the_marker_while_other_runs_are_in_flight(tmp_path):
    gw, settings = strand(tmp_path, status="cancelled")
    out, _, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}, "reply": REPLY})
    assert out["outcome"] == "done" and len(gw.turns) == 2
    assert run_task.load_pending(settings, "it1") is None


def test_a_run_the_gateway_forgot_is_over(tmp_path):
    gw, _ = strand(tmp_path)  # a gateway forgets a run only after it ended
    out, _, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}, "reply": REPLY})
    assert out["outcome"] == "done" and len(gw.turns) == 2


def test_a_gateway_without_run_status_proves_nothing(tmp_path):
    gw, _ = strand(tmp_path)
    gw.run_route = False  # an older gateway: its 404 is not "this run ended"
    out, _, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}, "reply": REPLY})
    assert out["outcome"] == "blocked" and len(gw.turns) == 1


# --- The answer the card never got ---------------------------------------------------------------


def test_run_now_reports_the_answer_that_finished_unwatched(tmp_path):
    gw, settings = strand(tmp_path, status="completed", output=ASKS)
    out, lines, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}})
    assert out == {
        "outcome": "blocked",
        "waiting_on": "alex",
        "reply": "Three questions for you.",
        "thread": {"ref": "sess-new"},
    }
    assert len(gw.turns) == 1  # nothing new was asked of the agent
    assert any("finished after" in (ln.get("note") or "") for ln in lines)
    assert run_task.load_pending(settings, "it1") is None


def test_a_new_message_gets_the_missed_answer_and_its_own(tmp_path):
    gw, _ = strand(tmp_path, status="completed", output=ASKS)
    gw.events = [("assistant.completed", {"content": "Opus.\nSLATE: done"}), ("run.completed", {})]
    out, _, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}, "reply": REPLY})
    assert out["outcome"] == "done" and len(gw.turns) == 2
    assert out["reply"] == "Three questions for you.\n\nOpus."
    assert run_task.MISSED not in gw.turns[1]["message"]


def test_an_answer_the_gateway_no_longer_holds_is_asked_for_again(tmp_path):
    gw, _ = strand(tmp_path)
    out, _, _ = go(gw, tmp_path, {"item": ITEM, "thread": {"ref": "sess-new"}, "reply": REPLY})
    assert out["outcome"] == "done"
    assert gw.turns[1]["message"].startswith(run_task.MISSED)
    assert "> Which model was this?" in gw.turns[1]["message"]


def test_a_lost_stream_is_settled_by_the_run_status(tmp_path):
    lost = httpx.ReadError("connection reset")
    gw = Runs(stream=[[(0, ("run.started", {"run_id": "r1"})), (0, lost)]])
    gw.runs["r1"] = {"status": "completed", "output": DONE}
    out, _, settings = go(gw, tmp_path)
    assert out["outcome"] == "done" and out["reply"] == "Sent it."
    assert run_task.load_pending(settings, "it1") is None


def test_a_lost_stream_whose_run_failed_is_blocked_and_retryable(tmp_path):
    lost = httpx.ReadError("connection reset")
    gw = Runs(stream=[[(0, ("run.started", {"run_id": "r1"})), (0, lost)]])
    gw.runs["r1"] = {"status": "cancelled"}
    out, _, settings = go(gw, tmp_path)
    assert out["outcome"] == "blocked" and "cancelled" in out["reply"]
    assert run_task.load_pending(settings, "it1") is None


# --- Chat turns settle the same way --------------------------------------------------------------


def chat(gw, tmp_path):
    return run_task.run(
        chat_task(),
        {
            "HERMES_STATE": str(tmp_path / "state.json"),
            "HERMES_API_URL": "http://hermes.test",
            "SLATE_URL": "http://slate.test",
            "HERMES_TURN_TIMEOUT": "60",
        },
        slate=ChatSlate().client(),
        hermes=gw.client(),
        emit=lambda line: None,
        sleep=gw.clock.sleep,
        clock=gw.clock,
    )


def test_a_chat_marker_clears_once_its_run_is_over(tmp_path):
    gw = Runs(stream=[[(0, ("run.started", {"run_id": "r1"})), (61, None)]])
    assert chat(gw, tmp_path)["outcome"] == "release"
    gw.runs["r1"] = {"status": "running"}
    gw.events = [("assistant.completed", {"content": DONE}), ("run.completed", {})]
    assert chat(gw, tmp_path)["outcome"] == "release" and len(gw.turns) == 1
    gw.runs["r1"] = {"status": "completed", "output": "An old answer.\nSLATE: done"}
    assert chat(gw, tmp_path) == {"outcome": "done", "reply": "Sent it."}
    assert len(gw.turns) == 2
