"""Finishing a turn settles only the Asks it took, not the entire conversation."""

import pytest

from conftest import API
from run_proofs import run_fields
from test_assigned_comments import _comment, _item
from test_work import _finish, _next


def _asks(client, headers, item):
    return {
        c["id"]: c for c in client.get(f"{API}/items/{item['id']}/comments", headers=headers).json()
    }


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
    assert rows[old["id"]]["resolved_by_id"] == (None if assigned else agent["id"])
    assert rows[new["id"]]["resolved_at"] is None
    assert rows[human["id"]]["resolved_at"] is None
    got = _next(client, ch)
    assert [a["id"] for a in got["asks"]] == [new["id"]]
    _finish(client, ch, item["id"], outcome="done", comment=False)
    assert _next(client, ch)["item"] is None
    assert person["id"] in client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"]


@pytest.mark.parametrize("outcome", ["review", "blocked", "release"])
@pytest.mark.parametrize("assigned", [False, True])
def test_non_done_finishes_leave_asks_visible_without_repeating_turn(
    client, alex, todo, make_account, outcome, assigned
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"] if assigned else [])
    ask = _comment(client, alex, item["id"], "Please check", assignee="agent")
    _next(client, ch)
    options = {"waiting_on": "alex"} if outcome == "blocked" else {}
    _finish(client, ch, item["id"], outcome=outcome, reply="Needs a person", **options)
    assert _asks(client, alex, item)[ask["id"]]["resolved_at"] is None
    assert _next(client, ch)["item"] is None
    assert item["id"] in [i["id"] for i in client.get(f"{API}/today", headers=ch).json()["asked"]]
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
        json=run_fields(client, ch, item["id"], **{"state": "review"}),
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


def test_asked_only_blocked_on_the_asker_still_waits(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"])
    _comment(client, alex, item["id"], "Which caterer?", assignee="agent2")
    _next(client, gh)
    _finish(client, gh, item["id"], outcome="blocked", waiting_on="alex", reply="Budget?")
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["agent"]["state"] == "blocked"
    assert got["waiting_on_account_id"] is not None
