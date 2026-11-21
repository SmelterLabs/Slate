"""One line of truth on every card (`standing`, and whose move it is), review only with
something to check, questions that close when answered, stale badges and stalled cards."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import API
from conftest import item_events as _events
from run_proofs import remember, run_fields
from slate.models import Event, utcnow
from slate.service import due, push
from slate.service.brief import closing_line
from test_migrations import _alembic, _row


def _item(client, h, list_id, title="Send the invoice", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _finish(client, h, item_id, **body):
    return client.post(
        f"{API}/work/{item_id}/finish", json=run_fields(client, h, item_id, **body), headers=h
    )


def _done(client, h, item_id, **body):
    r = _finish(client, h, item_id, **body)
    assert r.status_code == 200, r.text
    return r.json()["item"]


def _view(client, h, item_id):
    return client.get(f"{API}/items/{item_id}", headers=h).json()


def _me(client, h):
    return client.get(f"{API}/me", headers=h).json()["account"]


def _taken(client, alex, ch, todo, **kw):
    item = _item(client, alex, todo["id"], **{"assignees": ["agent"], **kw})
    assert _next(client, ch)["item"]["id"] == item["id"]
    return item


def _inbox(client, h):
    return client.get(f"{API}/inbox", headers=h).json()


# --- standing ----------------------------------------------------------------------------------


def test_status_is_the_standing_and_it_rides_on_the_events(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    view = _done(client, ch, item["id"], outcome="answered", reply="Long reply…",
                 status="Waiting on the bank, not on you")  # fmt: skip
    standing = view["standing"]
    assert standing["line"] == "Waiting on the bank, not on you"
    assert standing["by_id"] == agent["id"] and standing["at"]
    reported = _events(client, alex, item["id"], "item.reported")[-1]
    assert reported["after"]["status"] == "Waiting on the bank, not on you"
    finished = _events(client, alex, item["id"], "item.work_finished")[-1]
    assert finished["after"]["status"] == "Waiting on the bank, not on you"
    assert not [e for e in _events(client, alex, item["id"]) if e["type"] == "item.standing"]


def test_standing_is_derived_from_the_reply_then_the_note(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    reply = "\n\n## **Sent** the `invoice` to [the client](https://example.com/c).\nMore text."
    assert _done(client, ch, item["id"], outcome="answered", reply=reply)["standing"]["line"] == (
        "Sent the invoice to the client."
    )
    other = _taken(client, alex, ch, todo, title="Other")
    view = _done(client, ch, other["id"], outcome="blocked", note="x" * 300)
    assert view["standing"]["line"] == "x" * 139 + "…"


def test_a_working_report_never_changes_the_standing(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json={"state": "blocked", "note": "Need the VPN", "status": "Stuck on the VPN"},
        headers=ch,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"{API}/items/{item['id']}/report", json={"state": "working", "note": "Trying"}, headers=ch
    )
    assert r.json()["standing"]["line"] == "Stuck on the VPN"


def test_the_question_is_an_asks_standing_and_a_status_with_ask_is_refused(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    bad = _finish(client, ch, item["id"], outcome="ask", question="Go?", status="Asking")
    assert bad.status_code == 422
    view = _done(client, ch, item["id"], outcome="ask", question="Start the import now?")
    assert view["standing"]["line"] == "Start the import now?"


@pytest.mark.parametrize("status", ["", "two\nlines", "x" * 141])
def test_status_is_one_line_of_1_to_140(client, alex, todo, make_account, status):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    assert _finish(client, ch, item["id"], outcome="done", status=status).status_code == 422


def test_no_report_no_standing_and_it_stays_after_done(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    assert _view(client, alex, item["id"])["standing"] is None
    view = _done(client, ch, item["id"], outcome="done", reply="Sent it.")
    assert view["status"]["kind"] == "done"
    assert view["standing"]["line"] == "Sent it."


# --- whose move --------------------------------------------------------------------------------


def test_move_is_whoever_the_card_waits_on(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    view = _done(client, ch, item["id"], outcome="ask", question="Go?")
    assert view["standing"]["move_id"] == _me(client, alex)["id"]


def test_move_for_a_review_is_the_person_it_is_for(client, alex, todo, make_account):
    _, ch = make_account("agent")
    bob, _ = make_account("bob", kind="person")
    item = _taken(client, alex, ch, todo)
    view = _done(client, ch, item["id"], outcome="review", status="Check the draft")
    assert view["standing"]["move_id"] == _me(client, alex)["id"]  # default: the owner
    assert view["waiting_on_account_id"] is None
    other = _taken(client, alex, ch, todo, title="Other")
    view = _done(client, ch, other["id"], outcome="review", status="Check it", waiting_on="bob")
    assert view["standing"]["move_id"] == bob["id"]


def test_move_is_nobody_on_a_closed_card(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    assert (
        _done(client, ch, item["id"], outcome="done", reply="Sent.")["standing"]["move_id"] is None
    )


def test_move_is_the_first_agent_assignee_then_the_first_person(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    view = _done(client, ch, item["id"], outcome="answered", reply="Half done.")
    assert view["standing"]["move_id"] == agent["id"]

    people = _item(client, alex, todo["id"], title="People", assignees=["alex"])
    client.post(
        f"{API}/items/{people['id']}/comments",
        json={"body_md": "Rows?", "assignee": "agent"},
        headers=alex,
    )
    assert _next(client, ch)["item"]["id"] == people["id"]
    view = _done(client, ch, people["id"], outcome="answered", reply="1,204.")
    assert view["standing"]["move_id"] == _me(client, alex)["id"]


def test_move_is_nobody_on_an_open_card_with_no_assignee_or_wait(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], title="Loose")
    client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Rows?", "assignee": "agent"},
        headers=alex,
    )
    assert _next(client, ch)["item"]["id"] == item["id"]
    view = _done(client, ch, item["id"], outcome="answered", reply="1,204.")
    assert view["standing"]["move_id"] is None


# --- review needs something to check ------------------------------------------------------------


def test_review_without_a_status_is_refused(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    r = _finish(client, ch, item["id"], outcome="review", reply="All done, have a look")
    assert r.status_code == 422
    assert "done" in r.json()["detail"] and "check" in r.json()["detail"]
    r = client.post(f"{API}/items/{item['id']}/report", json={"state": "review"}, headers=ch)
    assert r.status_code == 422


def test_closing_lines_carry_the_status():
    assert closing_line("x\nSLATE: done: Sent the invoice.").status == "Sent the invoice."
    assert closing_line("SLATE: review: Check the draft linked above.").outcome == "review"
    assert closing_line("SLATE: later").status is None
    assert closing_line("SLATE: answered.").outcome == "answered"
    ask = closing_line("SLATE: ask alex: Start? [Yes | No]")
    assert (ask.outcome, ask.question, ask.status) == ("ask", "Start?", None)


def test_a_bare_review_line_is_done_and_a_review_line_with_text_is_review(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Finished.\nSLATE: review"},
        headers=ch,
    )
    assert _view(client, alex, item["id"])["status"]["kind"] == "done"
    other = _item(client, alex, todo["id"], title="Draft", assignees=["agent"])
    client.post(
        f"{API}/items/{other['id']}/comments",
        json={"body_md": "Drafted.\nSLATE: review: Check the draft linked above."},
        headers=ch,
    )
    view = _view(client, alex, other["id"])
    assert view["agent"]["state"] == "review"
    assert view["standing"]["line"] == "Check the draft linked above."


# --- questions close when answered -------------------------------------------------------------


def _asked(client, alex, ch, todo, **kw):
    item = _item(client, alex, todo["id"], assignees=["agent"], **kw)
    ask = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Can you post it Friday?", "assignee": "agent"},
        headers=alex,
    ).json()
    assert _next(client, ch)["item"]["id"] == item["id"]
    return item, ask


def _resolved(client, h, item_id, comment_id):
    return [
        e["after"]["reason"]
        for e in _events(client, h, item_id, "comment.resolved")
        if e["subject_id"] == comment_id
    ]


@pytest.mark.parametrize(
    ("outcome", "body"),
    [
        ("later", {}),
        ("review", {"status": "Check the post"}),
        ("ask", {"question": "Which Friday?"}),
        ("blocked", {"note": "The site is down"}),
        ("done", {}),
    ],
)
def test_every_finish_but_failed_and_release_resolves_the_turns_asks(
    client, alex, todo, make_account, outcome, body
):
    _, ch = make_account("agent")
    item, ask = _asked(client, alex, ch, todo, due_at=(utcnow() + timedelta(days=3)).isoformat())
    assert _finish(client, ch, item["id"], outcome=outcome, **body).status_code == 200
    assert _resolved(client, alex, item["id"], ask["id"]) == ["answered"]


@pytest.mark.parametrize(
    ("outcome", "body"), [("failed", {"note": "exit 1"}), ("release", {})]
)  # fmt: skip
def test_failed_and_release_leave_the_asks_open(client, alex, todo, make_account, outcome, body):
    _, ch = make_account("agent")
    item, ask = _asked(client, alex, ch, todo)
    assert _finish(client, ch, item["id"], outcome=outcome, **body).status_code == 200
    assert _resolved(client, alex, item["id"], ask["id"]) == []


def test_the_migration_resolves_old_asks_the_agent_already_answered(tmp_path):
    db = tmp_path / "slate.db"
    assert _alembic(db, "upgrade", "d8f6b2e4c913").returncode == 0
    with sqlite3.connect(db) as conn:
        _row(conn, "accounts", id="acc000000001", handle="alex", kind="person", role="owner")
        _row(conn, "accounts", id="acc000000002", handle="my-agent", kind="agent", role="member")
        _row(conn, "sections", id="sec000000001", slug="home", audience="work")
        _row(conn, "lists", id="lst000000001", section_id="sec000000001", slug="l")
        _row(conn, "items", id="itm000000001", list_id="lst000000001", kind="task",
             created_by="acc000000001")  # fmt: skip
        # Asked, then answered by the agent: resolved. Asked after its last word: left open.
        _row(conn, "comments", id="com000000001", item_id="itm000000001",
             author_id="acc000000001", assignee_id="acc000000002",
             created_at="2026-11-01T10:00:00.000000Z")  # fmt: skip
        _row(conn, "comments", id="com000000002", item_id="itm000000001",
             author_id="acc000000002", created_at="2026-11-01T11:00:00.000000Z")  # fmt: skip
        _row(conn, "comments", id="com000000003", item_id="itm000000001",
             author_id="acc000000001", assignee_id="acc000000002",
             created_at="2026-11-01T12:00:00.000000Z")  # fmt: skip
        # Addressed to a person: never touched.
        _row(conn, "comments", id="com000000004", item_id="itm000000001",
             author_id="acc000000002", assignee_id="acc000000001",
             created_at="2026-11-01T09:00:00.000000Z")  # fmt: skip
    assert _alembic(db, "upgrade", "head").returncode == 0
    with sqlite3.connect(db) as conn:
        rows = dict(conn.execute("SELECT id, resolved_at FROM comments ORDER BY id"))
        untouched = conn.execute(
            "SELECT resolved_by_id FROM comments WHERE id IN ('com000000003', 'com000000004')"
        ).fetchall()
        by = conn.execute("SELECT resolved_by_id FROM comments WHERE id='com000000001'").fetchone()
    assert rows["com000000001"] == "2026-11-01T11:00:00.000000Z" and by == ("acc000000002",)
    assert rows["com000000003"] is None and rows["com000000004"] is None
    assert untouched == [(None,), (None,)]


# --- stale badges ------------------------------------------------------------------------------


def test_a_working_badge_with_no_run_is_cleared_after_30_minutes(
    client, alex, todo, make_account, db
):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    client.post(f"{API}/items/{item['id']}/report", json={"state": "working"}, headers=ch)
    seen = {e["event"]["id"] for e in _inbox(client, alex)}
    db.expire_all()
    assert due.clear_stale_badges(db, now=utcnow() + timedelta(minutes=29)) == 0
    assert due.clear_stale_badges(db, now=utcnow() + timedelta(minutes=31)) == 1
    view = _view(client, alex, item["id"])
    assert view["agent"] is None
    last = _events(client, alex, item["id"], "item.reported")[-1]
    assert last["after"]["state"] == "idle" and last["after"]["reason"] == "stale"
    assert last["source"] == "system"
    assert [e for e in _inbox(client, alex) if e["event"]["id"] not in seen] == []


def test_a_working_badge_with_a_live_lease_stays(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch, lease_seconds=7200)
    db.expire_all()
    assert due.clear_stale_badges(db, now=utcnow() + timedelta(minutes=45)) == 0
    assert _view(client, alex, item["id"])["agent"]["state"] == "working"


# --- stalled cards -----------------------------------------------------------------------------


def _answered(client, alex, ch, todo, **kw):
    item = _taken(client, alex, ch, todo, **kw)
    _done(client, ch, item["id"], outcome="answered", reply="Did part of it.")
    return item


def _tick(db, hours=4.1):
    db.expire_all()
    return due.poke_stalled(db, now=utcnow() + timedelta(hours=hours))


def _pokes(client, h, item_id):
    return [
        e for e in _events(client, h, item_id, "item.run_requested")
        if e["after"]["reason"] == "stalled"
    ]  # fmt: skip


def test_an_answered_card_is_poked_once_after_4_hours(client, alex, todo, make_account, db):
    agent, ch = make_account("agent")
    item = _answered(client, alex, ch, todo)
    assert _tick(db, hours=3.9) == 0
    assert _tick(db) == 1
    assert _tick(db) == 0  # once
    (poke,) = _pokes(client, alex, item["id"])
    assert poke["after"]["for"] == [agent["id"]] and poke["source"] == "system"
    brief = client.get(f"{API}/items/{item['id']}/brief", headers=ch).json()["text"]
    assert "still open" in brief and "`done`, `ask`, `later` or `review`" in brief
    assert _next(client, ch)["item"]["id"] == item["id"]  # takeable once
    _done(client, ch, item["id"], outcome="done", reply="Finished it.")
    assert _view(client, alex, item["id"])["stalled"] is False


@pytest.mark.parametrize(
    "kw",
    [
        {"due_at": "2099-01-01"},
        {"start_at": "2099-01-01"},
        {"waiting_on_account_id": "other-agent"},
    ],
)
def test_a_card_with_time_to_come_or_a_wait_is_not_stalled(
    client, alex, todo, make_account, db, kw
):
    _, ch = make_account("agent")
    make_account("other-agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _done(client, ch, item["id"], outcome="answered", reply="Part.")
    client.patch(f"{API}/items/{item['id']}", json=kw, headers=alex)
    assert _tick(db) == 0


def _stalled(client, alex, ch, todo, db):
    item = _answered(client, alex, ch, todo)
    seen = {e["event"]["id"] for e in _inbox(client, alex)}
    assert _tick(db) == 1
    assert _next(client, ch)["item"]["id"] == item["id"]
    _done(client, ch, item["id"], outcome="answered", reply="Still part of it.")
    return item, seen


def test_a_poked_run_that_answers_again_marks_the_card_stalled(
    client, alex, todo, make_account, db
):
    agent, ch = make_account("agent")
    item, seen = _stalled(client, alex, ch, todo, db)
    view = _view(client, alex, item["id"])
    assert view["stalled"] is True
    (ev,) = _events(client, alex, item["id"], "item.stalled")
    assert ev["after"]["agent_id"] == agent["id"]
    new = [e for e in _inbox(client, alex) if e["event"]["id"] not in seen]
    assert ("stalled", "item.stalled") in [(e["reason"], e["event"]["type"]) for e in new]
    assert _tick(db, hours=9) == 0  # no further poke
    db.expire_all()
    stalled = db.scalar(select(Event).where(Event.type == "item.stalled"))
    note = push.notification_for(db, stalled, "stalled")
    assert note["title"] == f"{agent['display_name']} stopped without finishing: {item['title']}"
    assert "stalled" in push.DEFAULT_REASONS


def test_run_now_clears_stalled(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item, _ = _stalled(client, alex, ch, todo, db)
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _view(client, alex, item["id"])["stalled"] is False


def test_a_new_ask_clears_stalled(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item, _ = _stalled(client, alex, ch, todo, db)
    client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Where are we?", "assignee": "agent"},
        headers=alex,
    )
    assert _view(client, alex, item["id"])["stalled"] is False


def test_a_finish_other_than_answered_clears_stalled(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item, _ = _stalled(client, alex, ch, todo, db)
    r = client.post(
        f"{API}/work/{item['id']}/finish",
        json={"outcome": "review", "status": "Check the half that is done"},
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert _view(client, alex, item["id"])["stalled"] is False
