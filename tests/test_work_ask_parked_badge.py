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
    _finish(client, bh, item["id"], outcome="blocked", reply="Need a password", waiting_on="alex")
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
