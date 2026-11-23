"""Finishing a turn settles only the Asks it took, not the entire conversation."""

from datetime import timedelta

import pytest

from conftest import API, item_events
from run_proofs import run_fields
from slate.models import utcnow
from slate.service import due, work
from test_assigned_comments import _comment, _item
from test_mid_run import _beat
from test_work import _finish, _next


def _asks(client, headers, item):
    return {
        c["id"]: c for c in client.get(f"{API}/items/{item['id']}/comments", headers=headers).json()
    }


@pytest.mark.parametrize(("assigned", "mid_run"), [(False, False), (True, False), (True, True)])
def test_retry_takes_the_original_asks(client, alex, todo, make_account, db, assigned, mid_run):
    agent, ch = make_account("my-agent")
    assignees = ["my-agent"] if assigned else ["alex"]
    item = _item(client, alex, todo["id"], assignees=assignees)
    item_id = item["id"]
    first = _comment(client, alex, item_id, "Check the report", assignee="my-agent")
    assert _next(client, ch)["reply"]["id"] == first["id"]
    ask_ids = [first["id"]]
    if mid_run:
        followup = _comment(client, alex, item_id, "Also check the totals", assignee="my-agent")
        ask_ids.append(followup["id"])
        assert [message["id"] for message in _beat(client, ch, item_id)["messages"]] == [
            followup["id"]
        ]
        assert _beat(client, ch, item_id, seen=[followup["id"]])["messages"] == []
    _finish(client, ch, item_id, outcome="failed", note="Connection refused")
    assert _next(client, ch)["item"] is None

    assert due.retry_failed_runs(db, now=utcnow() + timedelta(minutes=6)) == 1
    retry = _next(client, ch)
    assert retry["item"]["id"] == item_id
    assert retry["reply"]["id"] == ask_ids[-1]
    assert [ask["id"] for ask in retry["asks"]] == ask_ids
    finished = _finish(client, ch, item_id, outcome="answered", reply="The report checks out")
    assert finished["item"]["assignees"] == item["assignees"]
    rows = _asks(client, alex, item)
    assert all(rows[ask_id]["resolved_by_id"] == agent["id"] for ask_id in ask_ids)
    assert _next(client, ch)["item"] is None


def test_third_failure_gives_up_once_and_keeps_the_ask_pending(
    client, alex, todo, make_account, db
):
    agent, ch = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    item_id = item["id"]
    ask = _comment(client, alex, item_id, "Check the report", assignee="my-agent")
    for attempt, delay in enumerate((6, 31, 60), start=1):
        assert _next(client, ch)["item"]["id"] == item_id
        _finish(client, ch, item_id, outcome="failed", note="Connection refused")
        assert _next(client, ch)["item"] is None
        assert due.retry_failed_runs(db, now=utcnow() + timedelta(minutes=delay)) == int(
            attempt < 3
        )

    assert len(item_events(client, alex, item_id, "item.run_failed")) == 1
    assert _asks(client, alex, item)[ask["id"]]["resolved_at"] is None
    assert [pending.id for pending in work.pending_asks(db, agent["id"])] == [ask["id"]]
    assert _next(client, ch)["item"] is None


def test_a_new_ask_still_wakes_a_failed_card(client, alex, todo, make_account):
    _, ch = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    item_id = item["id"]
    first = _comment(client, alex, item_id, "Check the report", assignee="my-agent")
    assert _next(client, ch)["item"]["id"] == item_id
    _finish(client, ch, item_id, outcome="failed", note="Connection refused")

    followup = _comment(client, alex, item_id, "Use the revised report", assignee="my-agent")
    assert [ask["id"] for ask in _next(client, ch)["asks"]] == [first["id"], followup["id"]]


