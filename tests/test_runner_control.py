"""The runner while a task runs: a new message and Stop reach the command through its control
file, a `seen` line marks the message on the card, and a command that ignores Stop is ended."""

from __future__ import annotations

import sys
import threading
import time

from conftest import API
from conftest import item_events as _events
from test_clients import _assign, _runner

# Reads the control file: marks each message seen, and ends with its own result on Stop.
COOPERATIVE = """
import json, os, sys, time
json.load(sys.stdin)
path, seen = os.environ["SLATE_CONTROL"], []
print(json.dumps({"note": "Working"}), flush=True)
end = time.time() + 30
while time.time() < end:
    for line in open(path, encoding="utf-8"):
        obj = json.loads(line)
        message = obj.get("message")
        if message and message["id"] not in seen:
            seen.append(message["id"])
            print(json.dumps({"seen": [message["id"]]}), flush=True)
        if obj.get("stop"):
            print(json.dumps({"outcome": "blocked", "waiting_on": obj["stop"]["by"],
                              "reply": "Stopped after reading %d message(s)" % len(seen)}))
            sys.exit(0)
    time.sleep(0.05)
sys.exit(3)
"""

DEAF = """
import json, sys, time
json.load(sys.stdin)
print(json.dumps({"note": "Working"}), flush=True)
time.sleep(60)
"""


def _start(client, h, tmp_path, item_id, script):
    """Run the command in a thread. The test client is not shared across threads safely, so
    the test never calls Slate while the runner might: it waits on the runner's own heartbeats
    (`beats`: one entry per heartbeat that carried something) instead of polling the API."""
    path = tmp_path / "command.py"
    path.write_text(script, encoding="utf-8")
    runner = _runner(client, h, tmp_path, f'"{sys.executable}" "{path}"')
    beats: list[dict] = []
    real = runner.heartbeat

    def heartbeat(item, **body):
        real(item, **body)
        if any(v is not None for v in body.values()):
            beats.append(body)

    runner.heartbeat = heartbeat
    done: list[int] = []
    thread = threading.Thread(target=lambda: done.append(runner.once(item_id)), daemon=True)
    thread.start()
    _wait(lambda: any(b.get("note") == "Working" for b in beats), "the command to start")
    return runner, thread, done, beats


def _wait(check, what, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _control(runner, item_id, type_):
    """What the agent stream would send: the runner heartbeats at once."""
    runner.on_message({"event": "control", "id": None, "data": {"item_id": item_id, "type": type_}})


def test_a_message_then_stop_reach_the_command_and_show_on_the_card(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo, title="Draft the notice", who=("agent", "alex"))
    runner, thread, done, beats = _start(client, h, tmp_path, item["id"], COOPERATIVE)

    new = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Use the short version", "assignee": "agent"},
        headers=alex,
    ).json()
    _control(runner, item["id"], "comment.assigned")
    _wait(lambda: any(b.get("seen") == [new["id"]] for b in beats), "the message to be seen")
    assert len(_events(client, alex, item["id"], "comment.seen")) == 1
    rows = client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    assert next(c for c in rows if c["id"] == new["id"])["seen_at"] is not None

    assert client.post(f"{API}/items/{item['id']}/stop", headers=alex).status_code == 202
    _control(runner, item["id"], "item.stop_requested")
    thread.join(20)
    assert done[0] == 1
    saved = runner._records[item["id"]]["result"]
    assert saved["outcome"] == "answered"
    assert saved["reply"] == "Stopped after reading 1 message(s)"
    assert "waiting_on" not in saved
    assert runner._control == {}  # the control file is gone with the run
    # The message it read is settled: nothing is left to run.
    assert runner.once() == 2


def test_a_command_that_ignores_stop_is_ended_and_the_card_says_who_stopped_it(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    monkeypatch.setattr("slate_runner.STOP_WAIT", 0.3)
    _, h = make_account("agent")
    item = _assign(client, alex, todo, title="Crawl the archive", who=("agent", "alex"))
    runner, thread, done, beats = _start(client, h, tmp_path, item["id"], DEAF)

    assert client.post(f"{API}/items/{item['id']}/stop", headers=alex).status_code == 202
    _control(runner, item["id"], "item.stop_requested")
    thread.join(30)
    assert done[0] == 1
    saved = runner._records[item["id"]]["result"]
    assert saved["outcome"] == "answered"
    assert saved["reply"].startswith("Stopped at @alex's request")
    assert "waiting_on" not in saved
