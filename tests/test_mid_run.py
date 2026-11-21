"""While an agent works on a card: a person's new message reaches the running agent (the
heartbeat hands it over, the agent marks it seen, the finish settles it), and a person can ask
the run to stop."""

from __future__ import annotations

from conftest import API
from conftest import item_events as _events
from run_proofs import run_fields
from test_assigned_comments import _comment, _item
from test_work import _finish, _next


def _beat(client, headers, item_id, **body):
    r = client.post(
        f"{API}/work/{item_id}/heartbeat",
        headers=headers,
        json=run_fields(client, headers, item_id, **body),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _comments(client, headers, item_id):
    rows = client.get(f"{API}/items/{item_id}/comments", headers=headers).json()
    return {c["id"]: c for c in rows}


def test_a_message_sent_mid_run_is_handed_over_marked_seen_and_settled(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    first = _comment(client, alex, item["id"], "Start with the report", assignee="agent")
    assert _next(client, ch)["asks"][0]["id"] == first["id"]
    quiet = _beat(client, ch, item["id"])
    assert quiet["messages"] == [] and quiet["stop"] is None

    new = _comment(client, alex, item["id"], "Also check the totals", assignee="agent")
    got = _beat(client, ch, item["id"])
    assert [m["id"] for m in got["messages"]] == [new["id"]]
    assert got["messages"][0]["body_md"] == "Also check the totals"
    assert got["messages"][0]["author_kind"] == "person"
    assert _comments(client, alex, item["id"])[new["id"]]["seen_at"] is None

    after = _beat(client, ch, item["id"], seen=[new["id"]])
    assert after["messages"] == []
    row = _comments(client, alex, item["id"])[new["id"]]
    assert row["seen_at"] is not None and row["seen_by_id"] == agent["id"]
    seen = _events(client, alex, item["id"], "comment.seen")
    assert len(seen) == 1 and seen[0]["subject_id"] == new["id"]
    assert seen[0]["actor_id"] == agent["id"]
    # Saying it again records nothing.
    _beat(client, ch, item["id"], seen=[new["id"]])
    assert len(_events(client, alex, item["id"], "comment.seen")) == 1

    # The finish settles the message it read: it is not handed over again as a new turn.
    _finish(client, ch, item["id"], outcome="review", reply="Report and totals done")
    assert _next(client, ch)["item"] is None


def test_a_message_the_run_never_saw_is_still_its_next_turn(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    one = _comment(client, alex, item["id"], "One", assignee="agent")
    two = _comment(client, alex, item["id"], "Two", assignee="agent")
    assert [m["id"] for m in _beat(client, ch, item["id"])["messages"]] == [one["id"], two["id"]]
    # Only the later one is marked: the earlier one was never read, so nothing is settled past it.
    left = _beat(client, ch, item["id"], seen=[two["id"]])
    assert [m["id"] for m in left["messages"]] == [one["id"]]
    _finish(client, ch, item["id"], outcome="review", reply="Done")
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]
    assert [a["id"] for a in got["asks"]] == [one["id"], two["id"]]


def test_only_this_runs_own_messages_can_be_marked_seen(client, alex, todo, make_account):
    _, ch = make_account("agent")
    make_account("other")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    before = _comment(client, alex, item["id"], "Before the run", assignee="agent")
    _next(client, ch)
    theirs = _comment(client, alex, item["id"], "For someone else", assignee="other")
    plain = _comment(client, alex, item["id"], "A note to self")
    _beat(client, ch, item["id"], seen=[before["id"], theirs["id"], plain["id"], "nope"])
    assert _events(client, alex, item["id"], "comment.seen") == []


def test_stop_reaches_the_running_agent_and_only_a_running_one(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.post(f"{API}/items/{item['id']}/stop", headers=alex)
    assert r.status_code == 409 and "working" in r.json()["detail"]

    _next(client, ch)
    r = client.post(f"{API}/items/{item['id']}/stop", headers=alex)
    assert r.status_code == 202, r.text
    ev = r.json()
    assert ev["type"] == "item.stop_requested" and ev["after"] == {"for": [agent["id"]]}
    stop = _beat(client, ch, item["id"])["stop"]
    assert stop["by"] == "alex" and stop["at"]
    # Pressing it twice is one request.
    assert client.post(f"{API}/items/{item['id']}/stop", headers=alex).json()["id"] == ev["id"]
    assert len(_events(client, alex, item["id"], "item.stop_requested")) == 1

    _finish(client, ch, item["id"], outcome="answered", reply="Stopped as asked")
    assert client.post(f"{API}/items/{item['id']}/stop", headers=alex).status_code == 409
    # The next run starts clean: the old stop is not carried into it.
    client.post(f"{API}/items/{item['id']}/run", headers=alex)
    assert _next(client, ch)["item"]["id"] == item["id"]
    assert _beat(client, ch, item["id"])["stop"] is None


def test_stop_settles_what_was_sent_before_it_and_a_later_message_carries_on(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _comment(client, alex, item["id"], "Never read", assignee="agent")
    client.post(f"{API}/items/{item['id']}/stop", headers=alex)
    _finish(client, ch, item["id"], outcome="answered", reply="Stopped")
    assert _next(client, ch)["item"] is None  # the unread message does not restart the run
    after = _comment(client, alex, item["id"], "Carry on, but skip step 2", assignee="agent")
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"] and got["reply"]["id"] == after["id"]


def test_stop_and_the_activity_feed(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    new = _comment(client, alex, item["id"], "Mid-run", assignee="agent")
    _beat(client, ch, item["id"], seen=[new["id"]])
    client.post(f"{API}/items/{item['id']}/stop", headers=alex)
    feed = client.get(f"{API}/items/{item['id']}/activity", headers=alex).json()
    kinds = [e["event"]["type"] for e in feed if e["kind"] == "event"]
    assert "item.stop_requested" in kinds
    assert "comment.seen" not in kinds  # shown on the comment itself
    shown = next(e["comment"] for e in feed if e["kind"] == "comment")
    assert shown["seen_at"] is not None
