"""`later`: the agent did what it can now and runs again at the task's due time.

The person already gave the go, so nothing waits on them: no badge, no waiting-on, the task
stays open and assigned to the agent, and the due gate holds it until its run time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import API
from run_proofs import remember, run_fields
from slate.service.brief import closing_line


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _view(client, h, item_id):
    r = client.get(f"{API}/items/{item_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _in(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _finish(client, h, item_id, **body):
    return client.post(
        f"{API}/work/{item_id}/finish", json=run_fields(client, h, item_id, **body), headers=h
    )


def _taken(client, alex, ch, todo, **kw):
    item = _item(client, alex, todo["id"], "Post the holiday hours", assignees=["agent"], **kw)
    if kw.get("due_at"):
        assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, ch)["item"]["id"] == item["id"]
    return item


def test_later_keeps_the_task_open_assigned_and_waiting_on_nobody(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _taken(client, alex, ch, todo, due_at=_in(3))

    r = _finish(client, ch, item["id"], outcome="later", reply="Set for 11:05; I'll post then.")
    assert r.status_code == 200, r.text

    view = _view(client, alex, item["id"])
    assert view["completed_at"] is None
    assert view["agent"] is None  # no blocked or review badge: nothing for a person to do
    assert view["waiting_on_account_id"] is None
    assert [a["id"] for a in view["assignees"]] == [agent["id"]]
    comments = client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    assert comments[-1]["body_md"] == "Set for 11:05; I'll post then."
    # The due gate holds it until its run time: not handed straight back.
    assert _next(client, ch)["item"] is None


def test_later_after_the_agent_moves_the_due_time(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)  # no due date: taken at once
    r = client.patch(f"{API}/items/{item['id']}", json={"due_at": _in(2)}, headers=ch)
    assert r.status_code == 200, r.text
    assert _finish(client, ch, item["id"], outcome="later").status_code == 200
    assert _next(client, ch)["item"] is None


def test_later_needs_a_due_time_still_to_come(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    r = _finish(client, ch, item["id"], outcome="later")
    assert r.status_code == 422
    assert "due" in r.text

    past = _taken(client, alex, ch, todo, due_at=_in(-1))
    assert _finish(client, ch, past["id"], outcome="later").status_code == 422


def test_later_takes_no_waiting_on_or_hand_to(client, alex, todo, make_account):
    _, ch = make_account("agent")
    make_account("other-agent")
    item = _taken(client, alex, ch, todo, due_at=_in(3))
    assert _finish(client, ch, item["id"], outcome="later", waiting_on="alex").status_code == 422
    r = _finish(client, ch, item["id"], outcome="later", hand_to="other-agent")
    assert r.status_code == 422


def test_closing_line_reads_later():
    assert closing_line("Scheduled.\nSLATE: later").outcome == "later"
    assert closing_line("`SLATE: LATER.`").waiting_on is None


def test_an_agents_comment_ending_slate_later_clears_its_badge(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Post hours", assignees=["agent"], due_at=_in(3))
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json={"state": "blocked", "note": "Need a go", "waiting_on": "alex"},
        headers=ch,
    )
    assert r.status_code == 200, r.text
    client.patch(f"{API}/items/{item['id']}", json={"waiting_on_account_id": None}, headers=alex)
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Got the go; posting at the due time.\nSLATE: later"},
        headers=ch,
    )
    assert r.status_code == 201, r.text
    view = _view(client, alex, item["id"])
    assert view["agent"] is None and view["completed_at"] is None
    assert view["waiting_on_account_id"] is None


def test_the_brief_offers_later(client, alex, todo, make_account):
    make_account("agent")
    item = _item(client, alex, todo["id"], "Post hours", assignees=["agent"])
    r = client.get(f"{API}/items/{item['id']}/brief", headers=alex)
    assert r.status_code == 200, r.text
    assert "SLATE: later" in r.json()["text"]
