"""The run log: the lines a run posted while it worked are kept with the answer it leaves, and
read back from the comment."""

from __future__ import annotations

from conftest import API
from conftest import item_events as _events
from run_proofs import run_fields
from slate import liveactivity
from test_assigned_comments import _item
from test_work import _finish, _next


def _say(client, headers, item_id, *lines):
    r = client.post(
        f"{API}/work/{item_id}/activity",
        headers=headers,
        json=run_fields(client, headers, item_id, lines=list(lines)),
    )
    assert r.status_code == 204, r.text


def _comments(client, headers, item_id):
    return client.get(f"{API}/items/{item_id}/comments", headers=headers).json()


def test_a_runs_lines_are_kept_with_its_answer(client, alex, todo, make_account):
    liveactivity.live.reset()
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _say(client, ch, item["id"], "terminal: uv run pytest -q", "read_file: docs/board.md")
    _say(client, ch, item["id"], "Thinking: two tests fail")
    _finish(client, ch, item["id"], outcome="review", reply="Fixed both tests.")

    [answer] = _comments(client, alex, item["id"])
    assert answer["body_md"] == "Fixed both tests." and answer["run_steps"] == 3
    log = client.get(f"{API}/comments/{answer['id']}/run", headers=alex).json()
    assert [line["text"] for line in log["lines"]] == [
        "terminal: uv run pytest -q",
        "read_file: docs/board.md",
        "Thinking: two tests fail",
    ]
    assert log["account_id"] == agent["id"] and log["started_at"] and log["finished_at"]
    finished = _events(client, alex, item["id"], "item.work_finished")[-1]["after"]
    assert finished["steps"] == 3 and finished["comment_id"] == answer["id"]
    # The box is empty for the next run, and the activity feed's comment carries the count.
    assert client.get(f"{API}/work/{item['id']}/activity", headers=alex).json()["lines"] == []
    feed = client.get(f"{API}/items/{item['id']}/activity", headers=alex).json()
    assert next(e["comment"] for e in feed if e["kind"] == "comment")["run_steps"] == 3


def test_no_lines_or_no_answer_keeps_nothing(client, alex, todo, make_account):
    liveactivity.live.reset()
    _, ch = make_account("agent")
    quiet = _item(client, alex, todo["id"], title="Quiet", assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, quiet["id"], outcome="review", reply="Done without a word.")
    [answer] = _comments(client, alex, quiet["id"])
    assert answer["run_steps"] is None
    assert client.get(f"{API}/comments/{answer['id']}/run", headers=alex).status_code == 404

    silent = _item(client, alex, todo["id"], title="Silent", assignees=["agent"])
    _next(client, ch)
    _say(client, ch, silent["id"], "terminal: ls")
    _finish(client, ch, silent["id"], outcome="release", comment=False)
    assert _comments(client, alex, silent["id"]) == []
    assert "steps" not in _events(client, alex, silent["id"], "item.work_finished")[-1]["after"]


def test_each_run_keeps_its_own_log_and_deleting_the_answer_drops_it(
    client, alex, todo, make_account
):
    liveactivity.live.reset()
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _say(client, ch, item["id"], "first run")
    _finish(client, ch, item["id"], outcome="review", reply="One.")
    client.post(f"{API}/items/{item['id']}/run", headers=alex)
    _next(client, ch)
    _say(client, ch, item["id"], "second run", "still the second")
    _finish(client, ch, item["id"], outcome="review", reply="Two.")
    one, two = _comments(client, alex, item["id"])
    assert (one["run_steps"], two["run_steps"]) == (1, 2)
    log = client.get(f"{API}/comments/{two['id']}/run", headers=alex).json()
    assert [line["text"] for line in log["lines"]] == ["second run", "still the second"]

    assert client.delete(f"{API}/comments/{one['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/comments/{one['id']}/run", headers=alex).status_code == 404
