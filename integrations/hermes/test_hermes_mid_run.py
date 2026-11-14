"""The Hermes adapter while a card turn runs: a new message is steered into the live run and
reported seen, one the run never read gets a turn of its own, and Stop stops the run. Against
the fake gateway; never a live Hermes."""

from __future__ import annotations

import json

import httpx
from test_hermes_adapter import ITEM, Gateway, env_with, go, state_file  # noqa: F401

MESSAGE = {
    "id": "c9",
    "author_handle": "alex",
    "author_kind": "person",
    "body_md": "Use the short version",
}
DONE = [
    ("run.started", {"run_id": "run_1"}),
    ("assistant.completed", {"run_id": "run_1", "content": "Sent it.\nSLATE: done"}),
    ("run.completed", {"run_id": "run_1"}),
]


class Live(Gateway):
    """The fake gateway plus the run controls: POST /v1/runs/{run_id}/steer|stop."""

    def __init__(self, *a, steer_status=200, **kw):
        super().__init__(*a, **kw)
        self.steer_status = steer_status
        self.controls: list[tuple[str, str, dict]] = []

    def handle(self, req: httpx.Request) -> httpx.Response:
        parts = req.url.path.strip("/").split("/")
        if parts[:2] == ["v1", "runs"]:
            self.controls.append((parts[2], parts[3], json.loads(req.content)))
            return httpx.Response(self.steer_status if parts[3] == "steer" else 200, json={})
        return super().handle(req)


def control_file(tmp_path, *rows):
    path = tmp_path / "control.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(path)


def test_a_new_message_is_steered_into_the_live_run_and_reported_seen(state_file, tmp_path):  # noqa: F811
    gw = Live(events=DONE)
    env = env_with(state_file, SLATE_CONTROL=control_file(tmp_path, {"message": MESSAGE}))
    out, lines, _ = go(gw, env=env)
    assert out["outcome"] == "done"
    [(run_id, what, body)] = gw.controls
    assert (run_id, what) == ("run_1", "steer")
    assert "> Use the short version" in body["text"] and '"author_handle": "alex"' in body["text"]
    assert {"seen": ["c9"], "note": "Passed a new message to Hermes"} in lines
    assert len(gw.turns) == 1  # the live run read it: no extra turn


def test_a_message_the_run_would_not_take_gets_its_own_turn(state_file, tmp_path):  # noqa: F811
    again = [
        ("run.started", {"run_id": "run_2"}),
        ("assistant.completed", {"run_id": "run_2", "content": "Shortened.\nSLATE: review"}),
        ("run.completed", {"run_id": "run_2"}),
    ]
    gw = Live(script=[DONE, again], steer_status=409)
    env = env_with(state_file, SLATE_CONTROL=control_file(tmp_path, {"message": MESSAGE}))
    out, lines, _ = go(gw, env=env)
    assert out["outcome"] == "review" and out["reply"] == "Shortened."
    assert len(gw.turns) == 2 and "> Use the short version" in gw.turns[1]["message"]
    assert {"seen": ["c9"]} in lines


def test_a_steer_the_run_ended_without_reading_is_sent_again_as_a_turn(state_file, tmp_path):  # noqa: F811
    first = [
        ("run.started", {"run_id": "run_1"}),
        ("assistant.completed", {"run_id": "run_1", "content": "Sent it.\nSLATE: done"}),
        ("run.completed", {"run_id": "run_1", "pending_steer": "..."}),
    ]
    again = [
        ("assistant.completed", {"run_id": "run_2", "content": "Shortened.\nSLATE: done"}),
        ("run.completed", {"run_id": "run_2"}),
    ]
    gw = Live(script=[first, again])
    env = env_with(state_file, SLATE_CONTROL=control_file(tmp_path, {"message": MESSAGE}))
    out, _, _ = go(gw, env=env)
    assert out["reply"] == "Shortened."
    assert [c[1] for c in gw.controls] == ["steer"]
    assert len(gw.turns) == 2 and "> Use the short version" in gw.turns[1]["message"]


def test_stop_stops_the_hermes_run_and_the_card_waits_on_who_asked(state_file, tmp_path):  # noqa: F811
    gw = Live(
        events=[
            ("run.started", {"run_id": "run_1"}),
            ("assistant.completed", {"run_id": "run_1", "content": ""}),
            ("run.cancelled", {"run_id": "run_1"}),
        ]
    )
    env = env_with(
        state_file,
        SLATE_CONTROL=control_file(tmp_path, {"message": MESSAGE}, {"stop": {"by": "alex"}}),
    )
    out, lines, _ = go(gw, env=env)
    assert gw.controls == [("run_1", "stop", {})]  # stopping wins: nothing is steered
    assert out["outcome"] == "blocked" and out["waiting_on"] == "alex"
    assert out["reply"].startswith("Stopped at @alex's request")
    assert out["thread"] == {"ref": "sess-new"}
    assert len(gw.turns) == 1
    assert not [line for line in lines if "seen" in line]  # the unread message stays a turn
    assert not state_file.exists() or "unconfirmed_turn" not in state_file.read_text()


def test_without_a_control_file_nothing_changes(state_file):  # noqa: F811
    gw = Live(events=DONE)
    out, _, _ = go(gw, env=env_with(state_file))
    assert out["outcome"] == "done" and gw.controls == [] and len(gw.turns) == 1
