"""The card is the conversation: an agent's thread reference,
its reply as one comment, "waiting on you" as a notification, the person's reply as the agent's
next turn (an Ask that wakes it), author kinds, and Run now refused while an agent works."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx
from sqlalchemy import select

from conftest import API
from conftest import item_events as _events
from run_proofs import remember, run_fields
from slate import webpush
from slate.models import Event
from slate.service import push
from test_agent_stream import _agent, _setup
from test_api_events import _bearer, _open, live  # noqa: F401 (live is a fixture)
from test_push import browser  # noqa: F401 (a fixture)

THREAD = {"ref": "sess_8f2c", "url": "https://chat.example/t/8f2c"}


def _item(client, h, list_id, title="Re-authenticate the mail login", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _finish(client, h, item_id, **body):
    body = run_fields(client, h, item_id, **body)
    r = client.post(f"{API}/work/{item_id}/finish", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["item"]


def _comment(client, h, item_id, body, **kw):
    r = client.post(f"{API}/items/{item_id}/comments", json={"body_md": body, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comments(client, h, item_id):
    return client.get(f"{API}/items/{item_id}/comments", headers=h).json()


def _view(client, h, item_id):
    return client.get(f"{API}/items/{item_id}", headers=h).json()


# --- Thread reference ------------------------------------------------------------------------


def test_thread_is_stored_per_agent_and_handed_back_on_the_next_take(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    got = _next(client, ch)
    assert got["thread"] is None

    r = client.post(
        f"{API}/work/{item['id']}/heartbeat",
        json=run_fields(client, ch, item["id"], thread=THREAD),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    view = _view(client, alex, item["id"])
    assert view["agent_threads"] == [{"account_id": agent["id"], **THREAD}]
    assert view["agent"]["url"] == THREAD["url"]  # the badge's Open link
    reported = _events(client, alex, item["id"], "item.reported")
    assert reported[-1]["after"]["thread"] == THREAD
    assert reported[-1]["session_ref"] == THREAD["ref"]

    # The same thread again is bookkeeping: no new event.
    client.post(
        f"{API}/work/{item['id']}/heartbeat",
        json=run_fields(client, ch, item["id"], thread=THREAD),
        headers=ch,
    )
    assert len(_events(client, alex, item["id"], "item.reported")) == len(reported)

    # After a runner reinstall the adapter learns its session from /work/next.
    _finish(client, ch, item["id"], outcome="release", comment=False)
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["agent"]}, headers=alex)
    again = _next(client, ch)
    assert again["item"]["id"] == item["id"]
    assert again["thread"] == {"account_id": agent["id"], **THREAD}


def test_thread_is_validated(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    for bad in ({"ref": ""}, {"ref": "x" * 201}, {"ref": "s", "url": "file:///etc/passwd"}):
        r = client.post(
            f"{API}/work/{item['id']}/heartbeat",
            json=run_fields(client, ch, item["id"], **{"thread": bad}),
            headers=ch,
        )
        assert r.status_code == 422, bad


# --- The agent's reply, waiting on you, the person's answer -----------------------------------


def test_a_blocked_reply_is_one_comment_and_the_persons_answer_is_the_next_turn(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    question = (
        "I started the login on the server.\n\nSign in here: https://login.example/abc and tell me "
        "when it says done."
    )
    _finish(
        client,
        ch,
        item["id"],
        outcome="blocked",
        waiting_on="alex",
        reply=question,
        thread=THREAD,
    )

    view = _view(client, alex, item["id"])
    assert view["agent"]["state"] == "blocked"
    assert view["agent"]["note"] == "I started the login on the server."
    assert view["waiting_on_account_id"] == me["id"]
    (said,) = _comments(client, alex, item["id"])
    assert said["body_md"] == question  # verbatim, no "**Blocked** ·" label
    assert said["author_id"] == agent["id"] and said["author_kind"] == "agent"
    commented = _events(client, alex, item["id"], "item.commented")[-1]
    assert commented["session_ref"] == THREAD["ref"]
    # Blocked has no clock: the lease is gone.
    assert client.get(f"{API}/work", headers=ch).json() == []
    # It waits on Alex: the agent cannot take it, and Alex's Today lists it.
    assert _next(client, ch)["item"] is None
    today = client.get(f"{API}/today", headers=alex).json()
    assert [i["id"] for i in today["waiting"]] == [item["id"]]
    inbox = client.get(f"{API}/inbox", headers=alex).json()
    assert [e["reason"] for e in inbox if e["event"]["item_id"] == item["id"]] == ["waiting"]

    # Alex answers in the thread: the answer is addressed to the agent and the card stops waiting.
    answer = _comment(client, alex, item["id"], "done")
    assert answer["assignee_id"] == agent["id"]
    (assigned,) = _events(client, alex, item["id"], "comment.assigned")
    assert assigned["after"]["reply"] is True
    assert _view(client, alex, item["id"])["waiting_on_account_id"] is None

    # The agent takes it at once (no blocked pause), with the answer as `reply` and its session.
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]
    assert got["reply"]["id"] == answer["id"] and got["reply"]["author_kind"] == "person"
    assert got["reply"]["author_handle"] == me["handle"]
    assert got["thread"]["ref"] == THREAD["ref"]
    assert got["why"] == "assigned"

    # Finishing the task does not silently settle an outstanding Ask.
    _finish(client, ch, item["id"], outcome="done", reply="Verified: signed in. Closing.")
    view = _view(client, alex, item["id"])
    assert view["status"]["kind"] == "done" and view["open_asks"] == [agent["id"]]
    assert client.post(f"{API}/comments/{answer['id']}/resolve", headers=ch).status_code == 200
    bodies = [c["body_md"] for c in _comments(client, alex, item["id"])]
    assert bodies == [question, "done", "Verified: signed in. Closing."]


def test_only_the_waited_on_persons_comment_is_a_reply(client, alex, todo, make_account):
    _, ch = make_account("agent")
    _, dh = make_account("member", kind="person")
    _, bh = make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="blocked", waiting_on="alex", reply="Which one?")

    assert _comment(client, dh, item["id"], "I think the blue one")["assignee_id"] is None
    assert _comment(client, bh, item["id"], "Agent chatter")["assignee_id"] is None
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    assert _view(client, alex, item["id"])["waiting_on_account_id"] == me["id"]
    # An explicit Ask to someone else stays that.
    ask = _comment(client, alex, item["id"], "@member can you check?", assignee="member")
    assert ask["assignee_id"] != _view(client, alex, item["id"])["agent"]["by"]
    assert _events(client, alex, item["id"], "comment.assigned")[-1]["after"].get("reply") is None


def test_an_explicit_ask_to_the_waiting_agent_is_a_reply(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="blocked", waiting_on="alex", reply="Signed in?")

    answer = _comment(client, alex, item["id"], "done", assignee="agent")
    assert answer["assignee_id"] == agent["id"]
    assert _events(client, alex, item["id"], "comment.assigned")[-1]["after"]["reply"] is True
    assert _view(client, alex, item["id"])["waiting_on_account_id"] is None
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"] and got["reply"]["id"] == answer["id"]


def test_a_reply_still_reaches_the_agent_after_its_flag_was_cleared(
    client, alex, todo, make_account
):
    # The person stopped the agent's run (the flag goes idle) but the card still waits on them:
    # their answer is still the agent's next turn, and the card stops waiting.
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="blocked", waiting_on="alex", reply="Unopened?")
    r = client.post(
        f"{API}/items/{item['id']}/report", json={"state": "idle", "comment": False}, headers=alex
    )
    assert r.status_code == 200, r.text
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    view = _view(client, alex, item["id"])
    assert view["agent"] is None and view["waiting_on_account_id"] == me["id"]

    answer = _comment(client, alex, item["id"], "Yes, leave it.")
    assert answer["assignee_id"] == agent["id"]
    assert _events(client, alex, item["id"], "comment.assigned")[-1]["after"]["reply"] is True
    assert _view(client, alex, item["id"])["waiting_on_account_id"] is None
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"] and got["reply"]["id"] == answer["id"]


def test_a_wait_a_person_set_is_not_a_reply_after_a_cleared_flag(client, alex, todo, make_account):
    member, dh = make_account("peer", kind="person")
    item = _item(client, alex, todo["id"])
    client.patch(f"{API}/items/{item['id']}", json={"waiting_on_account_id": "peer"}, headers=alex)
    assert _comment(client, dh, item["id"], "Here you go")["assignee_id"] is None


def test_a_person_waiting_on_a_person_is_not_a_reply(client, alex, todo, make_account):
    member, dh = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    client.patch(
        f"{API}/items/{item['id']}", json={"waiting_on_account_id": "member"}, headers=alex
    )
    assert _comment(client, dh, item["id"], "Here you go")["assignee_id"] is None
    assert _view(client, alex, item["id"])["waiting_on_account_id"] == member["id"]


def test_author_kind_marks_guests_and_agents(client, alex, db, make_account):
    agent, _ = make_account("agent")
    r = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    guest = r.json()
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    from slate.service.comments import authors_of

    got = authors_of(db, [me["id"], agent["id"], guest["id"]])
    assert got == {
        me["id"]: (me["handle"], "person"),
        agent["id"]: ("agent", "agent"),
        guest["id"]: ("guest", "guest"),
    }


def test_asking_the_same_person_again_notifies_them_again(
    client,
    alex,
    todo,
    db,
    browser,  # noqa: F811
    make_account,
):
    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    push.process(db, webpush.send)
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="blocked", waiting_on="alex", reply="Sign in, please")
    assert push.process(db, webpush.send) == 1
    first = browser.received[-1]
    assert first["title"] == "Agent needs you on: Re-authenticate the mail login"
    assert first["body"] == "Sign in, please"

    # The agent asks again without an answer in between (Run now, then blocked again).
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    _next(client, ch)
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json=run_fields(
            client,
            ch,
            item["id"],
            **{"state": "blocked", "waiting_on": "alex", "note": "Still need the sign-in"},
        ),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert push.process(db, webpush.send) == 1
    assert browser.received[-1]["body"] == "Still need the sign-in"
    reported = db.scalars(
        select(Event).where(Event.type == "item.reported").order_by(Event.id.desc())
    ).first()
    assert reported.after_json["again"] is True


# --- Run now while held ----------------------------------------------------------------------


def test_run_now_while_working_names_the_agent(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    r = client.post(f"{API}/items/{item['id']}/run", headers=alex)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Agent already has it (working")
    assert _events(client, alex, item["id"], "item.run_requested") == []


# --- The wake on the agent's stream ----------------------------------------------------------


async def test_a_reply_wakes_the_agent_with_the_comment_id(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        admin = _bearer(live.admin_token)
        me = (await client.get(f"{API}/me", headers=admin)).json()["account"]
        r = await client.post(
            f"{API}/items",
            json={"list_id": todo["id"], "title": "Sign in", "assignees": ["agent"]},
            headers=admin,
        )
        item = r.json()
        r = await client.post(f"{API}/work/next", json={}, headers=_bearer(tok))
        assert r.json()["item"]["id"] == item["id"]
        r = await client.post(
            f"{API}/work/{item['id']}/finish",
            json={
                "outcome": "blocked",
                "waiting_on": me["handle"],
                "reply": "Sign in?",
                "run_id": r.json()["lease"]["run_id"],
            },
            headers=_bearer(tok),
        )
        assert r.status_code == 200, r.text

        stream = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
        await stream.ready()
        r = await client.post(
            f"{API}/items/{item['id']}/comments", json={"body_md": "done"}, headers=admin
        )
        answer = r.json()
        hint = None
        for _ in range(6):
            msg = await stream.event(timeout=1)
            if msg.event == "work":
                hint = msg.data
                break
        assert hint is not None
        assert hint["item_id"] == item["id"] and hint["type"] == "comment.assigned"
        assert hint["comment_id"] == answer["id"] and hint["reply"] is True


async def test_run_now_by_the_agents_own_account_wakes_its_runner(live):  # noqa: F811
    # Another session of the agent (a desktop chat on the same account) files a card for it
    # and presses Run now: the runner, on the same account, must still hear it.
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        r = await client.post(
            f"{API}/items",
            json={"list_id": todo["id"], "title": "Fix SMS login", "assignees": ["agent"]},
            headers=_bearer(tok),
        )
        item = r.json()
        stream = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
        await stream.ready()
        r = await client.post(f"{API}/items/{item['id']}/run", headers=_bearer(tok))
        assert r.status_code == 202, r.text
        hint = None
        for _ in range(6):
            msg = await stream.event(timeout=1)
            if msg.event == "work":
                hint = msg.data
                break
        assert hint is not None
        assert hint["item_id"] == item["id"] and hint["type"] == "item.run_requested"


def test_run_now_on_a_card_waiting_on_you_lets_the_agent_go_on(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="blocked", waiting_on="alex", reply="Approve?")
    assert _next(client, ch)["item"] is None  # waiting on Alex
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, ch)["item"]["id"] == item["id"]

    # The same for a review badge.
    _finish(client, ch, item["id"], outcome="review", reply="Have a look")
    assert _next(client, ch)["item"] is None
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    assert _next(client, ch)["item"]["id"] == item["id"]


# --- Clearing "Ready for review" ---------------------------------------------------------------


def _flagged(client, alex, todo, make_account, state, **kw):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json=run_fields(client, ch, item["id"], **{"state": state, **kw}),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["state"] == state
    return item, ch


def test_a_person_clears_ready_for_review_with_an_idle_report(client, alex, todo, make_account):
    item, _ = _flagged(client, alex, todo, make_account, "review", note="Answered")
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json=run_fields(client, alex, item["id"], **{"state": "idle", "comment": False}),
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent"] is None
    assert _view(client, alex, item["id"])["status"] == item["status"]  # same column


def test_moving_a_reviewed_card_clears_the_flag(client, alex, todo, make_account):
    item, _ = _flagged(client, alex, todo, make_account, "review", note="Answered")
    active = todo["statuses"][1]["id"]
    r = client.patch(f"{API}/items/{item['id']}", json={"status": active}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["agent"] is None
    last = _events(client, alex, item["id"], "item.reported")[-1]
    assert last["after"]["state"] == "idle" and last["before"]["state"] == "review"


def test_closing_clears_a_blocked_flag_but_moving_does_not(client, alex, todo, make_account):
    item, _ = _flagged(client, alex, todo, make_account, "blocked", note="Need the code")
    active = todo["statuses"][1]["id"]
    moved = client.patch(f"{API}/items/{item['id']}", json={"status": active}, headers=alex)
    assert moved.json()["agent"]["state"] == "blocked"
    done = client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=alex)
    assert done.json()["agent"] is None


def test_a_working_flag_survives_a_move_and_an_agents_own_move_keeps_review(
    client, alex, todo, make_account
):
    item, ch = _flagged(client, alex, todo, make_account, "working")
    active = todo["statuses"][1]["id"]
    r = client.patch(f"{API}/items/{item['id']}", json={"status": active}, headers=alex)
    assert r.json()["agent"]["state"] == "working"
    client.post(
        f"{API}/items/{item['id']}/report",
        json=run_fields(client, ch, item["id"], **{"state": "review"}),
        headers=ch,
    )
    r = client.patch(
        f"{API}/items/{item['id']}", json={"status": todo["statuses"][0]["id"]}, headers=ch
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["state"] == "review"


# --- Card chat: a question on a card the agent already answered ------------------------------


def test_a_question_on_a_card_ready_for_review_wakes_the_agent(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="review", reply="Fixed, have a look")
    assert _next(client, ch)["item"] is None  # nothing new to do

    ask = _comment(client, alex, item["id"], "Are you stuck?", assignee="agent")
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]
    assert got["reply"]["id"] == ask["id"]  # the question is this turn


def test_a_question_on_a_finished_card_wakes_the_agent(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="done", reply="Paid")
    assert _next(client, ch)["item"] is None

    ask = _comment(client, alex, item["id"], "Which card did you use?", assignee="agent")
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]
    done = _finish(client, ch, item["id"], outcome="done", reply="The business card")
    assert done["completed_at"] is not None
    # Resolving is deliberate, separate from the agent's reply/completion report.
    assert client.post(f"{API}/comments/{ask['id']}/resolve", headers=ch).status_code == 200
    assert _next(client, ch)["item"] is None


# --- One answer per turn --------------------------------------------------------------------


def test_an_agent_that_already_answered_and_finished_is_not_posted_twice(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    # The agent finishes by itself during the run (its own Slate tool)...
    r = client.post(
        f"{API}/items/{item['id']}/report",
        json=run_fields(client, ch, item["id"], **{"state": "done", "note": "Paid"}),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    # ...then its runner reports the same turn's result.
    _finish(client, ch, item["id"], outcome="done", reply="Done 👍 paid it.")
    mine = [c for c in _comments(client, alex, item["id"]) if c["author_id"] != alex]
    assert len(mine) == 1


def test_an_interim_comment_does_not_swallow_the_final_answer(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _comment(client, ch, item["id"], "Looking now")
    _finish(client, ch, item["id"], outcome="done", reply="Paid it.")
    bodies = [c["body_md"] for c in _comments(client, alex, item["id"])]
    assert "Paid it." in bodies


def test_each_turn_gets_its_answer(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="review", reply="First answer")
    _comment(client, alex, item["id"], "And then?", assignee="agent")
    _next(client, ch)
    _finish(client, ch, item["id"], outcome="review", reply="Second answer")
    bodies = [c["body_md"] for c in _comments(client, alex, item["id"])]
    assert "First answer" in bodies and "Second answer" in bodies


def test_an_agent_closing_its_own_card_clears_review_and_the_roster_drops_it(
    client, alex, todo, make_account
):
    """An agent marking its reviewed card done clears "Ready for review": a closed card must
    not count on the Agents page while Needs you (open cards only) leaves it out."""
    item, ch = _flagged(client, alex, todo, make_account, "review", note="Have a look")
    r = client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=ch)
    assert r.status_code == 200, r.text
    assert r.json()["agent"] is None
    roster = client.get(f"{API}/agents", headers=alex).json()
    assert not [w for a in roster for w in a["working"] if w["item_id"] == item["id"]]


def test_the_roster_skips_a_closed_card_that_still_carries_a_flag(
    client, alex, todo, make_account, db
):
    """Old data: a flag left on a card closed before the rule above."""
    from slate.models import AgentState, Item

    item, ch = _flagged(client, alex, todo, make_account, "review", note="Old")
    agent_id = client.get(f"{API}/me", headers=ch).json()["account"]["id"]
    client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=alex)
    t = Item.__table__
    with db.get_bind().begin() as conn:  # past the service layer, like a row from before
        conn.execute(
            t.update()
            .where(t.c.id == item["id"])
            .values(agent_state=AgentState.review.value, agent_state_by=agent_id)
        )
    roster = client.get(f"{API}/agents", headers=alex).json()
    agent = next(a for a in roster if a["account"]["id"] == agent_id)
    assert not [w for w in agent["working"] if w["item_id"] == item["id"]]