@pytest.mark.parametrize("assigned", [False, True])
def test_done_settles_taken_agent_asks_only(client, alex, todo, make_account, assigned):
    agent, ch = make_account("agent")
    person, _ = make_account("person", kind="person")
    item = _item(client, alex, todo["id"], assignees=["agent"] if assigned else [])
    old = _comment(client, alex, item["id"], "First question", assignee="agent")
    human = _comment(client, alex, item["id"], "Human decision", assignee="person")
    assert _next(client, ch)["asks"][0]["id"] == old["id"]
    new = _comment(client, alex, item["id"], "New question during run", assignee="agent")
    # Progress must not expand the turn's settlement boundary.
    assert client.post(
        f"{API}/work/{item['id']}/heartbeat",
        headers=ch,
        json=run_fields(client, ch, item["id"], **{"note": "Progress"}),
    ).is_success
    _finish(client, ch, item["id"], outcome="done", reply="First answer")
    rows = _asks(client, alex, item)
    assert rows[old["id"]]["resolved_by_id"] == agent["id"]  # the turn answered it
    assert rows[new["id"]]["resolved_at"] is None
    assert rows[human["id"]]["resolved_at"] is None
    got = _next(client, ch)
    assert [a["id"] for a in got["asks"]] == [new["id"]]
    _finish(client, ch, item["id"], outcome="done", comment=False)
    assert _next(client, ch)["item"] is None
    assert person["id"] in client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"]


@pytest.mark.parametrize("outcome", ["review", "ask", "blocked", "release"])
@pytest.mark.parametrize("assigned", [False, True])
def test_non_done_finishes_settle_the_turn_without_repeating_it(
    client, alex, todo, make_account, outcome, assigned
):
    """Every finish but failed and release answers the Asks the turn took; release leaves them
    open (visible). Either way the same Ask does not start the turn again."""
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"] if assigned else [])
    ask = _comment(client, alex, item["id"], "Please check", assignee="agent")
    _next(client, ch)
    options = {
        "ask": {"waiting_on": "alex", "question": "Who checks it?"},
        "review": {"status": "Check the summary"},
    }.get(outcome, {})
    _finish(client, ch, item["id"], outcome=outcome, reply="Needs a person", **options)
    released = outcome == "release"
    assert (_asks(client, alex, item)[ask["id"]]["resolved_at"] is None) == released
    assert _next(client, ch)["item"] is None
    asked = [i["id"] for i in client.get(f"{API}/today", headers=ch).json()["asked"]]
    assert (item["id"] in asked) == released
    new = _comment(client, alex, item["id"], "Continue with this answer", assignee="agent")
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]
    assert [a["id"] for a in got["asks"]] == [new["id"]]
    assert got["reply"]["id"] == new["id"]


def test_reassigned_ask_and_mid_turn_report_are_not_swallowed(client, alex, todo, make_account):
    _, ch = make_account("agent")
    make_account("other", kind="person")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    ask = _comment(client, alex, item["id"], "First", assignee="agent")
    _next(client, ch)
    assert client.post(
        f"{API}/items/{item['id']}/report",
        headers=ch,
        json=run_fields(
            client, ch, item["id"], **{"state": "review", "status": "Check the result"}
        ),
    ).is_success
    assert client.patch(
        f"{API}/comments/{ask['id']}", headers=alex, json={"assignee": "other"}
    ).is_success
    assert client.patch(
        f"{API}/comments/{ask['id']}", headers=alex, json={"assignee": "agent"}
    ).is_success
    assert client.post(
        f"{API}/work/{item['id']}/heartbeat",
        headers=ch,
        json=run_fields(client, ch, item["id"], **{"note": "Still working"}),
    ).is_success
    _finish(client, ch, item["id"], outcome="done")
    assert _asks(client, alex, item)[ask["id"]]["resolved_at"] is None
    assert _next(client, ch)["item"]["id"] == item["id"]


def test_human_finish_never_resolves_asks(client, alex, todo, make_account):
    _, ph = make_account("person", kind="person")
    item = _item(client, alex, todo["id"])
    ask = _comment(client, alex, item["id"], "Decision", assignee="person")
    _next(client, ph)
    _finish(client, ph, item["id"], outcome="done", reply="A reply is not resolution")
    assert _asks(client, alex, item)[ask["id"]]["resolved_at"] is None


