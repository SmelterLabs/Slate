"""Files on chat messages (service/chat_files.py): upload to a channel, send with a message,
read under the message's own visibility, and go with the message when it is deleted."""

from __future__ import annotations

import base64
from datetime import timedelta

from sqlalchemy import select

from conftest import API, raw_write
from slate.models import Event, MessageFile, utcnow
from slate.service import chat_files

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def upload(client, headers, key, name="shot.png", data=PNG, ctype="image/png"):
    r = client.post(
        f"{API}/channels/{key}/files",
        params={"filename": name},
        content=data,
        headers={**headers, "Content-Type": ctype},
    )
    assert r.status_code == 201, r.text
    return r.json()


def post(client, headers, key, body="", **extra):
    return client.post(
        f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=headers
    )


def test_a_message_carries_the_files_sent_with_it(client, alex, todo, make_account, db):
    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    f = upload(client, alex, key)
    assert f["filename"] == "shot.png" and f["content_type"] == "image/png"
    assert f["size"] == len(PNG)

    r = post(client, alex, key, "what is this error?", file_ids=[f["id"]])
    assert r.status_code == 201, r.text
    msg = r.json()
    assert msg["files"] == [f]

    # Whoever reads the channel reads the file, with its type, and never as a script.
    got = client.get(f"{API}/message-files/{f['id']}", headers=agent)
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]

    # The page and the thread carry it too, and so does the event an agent is woken by.
    page = client.get(f"{API}/channels/{key}/messages", headers=agent).json()
    assert page[-1]["files"] == [f]
    assert client.get(f"{API}/messages/{msg['id']}/thread", headers=agent).json()[0]["files"] == [f]
    ev = db.scalars(select(Event).where(Event.type == "message.posted")).all()[-1]
    assert ev.after_json["files"] == [f]


def test_a_message_can_be_only_files_but_never_nothing(client, alex, todo):
    key = f"list:{todo['id']}"
    f = upload(client, alex, key)
    r = post(client, alex, key, "", file_ids=[f["id"]])
    assert r.status_code == 201, r.text
    assert r.json()["body_md"] == "" and len(r.json()["files"]) == 1
    assert post(client, alex, key, "").status_code == 422
    assert post(client, alex, key, "   ", file_ids=[]).status_code == 422


def test_only_your_own_unsent_file_in_this_channel_can_be_sent(client, alex, todo, make_account):
    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    mine = upload(client, alex, key)
    theirs = upload(client, agent, key)
    elsewhere = upload(client, alex, "general")

    assert post(client, alex, key, "x", file_ids=[theirs["id"]]).status_code == 422
    assert post(client, alex, key, "x", file_ids=[elsewhere["id"]]).status_code == 422
    assert post(client, alex, key, "x", file_ids=["nope"]).status_code == 422
    assert post(client, alex, key, "x", file_ids=[mine["id"]]).status_code == 201
    # Sent once: it belongs to that message now.
    assert post(client, alex, key, "again", file_ids=[mine["id"]]).status_code == 422

    many = [upload(client, alex, key, name=f"{i}.png")["id"] for i in range(11)]
    assert post(client, alex, key, "x", file_ids=many).status_code == 422


def test_an_unsent_file_is_its_uploaders_alone(client, alex, todo, make_account, db):
    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    f = upload(client, alex, key)
    assert client.get(f"{API}/message-files/{f['id']}", headers=alex).status_code == 200
    assert client.get(f"{API}/message-files/{f['id']}", headers=agent).status_code == 404
    assert client.delete(f"{API}/message-files/{f['id']}", headers=agent).status_code == 404

    # The upload is recorded, by its uploader, with no channel: nobody else hears of it.
    ev = db.scalars(select(Event).where(Event.type == "message.file_uploaded")).one()
    assert ev.after_json == f and ev.subject_id == ev.actor_id

    assert client.delete(f"{API}/message-files/{f['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/message-files/{f['id']}", headers=alex).status_code == 404
    assert post(client, alex, key, "x", file_ids=[f["id"]]).status_code == 422

    sent = upload(client, alex, key)
    assert post(client, alex, key, "x", file_ids=[sent["id"]]).status_code == 201
    assert client.delete(f"{API}/message-files/{sent['id']}", headers=alex).status_code == 422


def test_deleting_the_message_deletes_its_files(client, alex, todo, db):
    key = f"list:{todo['id']}"
    f = upload(client, alex, key)
    msg = post(client, alex, key, "oops", file_ids=[f["id"]]).json()
    r = client.delete(f"{API}/messages/{msg['id']}", headers=alex)
    assert r.status_code == 200 and r.json()["files"] == []
    assert client.get(f"{API}/message-files/{f['id']}", headers=alex).status_code == 404
    assert db.get(MessageFile, f["id"]) is None


