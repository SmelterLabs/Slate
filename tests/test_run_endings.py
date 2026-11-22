"""How an agent's run ends: `ask` (the only way to stop a card on a person, with a one-line
question and optional answer buttons), `answered` (a reply and nothing else) and `failed` (the
machine broke: no person is asked anything; Slate retries, then tells people once)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import API
from conftest import item_events as _events
from run_proofs import remember, run_fields
from slate.models import Comment, Event, Item, utcnow
from slate.service import attention, due, push
from slate.service.brief import closing_line


def _item(client, h, list_id, title="Import the price list", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _finish(client, h, item_id, **body):
    body = run_fields(client, h, item_id, **body)
    return client.post(f"{API}/work/{item_id}/finish", json=body, headers=h)


def _view(client, h, item_id):
    return client.get(f"{API}/items/{item_id}", headers=h).json()


def _comments(client, h, item_id):
    return client.get(f"{API}/items/{item_id}/comments", headers=h).json()


def _inbox(client, h):
    r = client.get(f"{API}/inbox", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _taken(client, alex, ch, todo, **kw):
    item = _item(client, alex, todo["id"], assignees=["agent"], **kw)
    assert _next(client, ch)["item"]["id"] == item["id"]
    return item


# --- ask ---------------------------------------------------------------------------------------


def test_ask_posts_the_question_as_an_ask_and_waits_on_the_person(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    seen = {e["event"]["id"] for e in _inbox(client, alex)}
    r = _finish(
        client, ch, item["id"], outcome="ask", question="Start the import now?",
        options=["Yes", "No", "Not yet"],
    )  # fmt: skip
    assert r.status_code == 200, r.text

    view = _view(client, alex, item["id"])
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    assert view["waiting_on_account_id"] == me["id"]
    assert view["agent"]["state"] == "blocked"
    assert view["agent"]["note"] == "Start the import now?"
    last = _comments(client, alex, item["id"])[-1]
    assert last["author_id"] == agent["id"]
    assert last["body_md"] == "Start the import now?"
    assert last["assignee_id"] == me["id"]
    q = {"question": "Start the import now?", "options": ["Yes", "No", "Not yet"]}
    assert last["ask"] == {**q, "questions": [q]}
    assert view["ask"]["comment_id"] == last["id"]
    assert view["ask"]["question"] == "Start the import now?"
    assert view["ask"]["options"] == ["Yes", "No", "Not yet"]
    assert view["ask"]["by_id"] == agent["id"]
    assert view["ask"]["at"]

    reported = _events(client, alex, item["id"], "item.reported")[-1]["after"]
    assert reported["state"] == "blocked"
    assert reported["waiting_on"] == me["id"]
    assert reported["question"] == "Start the import now?"
    assert reported["options"] == ["Yes", "No", "Not yet"]
    # One entry tells alex: `waiting`, not also a `mention` for the Ask it carries.
    new = [e for e in _inbox(client, alex) if e["event"]["id"] not in seen]
    assert [e["reason"] for e in new] == ["waiting"]


def test_ask_with_a_reply_posts_the_reply_and_keeps_the_question_on_the_comment(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    r = _finish(
        client, ch, item["id"], outcome="ask", question="Which list?",
        reply="The file has two price lists, retail and trade.\n\nWhich list?",
    )  # fmt: skip
    assert r.status_code == 200, r.text
    last = _comments(client, alex, item["id"])[-1]
    assert last["body_md"].startswith("The file has two price lists")
    q = {"question": "Which list?", "options": []}
    assert last["ask"] == {**q, "questions": [q]}
    assert _view(client, alex, item["id"])["agent"]["note"] == "Which list?"


def test_ask_waits_on_whoever_asked_this_turn_else_the_owner(client, alex, todo, make_account):
    _, ch = make_account("agent")
    bob, bh = make_account("bob", kind="person")
    item = _item(client, alex, todo["id"], title="Check the totals")
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Do the totals add up?", "assignee": "agent"},
        headers=bh,
    )
    assert r.status_code == 201, r.text
    assert _next(client, ch)["item"]["id"] == item["id"]
    assert (
        _finish(client, ch, item["id"], outcome="ask", question="Which month?").status_code == 200
    )
    assert _view(client, alex, item["id"])["waiting_on_account_id"] == bob["id"]

    other = _taken(client, alex, ch, todo)
    assert _finish(client, ch, other["id"], outcome="ask", question="Go?").status_code == 200
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    assert _view(client, alex, other["id"])["waiting_on_account_id"] == me["id"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "Two\nlines?"},
        {"question": "x" * 201},
        {"question": "Go?", "options": ["Yes"]},
        {"question": "Go?", "options": ["A", "B", "C", "D", "E"]},
        {"question": "Go?", "options": ["Yes", "x" * 41]},
        {"question": "Go?", "options": ["Yes", ""]},
    ],
)
def test_ask_refuses_a_question_or_options_out_of_shape(client, alex, todo, make_account, body):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    r = _finish(client, ch, item["id"], outcome="ask", **body)
    assert r.status_code == 422, r.text


def test_question_goes_only_with_ask(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    assert _finish(client, ch, item["id"], outcome="done", question="Go?").status_code == 422


# --- refusals: no stopping on a person without a question -------------------------------------


def test_blocked_waiting_on_a_person_is_refused_with_advice_to_ask(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    r = _finish(client, ch, item["id"], outcome="blocked", waiting_on="alex")
    assert r.status_code == 422
    assert "ask" in r.json()["detail"] and "question" in r.json()["detail"]

    r = client.post(
        f"{API}/items/{item['id']}/report",
        json={"state": "blocked", "note": "Need a go", "waiting_on": "alex"},
        headers=ch,
    )
    assert r.status_code == 422
    assert "ask" in r.json()["detail"]


def test_blocked_on_an_agent_or_on_nobody_still_works(client, alex, todo, make_account):
    _, ch = make_account("agent")
    other, _ = make_account("other-agent")
    item = _taken(client, alex, ch, todo)
    r = _finish(client, ch, item["id"], outcome="blocked", waiting_on="other-agent")
    assert r.status_code == 200, r.text
    assert _view(client, alex, item["id"])["waiting_on_account_id"] == other["id"]

    second = _taken(client, alex, ch, todo)
    r = _finish(client, ch, second["id"], outcome="blocked", note="The mail server is down")
    assert r.status_code == 200, r.text
    assert _view(client, alex, second["id"])["agent"]["state"] == "blocked"


def test_an_agent_may_not_set_a_card_waiting_on_a_person(client, alex, todo, make_account):
    _, ch = make_account("agent")
    make_account("other-agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.patch(
        f"{API}/items/{item['id']}", json={"waiting_on_account_id": "alex"}, headers=ch
    )
    assert r.status_code == 422
    assert "ask" in r.json()["detail"]
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [item["id"]], "changes": {"waiting_on_account_id": "alex"}},
        headers=ch,
    )
    assert r.status_code == 422
    r = client.patch(
        f"{API}/items/{item['id']}", json={"waiting_on_account_id": "other-agent"}, headers=ch
    )
    assert r.status_code == 200, r.text
    # A person setting who a card waits on is unchanged.
    r = client.patch(
        f"{API}/items/{item['id']}", json={"waiting_on_account_id": "alex"}, headers=alex
    )
    assert r.status_code == 200, r.text


# --- answering ---------------------------------------------------------------------------------


def _asked(client, alex, ch, todo, **kw):
    item = _taken(client, alex, ch, todo, **kw)
    r = _finish(
        client, ch, item["id"], outcome="ask", question="Start the import now?",
        options=["Yes", "No"],
    )  # fmt: skip
    assert r.status_code == 200, r.text
    return item, _view(client, alex, item["id"])["ask"]["comment_id"]


def _resolved(client, h, item_id, comment_id):
    return [
        e["after"]
        for e in _events(client, h, item_id, "comment.resolved")
        if e["subject_id"] == comment_id
    ]


def test_answering_with_an_option_replies_and_wakes_the_agent(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item, ask_id = _asked(client, alex, ch, todo)
    r = client.post(f"{API}/comments/{ask_id}/answer", json={"option": "Yes"}, headers=alex)
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["body_md"] == "Yes"
    assert answer["assignee_id"] == agent["id"]  # the reply is the agent's next turn

    view = _view(client, alex, item["id"])
    assert view["waiting_on_account_id"] is None
    assert view["ask"] is None
    assert _resolved(client, alex, item["id"], ask_id)[-1]["reason"] == "answered"
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]
    assert got["reply"]["id"] == answer["id"]


def test_answer_refuses_an_option_it_does_not_offer(client, alex, todo, make_account):
    _, ch = make_account("agent")
    _, ask_id = _asked(client, alex, ch, todo)
    r = client.post(f"{API}/comments/{ask_id}/answer", json={"option": "Maybe"}, headers=alex)
    assert r.status_code == 422


def test_answer_is_for_the_person_waited_on_or_an_admin(client, alex, todo, make_account):
    _, ch = make_account("agent")
    _, bh = make_account("bob", kind="person")
    item, ask_id = _asked(client, alex, ch, todo)
    r = client.post(f"{API}/comments/{ask_id}/answer", json={"option": "Yes"}, headers=bh)
    assert r.status_code == 403

    # Waiting on bob (who asked this turn): alex is an admin and may answer for him.
    bob_item = _item(client, alex, todo["id"], title="Totals")
    client.post(
        f"{API}/items/{bob_item['id']}/comments",
        json={"body_md": "Totals?", "assignee": "agent"},
        headers=bh,
    )
    assert _next(client, ch)["item"]["id"] == bob_item["id"]
    r = _finish(client, ch, bob_item["id"], outcome="ask", question="Which?", options=["A", "B"])
    assert r.status_code == 200, r.text
    bob_ask = _view(client, alex, bob_item["id"])["ask"]["comment_id"]
    r = client.post(f"{API}/comments/{bob_ask}/answer", json={"option": "B"}, headers=alex)
    assert r.status_code == 200, r.text
    view = _view(client, alex, bob_item["id"])
    assert view["waiting_on_account_id"] is None
    assert view["ask"] is None
    assert _resolved(client, alex, bob_item["id"], bob_ask)[-1]["reason"] == "answered"


def test_a_typed_reply_answers_the_ask_too(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item, ask_id = _asked(client, alex, ch, todo)
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Yes, but only the retail list"},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    view = _view(client, alex, item["id"])
    assert view["waiting_on_account_id"] is None
    assert view["ask"] is None
    assert _resolved(client, alex, item["id"], ask_id)[-1]["reason"] == "answered"
    assert _next(client, ch)["reply"]["body_md"] == "Yes, but only the retail list"


# --- answered ----------------------------------------------------------------------------------


def test_answered_posts_the_reply_and_changes_nothing_else(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "How many rows are in the file?", "assignee": "agent"},
        headers=alex,
    )
    ask = r.json()
    assert _next(client, ch)["item"]["id"] == item["id"]
    before = _view(client, alex, item["id"])
    assert _finish(client, ch, item["id"], outcome="answered").status_code == 422  # no reply
    r = _finish(client, ch, item["id"], outcome="answered", reply="1,204 rows.")
    assert r.status_code == 200, r.text

    view = _view(client, alex, item["id"])
    assert view["agent"] is None
    assert view["status"] == before["status"]
    assert view["completed_at"] is None
    assert [a["id"] for a in view["assignees"]] == [agent["id"]]
    assert view["waiting_on_account_id"] is None
    last = _comments(client, alex, item["id"])[-1]
    assert (last["author_id"], last["body_md"]) == (agent["id"], "1,204 rows.")
    assert _resolved(client, alex, item["id"], ask["id"])[-1]["reason"] == "answered"
    assert client.get(f"{API}/work", headers=ch).json() == []


def test_an_answered_card_waits_for_run_now_or_a_new_ask(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    assert _finish(client, ch, item["id"], outcome="answered", reply="Done what I can.").is_success
    assert _next(client, ch)["item"] is None  # not handed straight back: no loop
    client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "One more thing", "assignee": "agent"},
        headers=alex,
    )
    assert _next(client, ch)["item"]["id"] == item["id"]
    assert _finish(client, ch, item["id"], outcome="answered", reply="Sorted.").is_success
    assert _next(client, ch)["item"] is None
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, ch)["item"]["id"] == item["id"]


def test_answered_leaves_who_the_card_waits_on(client, alex, todo, make_account):
    _, ch = make_account("agent")
    other, _ = make_account("other-agent")
    item = _taken(client, alex, ch, todo)
    client.patch(f"{API}/items/{item['id']}", json={"waiting_on_account_id": "other-agent"},
                 headers=alex)  # fmt: skip
    assert _finish(client, ch, item["id"], outcome="answered", reply="Noted.").status_code == 200
    assert _view(client, alex, item["id"])["waiting_on_account_id"] == other["id"]


# --- failed ------------------------------------------------------------------------------------


def _fail(client, ch, item_id, note="runner: exit 1\nTraceback ..."):
    r = _finish(client, ch, item_id, outcome="failed", note=note)
    assert r.status_code == 200, r.text
    return r.json()["item"]


def _retry(db, at):
    db.expire_all()
    return due.retry_failed_runs(db, now=at)


def _requests(client, h, item_id):
    return [e["after"] for e in _events(client, h, item_id, "item.run_requested")]


def test_failed_needs_a_note_and_posts_no_comment(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    assert _finish(client, ch, item["id"], outcome="failed").status_code == 422
    count = len(_comments(client, alex, item["id"]))
    seen = {e["event"]["id"] for e in _inbox(client, alex)}
    view = _fail(client, ch, item["id"])

    assert view["agent"]["state"] == "failed"
    assert view["agent"]["note"] == "runner: exit 1"
    assert view["waiting_on_account_id"] is None
    assert len(_comments(client, alex, item["id"])) == count
    finished = _events(client, alex, item["id"], "item.work_finished")[-1]["after"]
    assert finished["outcome"] == "failed"
    assert finished["note"] == "runner: exit 1\nTraceback ..."
    assert finished["failures"] == 1
    assert client.get(f"{API}/work", headers=ch).json() == []
    # Nobody hears about a failure before Slate gives up (alex filed and watches the card).
    assert [e for e in _inbox(client, alex) if e["event"]["id"] not in seen] == []


def test_failed_leaves_who_the_card_waits_on(client, alex, todo, make_account):
    _, ch = make_account("agent")
    other, _ = make_account("other-agent")
    item = _taken(client, alex, ch, todo)
    client.patch(f"{API}/items/{item['id']}", json={"waiting_on_account_id": "other-agent"},
                 headers=alex)  # fmt: skip
    _fail(client, ch, item["id"])
    assert _view(client, alex, item["id"])["waiting_on_account_id"] == other["id"]


def test_failed_retries_after_5_then_30_minutes_then_gives_up_once(
    client, alex, todo, make_account, db
):
    agent, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    seen = {e["event"]["id"] for e in _inbox(client, alex)}

    _fail(client, ch, item["id"])
    assert _next(client, ch)["item"] is None  # not offered again before its retry time
    t1 = utcnow()
    assert _retry(db, t1 + timedelta(minutes=4)) == 0
    assert _retry(db, t1 + timedelta(minutes=5, seconds=1)) == 1
    assert _retry(db, t1 + timedelta(minutes=6)) == 0  # once per failure
    assert _requests(client, alex, item["id"])[-1] == {"for": [agent["id"]], "reason": "retry"}
    assert _events(client, alex, item["id"], "item.run_requested")[-1]["source"] == "system"

    assert _next(client, ch)["item"]["id"] == item["id"]
    assert _fail(client, ch, item["id"])["agent"]["state"] == "failed"
    t2 = utcnow()
    assert _retry(db, t2 + timedelta(minutes=29)) == 0
    assert _retry(db, t2 + timedelta(minutes=30, seconds=1)) == 1

    assert _next(client, ch)["item"]["id"] == item["id"]
    _fail(client, ch, item["id"], note="runner: out of memory")
    assert _retry(db, utcnow() + timedelta(hours=5)) == 0  # no third retry
    gave_up = _events(client, alex, item["id"], "item.run_failed")
    assert [e["after"]["agent_id"] for e in gave_up] == [agent["id"]]
    assert gave_up[0]["after"]["failures"] == 3
    assert gave_up[0]["after"]["note"] == "runner: out of memory"

    new = [e for e in _inbox(client, alex) if e["event"]["id"] not in seen]
    assert [(e["reason"], e["event"]["type"]) for e in new] == [("failed", "item.run_failed")]
    db.expire_all()
    ev = db.scalar(select(Event).where(Event.type == "item.run_failed"))
    note = push.notification_for(db, ev, "failed")
    assert note["title"] == f"{agent['display_name']} could not run: {item['title']}"


def test_run_now_starts_the_retry_count_over(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    for _ in range(3):
        _fail(client, ch, item["id"])
        _retry(db, utcnow() + timedelta(hours=1))
        _next(client, ch)
    assert len(_events(client, alex, item["id"], "item.run_failed")) == 1
    assert _next(client, ch)["item"] is None

    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, ch)["item"]["id"] == item["id"]
    _fail(client, ch, item["id"])
    finished = _events(client, alex, item["id"], "item.work_finished")[-1]["after"]
    assert finished["failures"] == 1
    assert _retry(db, utcnow() + timedelta(minutes=5, seconds=1)) == 1


def test_a_finish_that_is_not_failed_starts_the_count_over(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    _fail(client, ch, item["id"])
    _retry(db, utcnow() + timedelta(minutes=6))
    _next(client, ch)
    assert _finish(client, ch, item["id"], outcome="blocked", note="Disk full").status_code == 200
    client.post(f"{API}/items/{item['id']}/run", headers=alex)
    _next(client, ch)
    _fail(client, ch, item["id"])
    assert _events(client, alex, item["id"], "item.work_finished")[-1]["after"]["failures"] == 1


def test_a_failed_card_whose_run_was_pressed_is_offered_again(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    _fail(client, ch, item["id"])
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, ch)["item"]["id"] == item["id"]


# --- the pop-up and the closing line ------------------------------------------------------------


def test_the_waiting_pop_up_leads_with_the_question(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    item, _ = _asked(client, alex, ch, todo)
    db.expire_all()
    ev = db.scalar(
        select(Event)
        .where(Event.item_id == item["id"], Event.field == "waiting_on_account_id")
        .order_by(Event.id.desc())
    )
    note = push.notification_for(db, ev, "waiting")
    assert note["title"] == "Start the import now?"
    assert item["title"] in note["body"]
    assert "failed" in push.DEFAULT_REASONS


def test_question_and_questions_have_one_storage_and_view_shape(
    client, alex, todo, make_account, db
):
    _, token = make_account("my-agent")
    options = ["Yes", "No"]
    q = {"question": "Start the import?", "options": options}
    for fields in (q, {"questions": [q]}):
        item = _item(client, alex, todo["id"])
        response = client.post(
            f"{API}/items/{item['id']}/report", json={"state": "blocked", **fields}, headers=token
        )
        assert response.status_code == 200, response.text
        comment = _comments(client, alex, item["id"])[-1]
        db.expire_all()
        assert db.get(Comment, comment["id"]).ask_json == {"questions": [q]}
        assert comment["ask"] == {**q, "questions": [q]}
        assert response.json()["ask"]["questions"] == [q]


def test_multiple_questions_share_a_headline_and_one_typed_answer(
    client, alex, todo, make_account, db
):
    _, token = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    attention.release(db, utcnow())  # initialize the release cursor before the waiting event
    assert _next(client, token, item_id=item["id"])["item"]["id"] == item["id"]
    questions = [{"question": "Pick a date?", "options": ["Friday", "Saturday"]}] + [
        {"question": f"Check item {n}?", "options": []} for n in range(2, 5)
    ]
    for bad, error in (
        ({"question": "Go?", "questions": questions}, "send question or questions, not both"),
        ({"questions": []}, "1 to 4 questions"),
        ({"questions": [*questions, questions[0]]}, "1 to 4 questions"),
        ({"questions": [{"question": "Two\rlines?"}]}, "question is one line"),
        ({"questions": [{"question": "Go?", "options": "Yes"}]}, "options are 2 to 4"),
    ):
        response = _finish(client, token, item["id"], outcome="ask", **bad)
        assert response.status_code == 422, response.text
        assert error in response.json()["detail"]
    response = _finish(
        client, token, item["id"], outcome="ask", questions=questions, reply="Stock is counted."
    )
    assert response.status_code == 200, response.text
    view = _view(client, alex, item["id"])
    headline = "4 questions: Pick a date?"
    comment = _comments(client, alex, item["id"])[-1]
    assert comment["ask"] == {"questions": questions, "question": headline, "options": []}
    assert view["ask"]["questions"] == questions and view["ask"]["options"] == []
    assert view["agent"]["note"] == view["standing"]["line"] == headline
    event = _events(client, alex, item["id"], "item.reported")[-1]["after"]
    assert event["question"] == headline and event["questions"] == questions
    assert event["comment_id"] == comment["id"]
    attention.release(db, utcnow())
    alerts = client.get(f"{API}/inbox/alerts", headers=alex, params={"after": "0"}).json()
    assert any(row["question"] == headline for row in alerts), alerts
    db.expire_all()
    event = db.scalar(
        select(Event).where(Event.item_id == item["id"], Event.field == "waiting_on_account_id")
    )
    assert push.notification_for(db, event, "waiting")["title"] == headline
    tap = client.post(
        f"{API}/comments/{comment['id']}/answer", json={"option": "Friday"}, headers=alex
    )
    assert tap.status_code == 422
    assert tap.json()["detail"] == "this ask has several questions: answer them in one comment"
    body = "Friday. Yes on checks two through four."
    assert (
        client.post(
            f"{API}/items/{item['id']}/comments", json={"body_md": body}, headers=alex
        ).status_code
        == 201
    )
    assert _view(client, alex, item["id"])["ask"] is None
    assert _next(client, token, item_id=item["id"])["reply"]["body_md"] == body


def test_an_agent_comment_uses_the_trailing_run_of_ask_lines(client, alex, todo, make_account):
    _, token = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    text = (
        "Report.\nSLATE: ask other-agent: Earlier?\n\nSLATE: ask alex: First? [Yes | No]\n\n"
        "SLATE: ask @Alex: Second?\nSLATE: ask alex: Third?"
    )
    response = client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": text}, headers=token
    )
    assert response.status_code == 201, response.text
    assert response.json()["body_md"] == "Report.\nSLATE: ask other-agent: Earlier?"
    assert response.json()["ask"]["questions"] == [
        {"question": "First?", "options": ["Yes", "No"]},
        {"question": "Second?", "options": []},
        {"question": "Third?", "options": []},
    ]
    assert (
        _events(client, alex, item["id"], "item.reported")[-1]["after"]["comment_id"]
        == response.json()["id"]
    )
    other = _item(client, alex, todo["id"], assignees=["my-agent"])
    text = "Report.\n" + "\n".join(["SLATE: ask alex: Too many?"] * 5)
    refused = client.post(
        f"{API}/items/{other['id']}/comments", json={"body_md": text}, headers=token
    )
    assert refused.status_code == 201
    assert refused.json()["body_md"] == text and refused.json()["ask"] is None
    assert _view(client, alex, other["id"])["waiting_on_account_id"] is None


def test_closing_line_reads_the_new_endings():
    assert closing_line("Ok.\nSLATE: answered").outcome == "answered"
    line = closing_line("Ready.\nSLATE: ask alex: Start the import now? [Yes | No | Not yet]")
    assert (line.outcome, line.waiting_on) == ("ask", "alex")
    assert line.questions == [
        {"question": "Start the import now?", "options": ["Yes", "No", "Not yet"]}
    ]
    plain = closing_line("SLATE: ask @alex: Which list?")
    assert plain.waiting_on == "alex"
    assert plain.questions == [{"question": "Which list?", "options": []}]
    assert closing_line("SLATE: blocked waiting_on alex") is None


@pytest.mark.parametrize("via_comment", [True, False])
def test_next_turn_brief_quotes_the_asks_questions(client, alex, todo, make_account, via_comment):
    _, agent = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    path = f"{API}/items/{item['id']}"
    prompts = ["Which file first?", "Ship today?"]
    if via_comment:
        ending = "\n".join(f"SLATE: ask alex: {q}" for q in prompts)
        response = client.post(
            f"{path}/comments", json={"body_md": f"Inventory is complete.\n{ending}"}, headers=agent
        )
        assert response.status_code == 201, response.text
    else:
        _next(client, agent, item_id=item["id"])
        response = _finish(
            client,
            agent,
            item["id"],
            outcome="ask",
            reply="Inventory is complete.",
            questions=[{"question": q} for q in prompts],
        )
        assert response.status_code == 200, response.text
    response = client.post(
        f"{path}/comments", json={"body_md": "The first one, and yes."}, headers=alex
    )
    assert response.status_code == 201, response.text
    assert _next(client, agent, item_id=item["id"])["reply"]["body_md"] == "The first one, and yes."
    text = client.get(f"{path}/brief", headers=agent).json()["text"]
    assert "> Inventory is complete.\n> 1. Which file first?\n> 2. Ship today?" in text


def test_an_agents_ask_line_asks_and_a_plain_comment_changes_nothing(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "I found two lists.\nSLATE: ask alex: Which one? [Retail | Trade]"},
        headers=ch,
    )
    assert r.status_code == 201, r.text
    view = _view(client, alex, item["id"])
    assert view["ask"]["question"] == "Which one?"
    assert view["ask"]["options"] == ["Retail", "Trade"]
    assert view["agent"]["state"] == "blocked"

    other = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.post(
        f"{API}/items/{other['id']}/comments",
        json={"body_md": "Need a go.\nSLATE: blocked waiting_on alex"},
        headers=ch,
    )
    assert r.status_code == 201, r.text
    view = _view(client, alex, other["id"])
    assert view["waiting_on_account_id"] is None and view["agent"] is None


def test_an_ask_comment_write_error_rolls_back_the_ending(
    client, alex, todo, make_account, monkeypatch
):
    from slate.service import comments
    from slate.service.errors import Invalid

    _, agent = make_account("my-agent")
    item = _item(client, alex, todo["id"], assignees=["my-agent"])
    calls = 0

    def fail_first(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Invalid("could not store comment attachments")

    monkeypatch.setattr(comments, "attach_pasted", fail_first)
    response = client.post(
        f"{API}/items/{item['id']}/comments",
        json={"body_md": "Report.\nSLATE: ask alex: Which one?"},
        headers=agent,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "could not store comment attachments"
    assert calls == 1 and _comments(client, alex, item["id"]) == []
    assert _view(client, alex, item["id"])["ask"] is None
    assert _events(client, alex, item["id"], "item.reported") == []


def test_the_failed_badge_is_stored(db, client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _taken(client, alex, ch, todo)
    _fail(client, ch, item["id"])
    db.expire_all()
    assert db.get(Item, item["id"]).agent_state.value == "failed"
