"""Deleting a comment: the author deletes their own, an owner or admin anyone's. The comment and
its reactions go; one `comment.deleted` event keeps what it said; inboxes and check-in news drop
it; a project's guests still see no trace of a workspace agent's deleted comment."""

from __future__ import annotations

import pytest

from conftest import API
from slate.models import CommentReaction, Event


def _item(client, h, list_id, title="Plan the offsite"):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comment(client, h, item_id, body, **kw):
    r = client.post(f"{API}/items/{item_id}/comments", json={"body_md": body, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _ids(client, h, item_id):
    return [c["id"] for c in client.get(f"{API}/items/{item_id}/comments", headers=h).json()]


def _account(client, alex, handle, kind="person", role="member"):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": kind, "role": role},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    # Admin powers come with an admin token; a write token acts as an ordinary member.
    scopes = ["admin"] if role == "admin" else ["write"]
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": scopes}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def test_the_author_deletes_their_own_comment_and_its_reactions(client, alex, todo, db):
    _, mh = _account(client, alex, "member")
    item = _item(client, alex, todo["id"])
    keep = _comment(client, mh, item["id"], "First thought")
    gone = _comment(client, mh, item["id"], "Never mind that")
    client.post(f"{API}/comments/{gone['id']}/reactions", json={"emoji": "👍"}, headers=alex)

    r = client.delete(f"{API}/comments/{gone['id']}", headers=mh)
    assert r.status_code == 204, r.text
    assert _ids(client, alex, item["id"]) == [keep["id"]]
    assert db.query(CommentReaction).filter_by(comment_id=gone["id"]).count() == 0

    (ev,) = db.query(Event).filter_by(type="comment.deleted").all()
    assert ev.subject_id == gone["id"] and ev.item_id == item["id"]
    assert ev.before_json["body_md"] == "Never mind that"
    assert ev.before_json["reactions"][0]["emoji"] == "👍"
    # Gone means gone: a second delete is a 404, and so is any other write to it.
    assert client.delete(f"{API}/comments/{gone['id']}", headers=mh).status_code == 404
    activity = client.get(f"{API}/items/{item['id']}/activity", headers=alex).json()
    assert gone["id"] not in [e["comment"]["id"] for e in activity if e["kind"] == "comment"]


def test_only_the_author_or_an_admin_may_delete(client, alex, todo):
    _, mh = _account(client, alex, "member")
    _, oh = _account(client, alex, "other")
    _, ah = _account(client, alex, "admin2", role="admin")
    item = _item(client, alex, todo["id"])
    theirs = _comment(client, mh, item["id"], "The member's note")
    alexs = _comment(client, alex, item["id"], "Alex's note")

    # A member may not delete someone else's comment, not even the owner's.
    assert client.delete(f"{API}/comments/{theirs['id']}", headers=oh).status_code == 403
    assert client.delete(f"{API}/comments/{alexs['id']}", headers=mh).status_code == 403
    assert _ids(client, alex, item["id"]) == [theirs["id"], alexs["id"]]
    # An admin may delete anyone's; so may the owner.
    assert client.delete(f"{API}/comments/{theirs['id']}", headers=ah).status_code == 204
    third = _comment(client, ah, item["id"], "The admin's note")
    assert client.delete(f"{API}/comments/{third['id']}", headers=alex).status_code == 204
    assert _ids(client, alex, item["id"]) == [alexs["id"]]


def test_deleting_an_ask_closes_it_and_takes_it_out_of_the_inbox(client, alex, todo):
    member, mh = _account(client, alex, "member")
    item = _item(client, alex, todo["id"])
    ask = _comment(client, alex, item["id"], "@member can you book it?", assignee="member")
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"] == [
        member["id"]
    ]
    inbox = client.get(f"{API}/inbox", headers=mh).json()
    assert any(e["event"]["subject_id"] == ask["id"] for e in inbox)

    assert client.delete(f"{API}/comments/{ask['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"] == []
    inbox = client.get(f"{API}/inbox", headers=mh).json()
    assert not any(e["event"]["subject_id"] == ask["id"] for e in inbox)
    assert client.get(f"{API}/inbox", headers=alex).status_code == 200


def test_check_in_news_drops_a_deleted_mention(client, alex, todo, make_account):
    _, agent_h = make_account("helper")
    item = _item(client, alex, todo["id"])
    said = _comment(client, alex, item["id"], "@helper look at this")
    news = client.get(f"{API}/checkin", headers=agent_h).json()["news"]
    assert any(n["subject_id"] == said["id"] for n in news), news

    assert client.delete(f"{API}/comments/{said['id']}", headers=alex).status_code == 204
    news = client.get(f"{API}/checkin", headers=agent_h).json()["news"]
    assert not any(n["subject_id"] == said["id"] for n in news)


@pytest.fixture
def project(client, alex):
    sec = client.post(
        f"{API}/sections", json={"name": "Example Project", "audience": "personal"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    lst = client.post(
        f"{API}/lists", json={"section_id": sec["id"], "name": "Work"}, headers=alex
    ).json()
    agent, ah = _account(client, alex, "agent", kind="agent")
    guest, gh = _account(client, alex, "guest", role="guest")
    for a in (agent, guest):
        r = client.put(
            f"{API}/shares",
            json={
                "account_id": a["id"],
                "scope_kind": "section",
                "scope_id": sec["id"],
                "level": "edit",
            },
            headers=alex,
        )
        assert r.status_code == 200, r.text
    return {"list": lst, "agent": (agent, ah), "guest": (guest, gh)}


def test_a_guest_sees_no_trace_of_a_deleted_agent_comment(client, alex, project, db):
    from slate.api.events import _gate_check
    from slate.service.access import EventGate
    from slate.service.core import event_payload

    _, ah = project["agent"]
    guest, gh = project["guest"]
    item = _item(client, alex, project["list"]["id"], "Shared card")
    ask = _comment(client, alex, item["id"], "Private question for the agent", assignee="agent")
    said = _comment(client, ah, item["id"], "Agent's private answer")
    mine = _comment(client, gh, item["id"], "Guest's own note")
    for c in (ask, said, mine):
        assert client.delete(f"{API}/comments/{c['id']}", headers=alex).status_code == 204

    seen = client.get(f"{API}/items/{item['id']}/events", headers=gh).json()
    subjects = {e["subject_id"] for e in seen}
    assert ask["id"] not in subjects and said["id"] not in subjects
    assert "Private question" not in str(seen) and "private answer" not in str(seen)
    assert mine["id"] in subjects  # a guest's own comment and its deletion stay visible

    me = client.get(f"{API}/me", headers=gh).json()
    gate = EventGate(guest["id"], me["token_id"])
    gate.load(db)

    def deleted(cid):
        ev = db.query(Event).filter_by(type="comment.deleted", subject_id=cid).one()
        return event_payload(ev)

    assert not _gate_check(gate, deleted(ask["id"]))
    assert not _gate_check(gate, deleted(said["id"]))
    assert _gate_check(gate, deleted(mine["id"]))
