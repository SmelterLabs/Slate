"""Doc collaboration regressions: anchors, replies, Needs you, suggestions; isolated API only."""

import pytest
from sqlalchemy import select

from slate.models import Event
from test_doc_threads import API, _comment, _doc, _upload


def test_folded_page_save_invalidates_review_anchor(client, alex):
    page = _doc(client, alex, title="Plan", body_md="Seat 12")
    comment = _comment(client, alex, page["id"], body_md="Check", anchor={"quote": "Seat 12"})
    saved = client.patch(
        f"{API}/docs/{page['id']}", headers=alex, json={"body_md": "Seat 14"}
    ).json()
    assert saved["version_no"] == page["version_no"]
    comments = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    assert comments[0]["outdated"] is True
    assert comments[0]["anchor_page_revision"] == page["revision"]
    reply = _comment(client, alex, page["id"], body_md="Still checking", parent_id=comment["id"])
    assert reply["anchor_page_revision"] == page["revision"]
    assert reply["outdated"] is True


def test_viewed_file_revision_is_explicit_and_replies_keep_root(client, alex):
    doc = _doc(client, alex, title="Contract", kind="file")
    _upload(client, alex, doc["id"], b"%PDF-1", "one")
    _upload(client, alex, doc["id"], b"%PDF-2", "two")
    root = _comment(
        client, alex, doc["id"], body_md="Old date", viewed_revision=1, anchor={"page": 1}
    )
    assert root["anchor_revision"] == 1 and root["outdated"]
    reply = _comment(client, alex, doc["id"], body_md="Reply", parent_id=root["id"])
    assert reply["anchor_revision"] == 1
    _comment(client, alex, doc["id"], body_md="Invalid", viewed_revision=99, status=422)


def test_reply_keeps_ask_open_and_notifies_participants_once(client, alex, make_account, db):
    _, agent = make_account("agent")
    _, agent2 = make_account("agent2")
    doc = _doc(client, alex, title="Plan")
    ask = _comment(client, alex, doc["id"], body_md="Check this", assignee="agent")
    _comment(client, agent2, doc["id"], body_md="Following", parent_id=ask["id"])
    reply = _comment(client, agent, doc["id"], body_md="@alex still checking", parent_id=ask["id"])
    comments = client.get(f"{API}/docs/{doc['id']}/comments", headers=alex).json()
    assert comments[0]["resolved_at"] is None
    db.expire_all()
    events = [
        e
        for e in db.scalars(select(Event).where(Event.type == "doc.mentioned"))
        if e.after_json.get("comment_id") == reply["id"]
    ]
    assert len(events) == 1
    assert events[0].after_json["mentions"] == ["alex"]
    # Explicit mentions and implicit thread replies have distinct preference categories.
    alex_id = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    agent2_id = client.get(f"{API}/me", headers=agent2).json()["account"]["id"]
    assert sorted(events[0].after_json["reply_to"]) == sorted([alex_id, agent2_id])
    assert events[0].after_json["excerpt"] == "@alex still checking"


def test_prior_editor_stays_in_needs_you(client, alex, make_account, todo):
    _, agent = make_account("agent")
    _, agent2 = make_account("agent2")
    doc = _doc(client, agent, title="Plan", section_id=todo["section_id"], body_md="first")
    for who, body in [(alex, "second"), (agent, "third")]:
        client.patch(f"{API}/docs/{doc['id']}", headers=who, json={"body_md": body})
    comment = _comment(client, agent2, doc["id"], body_md="Check", anchor={"quote": "third"})
    hub = client.get(f"{API}/sections/{todo['section_id']}/documents", headers=alex).json()
    assert [n["comment"]["id"] for n in hub["needs_you"]] == [comment["id"]]


def test_pinned_revision_change_is_in_activity(client, alex):
    doc = _doc(client, alex, title="Contract", kind="file")
    _upload(client, alex, doc["id"], b"%PDF-1", "one")
    response = client.post(
        f"{API}/docs/{doc['id']}/revisions/1/pin", headers=alex, json={"note": "Signed copy"}
    )
    assert response.status_code == 200, response.text
    activity = client.get(f"{API}/docs/{doc['id']}/activity", headers=alex).json()
    assert any(e["type"] == "doc.revision_pinned" for e in activity["events"])


@pytest.mark.parametrize(
    "changed,anchor",
    [
        (True, {"quote": "Seat 12", "start": 3, "end": 10}),
        (False, {"quote": "Seat 12"}),
        (False, {"quote": "Seat 12", "start": 1, "end": 8}),
    ],
)
def test_stale_or_ambiguous_suggestion_never_replaces_another_span(client, alex, changed, anchor):
    body = "A: Seat 12\nB: Seat 12"
    page = _doc(client, alex, title="Plan", body_md=body)
    comment = _comment(
        client, alex, page["id"], body_md="Change A", anchor=anchor, suggestion_md="Seat 14"
    )
    if changed:
        body = "B: Seat 12"
        client.patch(f"{API}/docs/{page['id']}", headers=alex, json={"body_md": body})
    response = client.post(f"{API}/doc-comments/{comment['id']}/accept", headers=alex)
    assert response.status_code == 422
    assert client.get(f"{API}/docs/{page['id']}", headers=alex).json()["body_md"] == body
