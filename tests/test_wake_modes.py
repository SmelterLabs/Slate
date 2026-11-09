"""Agents nobody can wake from Slate: `accounts.wake` (push | pull | manual), the `nudge` inbox
entry Run now and the 06:00 run send their owner instead, "Copy brief" (GET /items/{id}/brief)
and the `SLATE:` closing line that reports back from a comment."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from conftest import API
from slate.models import Event
from slate.service import push
from slate.service.brief import closing_line
from slate.service.due import emit_due_events


def _item(client, h, list_id, title="Plan the offsite", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _agent(client, alex, handle, wake):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "agent", "wake": wake},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _inbox(client, h, reason=None):
    params = {"reason": reason} if reason else {}
    return client.get(f"{API}/inbox", params=params, headers=h).json()


def _view(client, h, item_id):
    return client.get(f"{API}/items/{item_id}", headers=h).json()


# --- the wake field ------------------------------------------------------------------------


def test_wake_defaults_to_push_and_an_admin_sets_it(client, alex, make_account):
    agent, _ = make_account("agent")
    assert agent["wake"] == "push"
    agent2 = _agent(client, alex, "agent2", "pull")
    assert agent2["wake"] == "pull"
    r = client.patch(f"{API}/accounts/{agent2['id']}", json={"wake": "manual"}, headers=alex)
    assert r.status_code == 200 and r.json()["wake"] == "manual"
    bad = client.patch(f"{API}/accounts/{agent2['id']}", json={"wake": "smoke"}, headers=alex)
    assert bad.status_code == 422
    roster = {a["account"]["handle"]: a for a in client.get(f"{API}/agents", headers=alex).json()}
    assert roster["agent2"]["wake"] == "manual" and roster["agent"]["wake"] == "push"


# --- nudges --------------------------------------------------------------------------------


def test_run_now_on_a_pull_agent_nudges_the_owner(client, alex, todo, make_account):
    agent2 = _agent(client, alex, "agent2", "pull")
    _, member = make_account("member", kind="person")
    item = _item(client, alex, todo["id"], "Draft the newsletter", assignees=["agent2"])

    r = client.post(f"{API}/items/{item['id']}/run", headers=member)
    assert r.status_code == 202, r.text
    after = r.json()["after"]
    assert after["nudge"] == [{"id": agent2["id"], "name": "Agent2", "wake": "pull"}]
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    assert after["notify"] == [me["id"]]  # no prefs.owner: the workspace owner

    (entry,) = _inbox(client, alex, "nudge")
    assert entry["item_title"] == "Draft the newsletter"
    assert _inbox(client, member, "nudge") == []


def test_a_push_agent_gets_no_nudge(client, alex, todo, make_account):
    make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    after = client.post(f"{API}/items/{item['id']}/run", headers=alex).json()["after"]
    assert "nudge" not in after and "notify" not in after


def test_the_agents_prefs_owner_is_nudged_by_the_0600_run(client, alex, todo, db, make_account):
    member, dh = make_account("member", kind="person")
    _, gh = make_account("agent2")
    client.patch(f"{API}/accounts/{_me(client, gh)['id']}", json={"wake": "pull"}, headers=alex)
    assert client.patch(f"{API}/me/prefs", json={"owner": "member"}, headers=gh).status_code == 200
    item = _item(client, alex, todo["id"], assignees=["agent2"], due_at="2026-11-02")

    emit_due_events(db, now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC))  # 06:00 Chicago
    ev = db.scalar(
        select(Event).where(Event.item_id == item["id"], Event.type == "item.run_requested")
    )
    assert ev is not None and ev.after_json["notify"] == [member["id"]]
    (entry,) = _inbox(client, dh, "nudge")
    assert entry["event"]["after"]["reason"] == "due"
    assert _inbox(client, alex, "nudge") == []

    note = push.notification_for(db, ev, "nudge")
    assert note["title"] == "Agent2 has a Slate task waiting"
    assert "work Slate" in note["body"]


def _me(client, h):
    return client.get(f"{API}/me", headers=h).json()["account"]


# --- Copy brief ----------------------------------------------------------------------------


def test_brief_is_the_task_as_one_prompt_ending_with_the_rule(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(
        client,
        alex,
        todo["id"],
        "Book the venue",
        body_md="Lake house, 12 people",
        due_at="2026-11-02",
    )
    client.post(
        f"{API}/items/{item['id']}/checklists",
        json={"name": "Steps", "items": ["Call them", "Pay the deposit"]},
        headers=alex,
    )
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "Budget $2k"}, headers=ch)

    r = client.get(f"{API}/items/{item['id']}/brief", headers=alex)
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert text.startswith(f"Slate task {item['id']}: Book the venue")
    assert "Due: Mon 02 Nov 2026" in text
    assert "Lake house, 12 people" in text
    assert "- [ ] Call them" in text and "Steps:" in text
    assert "> Budget $2k" in text
    assert '"author_handle": "agent"' in text and '"author_kind": "agent"' in text
    assert text.rstrip().endswith("Put that line last, on its own.")
    # The agent is told to use its own memory: look for related past work, save what it learns.
    assert "check your memory and past sessions" in text
    assert "save anything worth remembering" in text
    assert "SLATE: blocked waiting_on alex" in text and r.json()["ask"] == "alex"

    other = client.get(f"{API}/items/{item['id']}/brief", params={"ask": "member"}, headers=alex)
    assert "waiting_on member" in other.json()["text"]
    # An agent asking gets the workspace owner as who to wait on.
    assert client.get(f"{API}/items/{item['id']}/brief", headers=ch).json()["ask"] == "alex"
    # No acceptance criteria: no "Done means:" block.
    assert "Done means:" not in text


def test_brief_carries_acceptance_after_the_body(client, alex, todo):
    item = _item(
        client,
        alex,
        todo["id"],
        "Fix the backup",
        body_md="The nightly backup failed.",
        acceptance_md="- backup ran today\n- restore test passes",
    )
    client.post(
        f"{API}/items/{item['id']}/checklists",
        json={"name": "Steps", "items": ["Look at the log"]},
        headers=alex,
    )
    text = client.get(f"{API}/items/{item['id']}/brief", headers=alex).json()["text"]
    assert (
        "The nightly backup failed.\n\nDone means:\n- backup ran today\n- restore test passes"
        "\n\nSteps:"
    ) in text
    # The runner's /work/next item carries it too (its JSON is the item view).
    assert item["acceptance_md"] == "- backup ran today\n- restore test passes"
    blank = _item(client, alex, todo["id"], "No criteria", acceptance_md="   ")
    text = client.get(f"{API}/items/{blank['id']}/brief", headers=alex).json()["text"]
    assert "Done means:" not in text


async def test_mcp_item_brief(alex):
    from test_mcp import call, http_mcp, make_list, ok

    async with http_mcp(alex) as mcp:
        todo = await make_list(mcp)
        item = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Brief me"))
        out = ok(await call(mcp, "slate_item", "brief", id=item["id"]))
        assert out["text"].startswith(f"Slate task {item['id']}: Brief me")


# --- the SLATE: closing line ---------------------------------------------------------------


def test_closing_line_parse():
    assert closing_line("All booked.\n\nSLATE: done") == closing_line("SLATE: DONE.")
    assert closing_line("x\n`SLATE: review`").outcome == "review"
    line = closing_line("Two questions...\nSLATE: blocked waiting_on @Alex")
    assert (line.outcome, line.waiting_on) == ("blocked", "alex")
    assert closing_line("SLATE: blocked waiting on alex").waiting_on == "alex"
    assert closing_line("SLATE: done\nthanks") is None  # not the last line
    assert closing_line("SLATE: finished") is None
    assert closing_line("") is None


def _comment(client, h, item_id, body):
    r = client.post(f"{API}/items/{item_id}/comments", json={"body_md": body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_an_agents_comment_ending_slate_done_completes_its_task(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    _comment(client, gh, item["id"], "Booked for Friday.\n\nSLATE: done")
    view = _view(client, alex, item["id"])
    assert view["completed_at"] is not None and view["agent"] is None


def test_an_agent_blocked_line_badges_and_waits_on_the_person(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    _comment(client, gh, item["id"], "Which date?\nSLATE: blocked waiting_on alex")
    view = _view(client, alex, item["id"])
    assert view["agent"]["state"] == "blocked"
    assert view["waiting_on_account_id"] == _me(client, alex)["id"]
    assert view["completed_at"] is None


def test_an_unassigned_agents_done_does_not_complete(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"])
    _comment(client, gh, item["id"], "Answered.\nSLATE: done")
    assert _view(client, alex, item["id"])["completed_at"] is None


def test_a_person_pasting_an_answer(client, alex, todo, make_account):
    _agent(client, alex, "agent3", "manual")
    make_account("member", kind="person")
    item = _item(client, alex, todo["id"], assignees=["agent3"])
    _comment(
        client, alex, item["id"], "Agent3 says: need the budget\nSLATE: blocked waiting_on member"
    )
    view = _view(client, alex, item["id"])
    assert view["agent"] is None  # a person's line sets no agent badge
    assert view["waiting_on_account_id"] is not None
    _comment(client, alex, item["id"], "SLATE: review")  # nothing to change
    assert _view(client, alex, item["id"])["completed_at"] is None
    _comment(client, alex, item["id"], "Agent3: done, invite sent\nSLATE: done")
    assert _view(client, alex, item["id"])["completed_at"] is not None


def test_a_line_that_cannot_apply_leaves_the_comment(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    _comment(client, gh, item["id"], "Stuck\nSLATE: blocked waiting_on nobody_here")
    assert _view(client, alex, item["id"])["agent"]["state"] == "blocked"
    assert _view(client, alex, item["id"])["waiting_on_account_id"] is None


def test_a_report_note_ending_with_the_line_is_not_applied_twice(client, alex, todo, make_account):
    _, gh = make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["agent2"])
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json={"state": "review", "note": "Draft ready\nSLATE: done"},
        headers=gh,
    )
    assert r.status_code == 200, r.text
    view = _view(client, alex, item["id"])
    assert view["agent"]["state"] == "review" and view["completed_at"] is None
