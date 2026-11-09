"""A doc @mention wakes a push agent (docs/agents-work-loop.md): the chat design on pages.
The wake is an event (`doc.mentioned_agent`), the turn is a lease the runner takes with
`POST /work/next {doc_id, thread_id}`, and `POST /work/doc/finish` posts the answer as the
agent's comment in the thread (a new discussion thread for a mention in the page body)."""

from __future__ import annotations

from datetime import timedelta

from conftest import API
from run_proofs import remember, run_fields
from slate.models import DocWake, utcnow
from slate.service import docwake
from test_chat_wake import _agent, _events


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _save(client, h, doc_id, body_md):
    r = client.patch(f"{API}/docs/{doc_id}", json={"body_md": body_md}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _comment(client, h, doc_id, **body):
    r = client.post(f"{API}/docs/{doc_id}/comments", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _key(body):
    return ("doc", body["doc_id"], body.get("thread_id"))


def _finish(client, h, **body):
    body = run_fields(client, h, _key(body), **body)
    r = client.post(f"{API}/work/doc/finish", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


BODY = (
    "# Roof\n\nIntro line.\n\n## Shingles\n\nAsk @agent to price 30 squares.\n\n"
    "## Gutters\n\nLater."
)


def test_a_body_mention_wakes_and_the_answer_starts_a_thread(client, alex):
    agent, ch = _agent(client, alex)
    page = _doc(client, alex, title="Roof plan", body_md="Draft")
    _save(client, alex, page["id"], BODY)

    wakes = _events(client, alex, "doc.mentioned_agent")
    assert len(wakes) == 1 and wakes[0]["subject_id"] == page["id"]
    after = wakes[0]["after"]
    assert after == {
        **after,
        "doc_id": page["id"],
        "title": "Roof plan",
        "thread_id": None,
        "comment_id": None,
        "excerpt": "Ask @agent to price 30 squares.",
        "for": [agent["id"]],
        "queued": [],
    }
    # The mention still reaches the agent's check-in as before.
    news = client.get(f"{API}/checkin", headers=ch).json()["news"]
    assert [(n["type"], n["why"]) for n in news] == [("doc.mentioned", "mentioned")]

    got = _next(client, ch, doc_id=page["id"])
    assert got["item"] is None and got["chat"] is None and got["why"] == "mentioned"
    turn = got["doc"]
    assert turn["doc_id"] == page["id"] and turn["thread_id"] is None
    assert turn["title"] == "Roof plan" and turn["kind"] == "page" and turn["path"] == []
    # The section around the mention, not the whole page.
    assert turn["section_md"] == "## Shingles\n\nAsk @agent to price 30 squares.\n"
    assert turn["body_chars"] == len(BODY)
    assert turn["comments"] == [] and turn["mention"]["comment"] is None
    assert turn["mention"]["by_handle"] == "alex" and turn["mention"]["by_kind"] == "person"
    assert turn["may"] == {"reply": True, "edit": True}
    assert turn["lease"]["account_id"] == agent["id"]
    started = _events(client, alex, "doc.agent_turn")[-1]
    assert started["after"]["state"] == "started" and started["actor_id"] == agent["id"]
    # Nothing else is free.
    assert _next(client, ch, resume=False)["doc"] is None

    done = _finish(client, ch, doc_id=page["id"], outcome="done", reply="About $1,840.")
    assert done["pending"] is False
    reply = done["comment"]
    assert reply["body_md"] == "About $1,840." and reply["parent_id"] is None
    assert reply["surface"] == "discussion" and reply["author_id"] == agent["id"]
    comments = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    assert [c["id"] for c in comments] == [reply["id"]]
    ended = _events(client, alex, "doc.agent_turn")[-1]["after"]
    assert ended["state"] == "done" and ended["reply_id"] == reply["id"]
    assert _next(client, ch, doc_id=page["id"])["doc"] is None
    # The agent's own comment wakes nobody.
    assert len(_events(client, alex, "doc.mentioned_agent")) == 1


def test_a_thread_mention_is_answered_in_that_thread(client, alex):
    _, ch = _agent(client, alex)
    page = _doc(client, alex, title="Spec", body_md="The table is 14 ft.\n\nOther text.")
    root = _comment(
        client, alex, page["id"], body_md="Is this right?", anchor={"quote": "The table is 14 ft."}
    )
    ask = _comment(client, alex, page["id"], body_md="@agent check it", parent_id=root["id"])

    hint = _events(client, alex, "doc.mentioned_agent")[-1]["after"]
    assert hint["thread_id"] == root["id"] and hint["comment_id"] == ask["id"]
    got = _next(client, ch, doc_id=page["id"], thread_id=root["id"])
    turn = got["doc"]
    assert turn["thread_id"] == root["id"] and turn["comment_id"] == ask["id"]
    assert [c["id"] for c in turn["comments"]] == [root["id"], ask["id"]]
    assert turn["mention"]["comment"]["id"] == ask["id"]
    assert turn["section_md"].startswith("The table is 14 ft.")
    # The body turn is a different key: nothing there.
    assert _next(client, ch, doc_id=page["id"], resume=False)["doc"] is None

    done = _finish(
        client, ch, doc_id=page["id"], thread_id=root["id"], outcome="done", reply="Yes, 14 ft."
    )
    assert done["comment"]["parent_id"] == root["id"]
    comments = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    assert [c["body_md"] for c in comments][-1] == "Yes, 14 ft."


def test_an_ask_by_assignee_wakes_too(client, alex):
    agent, ch = _agent(client, alex)
    page = _doc(client, alex, title="Plan", body_md="Text")
    ask = _comment(client, alex, page["id"], body_md="Can you price it?", assignee="agent")
    after = _events(client, alex, "doc.mentioned_agent")[-1]["after"]
    assert after["for"] == [agent["id"]] and after["thread_id"] == ask["id"]
    assert _next(client, ch)["doc"]["thread_id"] == ask["id"]


def test_a_generic_next_hands_out_doc_turns_after_tasks_and_chat(client, alex, todo):
    _, ch = _agent(client, alex)
    page = _doc(client, alex, title="P", body_md="Hi @agent")
    r = client.post(
        f"{API}/channels/list:{todo['id']}/messages", json={"body_md": "@agent hi"}, headers=alex
    )
    assert r.status_code == 201
    first = _next(client, ch)
    assert first["chat"] is not None and first["doc"] is None and first["remaining"] == 1
    second = _next(client, ch)
    assert second["doc"]["doc_id"] == page["id"] and second["remaining"] == 0
    # kinds narrows what is handed out.
    other = _doc(client, alex, title="Q", body_md="Hi @agent")
    assert _next(client, ch, kinds=["task", "chat"])["doc"] is None
    assert _next(client, ch, kinds=["doc"])["doc"]["doc_id"] == other["id"]


def test_a_second_mention_while_a_turn_is_open_queues_into_it(client, alex):
    agent, ch = _agent(client, alex)
    page = _doc(client, alex, title="P", body_md="x")
    root = _comment(client, alex, page["id"], body_md="@agent one")
    _next(client, ch, doc_id=page["id"], thread_id=root["id"])
    _comment(client, alex, page["id"], body_md="@agent and two", parent_id=root["id"])
    after = _events(client, alex, "doc.mentioned_agent")[-1]["after"]
    assert after["for"] == [] and after["queued"] == [agent["id"]]

    done = _finish(client, ch, doc_id=page["id"], thread_id=root["id"], outcome="done", reply="1")
    assert done["pending"] is True
    assert _events(client, alex, "doc.agent_turn")[-1]["after"]["pending"] is True
    again = _next(client, ch, doc_id=page["id"], thread_id=root["id"])
    assert again["doc"]["mention"]["excerpt"] == "@agent and two"
    assert _finish(
        client, ch, doc_id=page["id"], thread_id=root["id"], outcome="release", note="x"
    ) == {"comment": None, "pending": False}


def test_agents_pull_agents_and_outsiders_are_not_woken(client, alex, todo):
    agent, agent_h = _agent(client, alex, "agent")
    _, agent2_h = _agent(client, alex, "agent2")
    _, pull_h = _agent(client, alex, "agent3", wake="pull")
    page = _doc(client, alex, title="P", body_md="x")
    # An agent's mention of an agent never wakes it.
    _comment(client, agent_h, page["id"], body_md="@agent2 over to you")
    _save(client, agent_h, page["id"], "x @agent2")
    # Pull agents get nothing new.
    _comment(client, alex, page["id"], body_md="@agent3 anyone?")
    assert _events(client, alex, "doc.mentioned_agent") == []
    assert _next(client, agent2_h)["doc"] is None and _next(client, pull_h)["doc"] is None

    # An agent that cannot see the page is not woken.
    r = client.patch(f"{API}/sections/{todo['section_id']}", json={"private": True}, headers=alex)
    assert r.status_code == 200, r.text
    hidden = _doc(client, alex, title="Hidden", list_id=todo["id"], body_md="@agent psst")
    assert _events(client, alex, "doc.mentioned_agent") == []
    assert client.get(f"{API}/docs/{hidden['id']}", headers=agent_h).status_code == 404


def test_release_posts_nothing_and_retry_returns_the_receipt(client, alex):
    _, ch = _agent(client, alex)
    page = _doc(client, alex, title="P", body_md="@agent hi")
    _next(client, ch, doc_id=page["id"])
    body = run_fields(client, ch, ("doc", page["id"], None), doc_id=page["id"], outcome="release")
    first = client.post(f"{API}/work/doc/finish", json=body, headers=ch)
    assert first.status_code == 200 and first.json() == {"comment": None, "pending": False}
    again = client.post(f"{API}/work/doc/finish", json=body, headers=ch)
    assert again.status_code == 200 and again.json() == first.json()
    assert client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json() == []
    assert _events(client, alex, "doc.agent_turn")[-1]["after"]["state"] == "released"


def test_heartbeat_expire_and_reconcile(client, alex, db):
    agent, ch = _agent(client, alex)
    page = _doc(client, alex, title="P", body_md="@agent slow one")
    got = _next(client, ch, doc_id=page["id"], lease_seconds=60)
    until = got["doc"]["lease"]["until"]
    r = client.post(
        f"{API}/work/doc/heartbeat",
        json=run_fields(
            client, ch, ("doc", page["id"], None), doc_id=page["id"], lease_seconds=600
        ),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert r.json()["until"] > until
    assert [e["after"]["state"] for e in _events(client, alex, "doc.agent_turn")] == ["started"]

    assert docwake.expire(db, utcnow() + timedelta(hours=1)) == 1
    ended = _events(client, alex, "doc.agent_turn")[-1]
    assert ended["after"]["state"] == "expired" and ended["source"] == "system"
    assert ended["after"]["reconciliation_required"] is True
    held = db.get(DocWake, (agent["id"], page["id"], ""))
    assert held is not None and held.until is None and held.run_id
    assert _next(client, ch, doc_id=page["id"])["doc"] is None
    # The expired turn cannot be finished; it is reconciled with its run proof.
    late = client.post(
        f"{API}/work/doc/finish",
        json=run_fields(client, ch, ("doc", page["id"], None), doc_id=page["id"], outcome="done"),
        headers=ch,
    )
    assert late.status_code == 409
    r = client.post(
        f"{API}/work/doc/reconcile",
        json=run_fields(
            client,
            ch,
            ("doc", page["id"], None),
            doc_id=page["id"],
            state="stopped",
            evidence="process exited",
        ),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(DocWake, (agent["id"], page["id"], "")) is None


def test_finish_without_a_turn_or_proof_is_a_409(client, alex):
    _, ch = _agent(client, alex)
    page = _doc(client, alex, title="P", body_md="x")
    r = client.post(
        f"{API}/work/doc/finish", json={"doc_id": page["id"], "outcome": "done"}, headers=ch
    )
    assert r.status_code == 409
    _save(client, alex, page["id"], "x @agent")
    _next(client, ch, doc_id=page["id"])
    r = client.post(
        f"{API}/work/doc/finish",
        json={"doc_id": page["id"], "outcome": "done", "run_id": "wrong"},
        headers=ch,
    )
    assert r.status_code == 409
    r = client.post(f"{API}/work/next", json={"thread_id": "abc"}, headers=ch)
    assert r.status_code == 422


def test_section_around():
    assert docwake.section_around("", "@x") == ""
    assert docwake.section_around("a\nb", "@x") == "a\nb"
    body = "# A\n\none\n\n## B\n\n@x here\n\n### C\n\nsub\n\n## D\n\nend"
    assert docwake.section_around(body, "@X") == "## B\n\n@x here\n\n### C\n\nsub\n"
    long = "\n".join(["filler"] * 3000 + ["@x"] + ["tail"] * 3000)
    cut = docwake.section_around(long, "@x")
    assert len(cut) == docwake.SECTION_MAX and "@x" in cut
