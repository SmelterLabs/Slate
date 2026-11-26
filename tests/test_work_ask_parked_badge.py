"""An Ask reaches the agent it names even when another agent's badge sits parked on the card.

One agent reports `blocked` (waiting on the owner) and finishes; the owner then asks another
agent on the same card. A parked badge (blocked or review, no live lease) is not someone
working: the Ask must be taken. A live `working` badge or a live lease still holds the card."""

from __future__ import annotations

from conftest import API
from test_assigned_comments import _comment, _item
from test_work import _finish, _next


def test_ask_is_taken_over_another_agents_parked_blocked_badge(client, alex, todo, make_account):
    _, bh = make_account("agent2")
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    assert _next(client, bh)["item"]["id"] == item["id"]
    _finish(client, bh, item["id"], outcome="ask", question="Need a password", waiting_on="alex")
    ask = _comment(client, alex, item["id"], "Agent, can you help here?", assignee="agent")
    got = _next(client, ch)
    assert got["item"] is not None and got["item"]["id"] == item["id"]
    assert [a["id"] for a in got["asks"]] == [ask["id"]]


def test_ask_waits_while_another_agent_is_working(client, alex, todo, make_account):
    _, bh = make_account("agent2")
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    assert _next(client, bh)["item"]["id"] == item["id"]
    _comment(client, alex, item["id"], "Agent, look too?", assignee="agent")
    assert _next(client, ch)["item"] is None


def test_ask_waits_on_a_live_working_badge_without_a_lease(client, alex, todo, make_account):
    _, bh = make_account("agent2")
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    r = client.post(
        f"{API}/items/{item['id']}/report", headers=bh, json={"state": "working", "note": "On it"}
    )
    assert r.is_success, r.text
    _comment(client, alex, item["id"], "Agent, look too?", assignee="agent")
    assert _next(client, ch)["item"] is None


def test_unclaimed_working_report_cannot_overwrite_another_agents_fresh_badge(
    client, alex, todo, make_account
):
    agent2, bh = make_account("agent2")
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    assert _next(client, bh)["item"]["id"] == item["id"]
    _finish(client, bh, item["id"], outcome="ask", question="Need a password", waiting_on="alex")

    # Another agent without an Ask cannot clobber agent2's blocked badge by reporting working.
    r = client.post(
        f"{API}/items/{item['id']}/report",
        headers=ch,
        json={"state": "working", "note": "Helping out"},
    )
    assert r.status_code == 409, r.text

    # The original badge stays intact and Alex's reply to agent2 resolves the ask and wakes agent2.
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["agent"]["state"] == "blocked"
    assert detail["agent"]["by"] == agent2["id"]
    _comment(client, alex, item["id"], "Fixed now", assignee="agent2")
    after = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert after["waiting_on_account_id"] is None
    assert after["ask"] is None
    assert _next(client, bh)["item"]["id"] == item["id"]


def test_explicit_reply_to_asking_agent_resolves_ask_even_if_flag_changed(
    client, alex, todo, make_account
):
    _, bh = make_account("agent2")
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    assert _next(client, bh)["item"]["id"] == item["id"]
    _finish(client, bh, item["id"], outcome="ask", question="Sign back in?", waiting_on="alex")
    # Suppose another agent gets asked on the card and leaves a review/working badge while the
    # card still waits on Alex for agent2's structured ask.
    _comment(client, alex, item["id"], "Agent, check this", assignee="agent")
    assert _next(client, ch)["item"]["id"] == item["id"]
    _finish(client, ch, item["id"], outcome="answered", reply="Checked.")
    r = client.post(
        f"{API}/items/{item['id']}/report",
        headers=ch,
        json={"state": "review", "status": "Look at the log"},
    )
    assert r.is_success, r.text

    # Alex explicitly replies to agent2: agent2's open ask is resolved and waiting_on clears.
    _comment(client, alex, item["id"], "@agent2 all set", assignee="agent2")
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["waiting_on_account_id"] is None
    assert detail["ask"] is None
