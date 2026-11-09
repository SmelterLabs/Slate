"""Discussion and review threads on docs.

Two surfaces, one table: a *review thread* is anchored (a span of a page's text, a page number
or a spot on a file) and can be resolved; *discussion* is the unanchored conversation. Every
comment is stamped with the revision it was written on, stays when the doc moves on, and is
marked outdated when the current revision differs. Mentions and Asks ride on `doc.mentioned`,
so the inbox, check-in, push and webhooks hear them as they hear a page mention.
"""

from __future__ import annotations

from sqlalchemy import select

from slate.models import Event
from slate.service import push

API = "/api/v1"


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comment(client, h, doc_id, status=201, **body):
    r = client.post(f"{API}/docs/{doc_id}/comments", json=body, headers=h)
    assert r.status_code == status, r.text
    return r.json()


def _upload(client, h, doc_id, data, note):
    r = client.post(
        f"{API}/docs/{doc_id}/revisions",
        params={"filename": "spec.pdf", "note": note},
        content=data,
        headers={**h, "Content-Type": "application/pdf"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_discussion_and_review_threads_on_a_page(client, alex, make_account, db):
    _, agent = make_account("agent")
    page = _doc(
        client, alex, title="Scope", body_md="## Scope\n\nSeat 12 guests.\n\nConfirm by Friday."
    )

    talk = _comment(client, alex, page["id"], body_md="The venue wants this by the 10th.")
    assert talk["surface"] == "discussion" and talk["anchor_revision"] == 1
    assert talk["parent_id"] is None and talk["resolved_at"] is None and talk["outdated"] is False

    review = _comment(
        client,
        agent,
        page["id"],
        body_md="Should this be 14? The guest list has 14 names.",
        anchor={"quote": "Seat 12 guests.", "start": 10, "end": 25},
    )
    assert review["surface"] == "review" and review["anchor_quote"] == "Seat 12 guests."
    reply = _comment(client, alex, page["id"], body_md="Yes, 14.", parent_id=review["id"])
    assert reply["parent_id"] == review["id"] and reply["surface"] == "review"
    # A reply inherits the thread's anchor and surface; nesting stays one level deep.
    assert reply["anchor_quote"] == "Seat 12 guests."
    assert (
        "reply"
        in _comment(client, alex, page["id"], status=422, body_md="x", parent_id=reply["id"])[
            "detail"
        ]
    )

    listed = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    assert [c["id"] for c in listed] == [talk["id"], review["id"], reply["id"]]
    got = client.get(f"{API}/docs/{page['id']}", headers=alex).json()
    assert got["comment_count"] == 3 and got["open_threads"] == 1

    # Resolve the review thread; replies cannot be resolved on their own.
    r = client.post(f"{API}/doc-comments/{review['id']}/resolve", headers=alex)
    assert r.status_code == 200 and r.json()["resolved_at"] and r.json()["resolved_by_id"]
    assert client.post(f"{API}/doc-comments/{reply['id']}/resolve", headers=alex).status_code == 422
    assert client.get(f"{API}/docs/{page['id']}", headers=alex).json()["open_threads"] == 0
    r = client.post(f"{API}/doc-comments/{review['id']}/reopen", headers=alex)
    assert r.status_code == 200 and r.json()["resolved_at"] is None

    # Only the author edits.
    r = client.patch(
        f"{API}/doc-comments/{talk['id']}", json={"body_md": "By the 12th."}, headers=agent
    )
    assert r.status_code == 403
    r = client.patch(
        f"{API}/doc-comments/{talk['id']}", json={"body_md": "By the 12th."}, headers=alex
    )
    assert r.status_code == 200 and r.json()["edited_at"]

    db.expire_all()
    types = [
        e.type
        for e in db.scalars(select(Event).where(Event.subject_id == page["id"]).order_by(Event.id))
    ]
    assert types.count("doc.commented") == 3
    assert "doc.comment_resolved" in types and "doc.comment_edited" in types
    ev = db.scalars(select(Event).where(Event.type == "doc.commented")).first()
    assert ev.after_json["comment_id"] == talk["id"] and ev.after_json["surface"] == "discussion"


def test_comments_are_stamped_with_the_revision_and_marked_outdated(client, alex):
    spec = _doc(client, alex, title="Spec", kind="file")
    _upload(client, alex, spec["id"], b"%PDF-1", "first")
    pin = _comment(
        client,
        alex,
        spec["id"],
        body_md="This date is wrong",
        anchor={"page": 2, "x": 0.4, "y": 0.6},
    )
    assert pin["surface"] == "review" and pin["anchor_revision"] == 1
    assert pin["anchor_page"] == 2 and pin["anchor_x"] == 0.4 and pin["outdated"] is False
    _upload(client, alex, spec["id"], b"%PDF-2", "fixed the date")
    listed = client.get(f"{API}/docs/{spec['id']}/comments", headers=alex).json()
    assert listed[0]["anchor_revision"] == 1 and listed[0]["outdated"] is True
    fresh = _comment(client, alex, spec["id"], body_md="Looks right now")
    assert fresh["anchor_revision"] == 2 and fresh["outdated"] is False
    # A page's comments stamp the page-history version.
    page = _doc(client, alex, title="P", body_md="one")
    c = _comment(client, alex, page["id"], body_md="hi")
    assert c["anchor_revision"] == 1
    client.patch(
        f"{API}/docs/{page['id']}", json={"body_md": "two", "note": "Sealed"}, headers=alex
    )
    c2 = _comment(client, alex, page["id"], body_md="again")
    assert (
        c2["anchor_revision"] == 1
    )  # the sitting folded into version 1 (same author, minutes ago)
    # A spot needs both coordinates in range.
    _comment(client, alex, spec["id"], status=422, body_md="x", anchor={"x": 1.4})


def test_mentions_and_asks_reach_inbox_checkin_push_and_require_resolution(
    client, alex, make_account, db
):
    _, agent = make_account("agent")
    page = _doc(client, alex, title="Release plan", body_md="Intro")
    ask = _comment(
        client, alex, page["id"], body_md="Can you estimate the timeline?", assignee="agent"
    )
    assert ask["assignee_id"] and ask["resolved_at"] is None

    news = client.get(f"{API}/checkin", headers=agent).json()["news"]
    assert [(n["type"], n["why"]) for n in news] == [("doc.mentioned", "mentioned")]
    entries = client.get(f"{API}/inbox", headers=agent).json()
    assert [(e["reason"], e["doc_id"], e["item_title"]) for e in entries] == [
        ("mention", page["id"], "Release plan")
    ]
    assert client.get(f"{API}/inbox", headers=alex).json() == []
    db.expire_all()
    ev = db.scalars(select(Event).where(Event.type == "doc.mentioned")).one()
    assert ev.after_json["mentions"] == ["agent"] and ev.after_json["comment_id"] == ask["id"]
    note = push.notification_for(db, ev, "mention")
    assert note["title"] == "Alex asked you in Release plan"
    assert note["url"] == f"/#/docs/{page['id']}?comment={ask['id']}"

    # The agent's reply notifies the asker but does not complete the Ask.
    _comment(client, agent, page["id"], body_md="About two weeks.", parent_id=ask["id"])
    got = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    root = next(c for c in got if c["id"] == ask["id"])
    assert root["resolved_at"] is None and root["resolved_by_id"] is None
    # A mention inside a comment is one doc.mentioned with the comment's excerpt.
    _comment(client, alex, page["id"], body_md="Thanks @agent, go ahead.")
    db.expire_all()
    evs = db.scalars(select(Event).where(Event.type == "doc.mentioned").order_by(Event.id)).all()
    assert len(evs) == 3 and evs[2].after_json["excerpt"] == "Thanks @agent, go ahead."


def test_accepting_a_suggestion_edits_the_page_and_resolves_the_thread(
    client, alex, make_account, db
):
    _, agent = make_account("agent")
    page = _doc(
        client, alex, title="Quote", body_md="Price: $1,200 per table.\n\nLead time: 3 weeks."
    )
    sug = _comment(
        client,
        agent,
        page["id"],
        body_md="The caterer raised it; the new price is 1,350.",
        anchor={"quote": "$1,200 per table", "start": 7, "end": 7 + len("$1,200 per table")},
        suggestion_md="$1,350 per table",
    )
    assert sug["suggestion_md"] == "$1,350 per table"
    r = client.post(f"{API}/doc-comments/{sug['id']}/accept", headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["doc"]["body_md"] == "Price: $1,350 per table.\n\nLead time: 3 weeks."
    assert r.json()["comment"]["resolved_at"] and r.json()["comment"]["accepted_at"]
    # Accepting twice, or when the quoted text is gone, is refused; a file doc has no suggestions.
    assert client.post(f"{API}/doc-comments/{sug['id']}/accept", headers=alex).status_code == 422
    gone = _comment(
        client,
        agent,
        page["id"],
        body_md="x",
        anchor={"quote": "not in the page"},
        suggestion_md="y",
    )
    assert client.post(f"{API}/doc-comments/{gone['id']}/accept", headers=alex).status_code == 422
    spec = _doc(client, alex, title="S", kind="file")
    assert _comment(
        client, alex, spec["id"], status=422, body_md="x", anchor={"page": 1}, suggestion_md="y"
    )
    db.expire_all()
    types = [
        e.type
        for e in db.scalars(select(Event).where(Event.subject_id == page["id"]).order_by(Event.id))
    ]
    assert "doc.suggestion_accepted" in types and types.count("doc.updated") >= 1


def test_activity_merges_revisions_and_thread_events_for_the_side_panel(client, alex):
    spec = _doc(client, alex, title="Spec", kind="file")
    _upload(client, alex, spec["id"], b"%PDF-1", "first")
    c = _comment(client, alex, spec["id"], body_md="Check page 3", anchor={"page": 3})
    _upload(client, alex, spec["id"], b"%PDF-2", "fixed page 3")
    client.post(f"{API}/doc-comments/{c['id']}/resolve", headers=alex)
    client.patch(f"{API}/docs/{spec['id']}", json={"title": "Spec v2"}, headers=alex)
    act = client.get(f"{API}/docs/{spec['id']}/activity", headers=alex).json()
    kinds = [(e["type"], e.get("field")) for e in act["events"]]
    assert ("doc.revision_added", None) in kinds and ("doc.updated", "title") in kinds
    assert ("doc.comment_resolved", "resolved_at") in kinds
    # Comment bodies live in `comments`, not in the event list (no doc.commented rows there).
    assert not any(t == "doc.commented" for t, _ in kinds)
    assert [x["id"] for x in act["comments"]] == [c["id"]]


def test_doc_comments_follow_doc_access_and_cascade(client, alex, make_account, db):
    sec = client.post(
        f"{API}/sections", json={"name": "Private", "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    page = _doc(client, alex, title="Hidden", section_id=sec["id"])
    c = _comment(client, alex, page["id"], body_md="secret")
    _, other = make_account("other", kind="person")
    assert client.get(f"{API}/docs/{page['id']}/comments", headers=other).status_code == 404
    assert (
        client.post(
            f"{API}/docs/{page['id']}/comments", json={"body_md": "hi"}, headers=other
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"{API}/doc-comments/{c['id']}", json={"body_md": "x"}, headers=other
        ).status_code
        == 404
    )
    # Deleting the doc takes its comments with it.
    client.post(f"{API}/docs/{page['id']}/archive", headers=alex)
    assert client.delete(f"{API}/docs/{page['id']}", headers=alex).status_code == 200
    from slate.models import DocComment

    db.expire_all()
    assert db.scalar(select(DocComment).where(DocComment.id == c["id"])) is None