def test_asked_only_blocked_on_a_third_person_is_just_an_answer(client, alex, todo, make_account):
    """An agent asked on someone else's card must not finish blocked waiting on a
    third person; that would put a red "Waiting on ..." on a card it does not own. An agent only
    asked something can wait on the person who asked it, nobody else: otherwise its answer is a
    plain answer, with no badge and no waiting-on."""
    _, gh = make_account("agent2")
    make_account("member", kind="person")
    item = _item(client, alex, todo["id"], assignees=["member"])
    _comment(
        client, alex, item["id"], "@member is the second report incomplete?", assignee="agent2"
    )
    _next(client, gh)
    _finish(
        client, gh, item["id"], outcome="blocked", waiting_on="member", reply="That is for Member."
    )
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["agent"] is None
    assert got["waiting_on_account_id"] is None
    bodies = [
        c["body_md"] for c in client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    ]
    assert "That is for Member." in bodies


def test_asked_only_ask_of_a_third_person_is_just_an_answer(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    make_account("member", kind="person")
    item = _item(client, alex, todo["id"], assignees=["member"])
    _comment(client, alex, item["id"], "Is the report complete?", assignee="agent2")
    _next(client, gh)
    _finish(client, gh, item["id"], outcome="ask", waiting_on="member", question="Member, done?")
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["agent"] is None and got["waiting_on_account_id"] is None and got["ask"] is None
    bodies = [
        c["body_md"] for c in client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    ]
    assert "Member, done?" in bodies


def test_asked_only_blocked_on_the_asker_still_waits(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"])
    _comment(client, alex, item["id"], "Which caterer?", assignee="agent2")
    _next(client, gh)
    _finish(client, gh, item["id"], outcome="ask", waiting_on="alex", question="Budget?")
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["agent"]["state"] == "blocked"
    assert got["waiting_on_account_id"] is not None


@pytest.mark.parametrize("how", ["resolve", "delete"])
def test_closing_an_agents_question_unanswered_stops_the_wait(
    client, alex, todo, make_account, how
):
    _, ch = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    item_id = item["id"]
    assert _next(client, ch)["item"]["id"] == item_id
    _finish(client, ch, item_id, outcome="ask", waiting_on="alex", question="Which one?")
    asked = next(c for c in _asks(client, alex, item).values() if c["ask"])
    assert client.get(f"{API}/items/{item_id}", headers=alex).json()["waiting_on_account_id"]

    if how == "resolve":
        r = client.post(f"{API}/comments/{asked['id']}/resolve", headers=alex)
    else:
        r = client.delete(f"{API}/comments/{asked['id']}", headers=alex)
    assert r.status_code in (200, 204), r.text

    after = client.get(f"{API}/items/{item_id}", headers=alex).json()
    assert after["waiting_on_account_id"] is None
    assert after["ask"] is None
    assert after["agent"] is None  # no "agent blocked" left for the person to deal with
    # The card goes straight back to the agent, which is told its question was closed.
    brief = client.get(f"{API}/items/{item_id}/brief", headers=ch).json()["text"]
    assert "closed without an answer" in brief
    assert _next(client, ch)["item"]["id"] == item_id
    _finish(client, ch, item_id, outcome="done", reply="Booked the blue one")


def test_an_answered_question_resolved_later_changes_no_wait(client, alex, todo, make_account):
    _, ch = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    item_id = item["id"]
    assert _next(client, ch)["item"]["id"] == item_id
    _finish(client, ch, item_id, outcome="ask", waiting_on="alex", question="Which one?")
    asked = next(c for c in _asks(client, alex, item).values() if c["ask"])
    _comment(client, alex, item_id, "The blue one")
    assert _asks(client, alex, item)[asked["id"]]["resolved_at"] is not None
    brief = client.get(f"{API}/items/{item_id}/brief", headers=ch).json()["text"]
    assert "closed without an answer" not in brief
    events = len(item_events(client, alex, item_id))
    assert client.post(f"{API}/comments/{asked['id']}/resolve", headers=alex).status_code == 200
    assert len(item_events(client, alex, item_id)) == events