def test_a_files_only_message_keeps_its_files_when_its_text_is_edited_away(client, alex, todo):
    key = f"list:{todo['id']}"
    f = upload(client, alex, key)
    msg = post(client, alex, key, "caption", file_ids=[f["id"]]).json()
    r = client.patch(f"{API}/messages/{msg['id']}", json={"body_md": ""}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["body_md"] == "" and r.json()["files"] == [f]
    plain = post(client, alex, key, "words").json()
    assert (
        client.patch(f"{API}/messages/{plain['id']}", json={"body_md": ""}, headers=alex)
    ).status_code == 422


def test_a_task_made_from_a_message_gets_its_files(client, alex, todo):
    key = f"list:{todo['id']}"
    f = upload(client, alex, key, name="plan.pdf", data=b"%PDF-1.4 x", ctype="application/pdf")
    msg = post(client, alex, key, "File the permit", file_ids=[f["id"]]).json()
    item = client.post(f"{API}/messages/{msg['id']}/task", json={}, headers=alex).json()
    atts = client.get(f"{API}/items/{item['id']}/attachments", headers=alex).json()
    assert [(a["filename"], a["content_type"], a["size"]) for a in atts] == [
        ("plan.pdf", "application/pdf", 10)
    ]
    # The message keeps its own copy.
    assert client.get(f"{API}/message-files/{f['id']}", headers=alex).content == b"%PDF-1.4 x"


def test_a_file_is_read_only_where_its_message_is(client, alex):
    sec = client.post(
        f"{API}/sections", json={"name": "Example Project", "audience": "personal"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    other = client.post(
        f"{API}/sections", json={"name": "Elsewhere", "audience": "personal"}, headers=alex
    ).json()

    def account(handle, kind, role):
        acct = client.post(
            f"{API}/accounts",
            json={"handle": handle, "display_name": handle.title(), "kind": kind, "role": role},
            headers=alex,
        ).json()
        tok = client.post(
            f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
        ).json()
        return acct, {"Authorization": f"Bearer {tok['token']}"}

    guest, gh = account("guest", "person", "guest")
    agent, _ = account("agent", "agent", "member")
    for a in (guest, agent):
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
    key = f"section:{sec['id']}"

    shown = upload(client, alex, key)
    assert post(client, alex, key, "for everyone", file_ids=[shown["id"]]).status_code == 201
    assert client.get(f"{API}/message-files/{shown['id']}", headers=gh).status_code == 200

    # A message to a workspace agent is members-only: so is its file.
    hidden = upload(client, alex, key)
    msg = post(client, alex, key, "@agent look", file_ids=[hidden["id"]]).json()
    assert msg["members_only"] is True
    assert client.get(f"{API}/message-files/{hidden['id']}", headers=gh).status_code == 404

    # A channel the guest cannot read: no upload, no file.
    away = upload(client, alex, f"section:{other['id']}")
    assert (
        post(client, alex, f"section:{other['id']}", "x", file_ids=[away["id"]]).status_code == 201
    )
    assert client.get(f"{API}/message-files/{away['id']}", headers=gh).status_code == 404
    r = client.post(
        f"{API}/channels/section:{other['id']}/files",
        params={"filename": "a.png"},
        content=PNG,
        headers={**gh, "Content-Type": "image/png"},
    )
    assert r.status_code == 404
    # In its own project the guest sends files like anyone.
    own = upload(client, gh, key)
    assert post(client, gh, key, "mine", file_ids=[own["id"]]).status_code == 201


def test_uploads_are_checked_like_attachments(client, alex, todo):
    key = f"list:{todo['id']}"
    r = client.post(
        f"{API}/channels/{key}/files",
        params={"filename": "a.png"},
        content=b"",
        headers={**alex, "Content-Type": "image/png"},
    )
    assert r.status_code == 422
    # A type that is not a plain `type/subtype` is replaced by the name's.
    f = upload(client, alex, key, name="notes.txt", data=b"hi", ctype="text/html\r\nX: y")
    assert f["content_type"] == "text/plain"
    b64 = client.post(
        f"{API}/channels/{key}/files/base64",
        json={"filename": "b.png", "content_base64": base64.b64encode(PNG).decode()},
        headers=alex,
    )
    assert b64.status_code == 201 and b64.json()["content_type"] == "image/png"
    client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=alex)
    r = client.post(
        f"{API}/channels/{key}/files",
        params={"filename": "a.png"},
        content=PNG,
        headers={**alex, "Content-Type": "image/png"},
    )
    assert r.status_code == 422


def test_files_never_sent_are_swept_on_the_next_upload(client, alex, todo, db):
    key = f"list:{todo['id']}"
    stale = upload(client, alex, key, name="stale.png")
    fresh = upload(client, alex, key, name="fresh.png")
    sent = upload(client, alex, key, name="sent.png")
    assert post(client, alex, key, "x", file_ids=[sent["id"]]).status_code == 201
    old = utcnow() - chat_files.UNSENT_KEEP - timedelta(minutes=1)
    # Age the rows behind the ORM (the service never rewrites `created_at`).
    db.commit()
    raw_write(
        "UPDATE message_files SET created_at = :at WHERE id IN (:a, :b)",
        {"at": old.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "a": stale["id"], "b": sent["id"]},
    )

    upload(client, alex, key, name="next.png")
    db.expire_all()
    assert db.get(MessageFile, stale["id"]) is None
    assert db.get(MessageFile, fresh["id"]) is not None
    assert db.get(MessageFile, sent["id"]) is not None
    assert db.scalars(select(Event).where(Event.type == "message.file_discarded")).all()
