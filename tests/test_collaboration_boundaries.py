"""Adversarial private project surface matrix; real REST, MCP, batch and event gates."""

import pytest
from sqlalchemy import select

from slate.models import Event
from slate.service.core import event_payload
from test_collaboration import API, BROWSER, accept, invite
from test_mcp import call, err, http_mcp, ok


@pytest.fixture
def project_world(client, alex, todo, monkeypatch):
    from slate.api import collaboration

    monkeypatch.setattr(collaboration, "attempts", collaboration.Attempts())

    def post(path, **body):
        r = client.post(API + path, json=body, headers=alex)
        assert r.status_code in (200, 201), r.text
        return r.json()

    other = post("/sections", name="Secret unrelated section", audience="personal")
    hidden = post("/lists", section_id=other["id"], name="Secret unrelated list")
    private = post("/lists", section_id=todo["section_id"], name="Secret private list")
    assert (
        client.patch(
            f"{API}/lists/{private['id']}", json={"private": True}, headers=alex
        ).status_code
        == 200
    )
    rows = []
    for lst, title in [
        (todo, "Shared item"),
        (hidden, "Secret item"),
        (private, "Secret private item"),
    ]:
        item = post("/items", list_id=lst["id"], title=title)
        doc = post("/docs", list_id=lst["id"], title=title + " doc", body_md=title)
        child = post("/docs", parent_id=doc["id"], title=title + " child")
        attachment = client.post(
            f"{API}/items/{item['id']}/attachments?filename=notes.txt",
            content=title.encode(),
            headers={**alex, "Content-Type": "text/plain"},
        )
        assert attachment.status_code == 201, attachment.text
        file = client.post(
            f"{API}/docs/{doc['id']}/files?filename=notes.txt",
            content=title.encode(),
            headers={**alex, "Content-Type": "text/plain"},
        )
        assert file.status_code == 201, file.text
        rows.append(
            dict(
                lst=lst,
                item=item,
                doc=doc,
                child=child,
                attachment=attachment.json(),
                file=file.json(),
            )
        )
    outsider = post(
        "/accounts",
        handle="unrelated-person",
        display_name="Secret Person",
        kind="person",
        role="guest",
    )
    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    me = client.get(API + "/me").json()
    return {
        "rows": rows,
        "invitation": invitation,
        "me": me,
        "outsider": outsider,
        "other": other,
        "post": post,
    }


def test_rest_search_docs_history_files_attachments_export(project_world, client, alex):
    shared, *hidden = project_world["rows"]
    for row in hidden:
        for path in [
            f"/items/{row['item']['id']}",
            f"/docs/{row['doc']['id']}",
            f"/docs/{row['child']['id']}",
            f"/docs/{row['doc']['id']}/versions",
            f"/docs/{row['doc']['id']}/versions/1",
            f"/doc-files/{row['file']['id']}",
            f"/attachments/{row['attachment']['id']}",
        ]:
            assert client.get(API + path).status_code == 404, path
        for path in [f"/items/{row['item']['id']}", f"/docs/{row['doc']['id']}"]:
            assert (
                client.patch(API + path, json={"title": "escape"}, headers=BROWSER).status_code
                == 404
            )
        assert (
            client.post(
                API + "/lists",
                json={"section_id": row["lst"]["section_id"], "name": "escape"},
                headers=BROWSER,
            ).status_code
            in (403, 404)
            if row["lst"]["section_id"] != shared["lst"]["section_id"]
            else True
        )
    for path in [
        "/items?q=Secret",
        "/docs?q=Secret",
        "/export",
        "/events",
        "/inbox",
        "/sections",
        "/lists",
    ]:
        r = client.get(API + path)
        assert r.status_code == 200, (path, r.text)
        assert "Secret" not in r.text, (path, r.text)
    for path in [
        f"/docs/{shared['doc']['id']}/versions",
        f"/doc-files/{shared['file']['id']}",
        f"/attachments/{shared['attachment']['id']}",
    ]:
        assert client.get(API + path).status_code == 200
    assert (
        client.post(API + "/docs", json={"title": "Workspace escape"}, headers=BROWSER).status_code
        == 403
    )
    assert client.get(API + "/admin/backup").status_code == 403
    assert client.get(f"{API}/sections/{project_world['other']['id']}/project").status_code == 404


def test_participants_changes_are_live_without_exposing_share_records(
    project_world, client, alex, db
):
    from slate.api.events import _gate_check
    from slate.service.access import EventGate

    me = project_world["me"]
    sid = project_world["rows"][0]["lst"]["section_id"]
    gate = EventGate(me["account"]["id"], me["token_id"])
    gate.load(db)
    response = client.put(
        API + "/shares",
        headers=alex,
        json={
            "account_id": project_world["outsider"]["id"],
            "scope_kind": "section",
            "scope_id": sid,
            "level": "edit",
        },
    )
    assert response.status_code == 200
    event = db.scalar(
        select(Event).where(Event.type == "section.participants_changed").order_by(Event.id.desc())
    )
    assert event is not None and event.subject_id == sid
    assert event.before_json is None and event.after_json is None
    assert _gate_check(gate, event_payload(event))
    assert "Secret Person" in client.get(f"{API}/sections/{sid}/project").text


def test_scoped_roster_hides_unrelated_identity_and_presence(project_world, client, alex):
    response = client.get(API + "/agents?kind=all")
    assert response.status_code == 200
    assert "Secret Person" not in response.text
    for row in response.json():
        assert row["last_seen_at"] is None
        assert row["live_streams"] == 0 and not row["online"]
        assert row["throttled"] == 0


def test_batch_scoped_reads_writes_and_reorder_denial(project_world, client, alex):
    shared, hidden, _ = project_world["rows"]

    def batch(ops):
        return client.post(API + "/batch", headers=BROWSER, json={"ops": ops})

    good = batch(
        [
            {
                "tool": "item",
                "action": "update",
                "params": {"id": shared["item"]["id"], "title": "Changed"},
            },
            {"tool": "item", "action": "list", "params": {"q": "Secret"}},
        ]
    )
    assert good.status_code == 200, good.text
    assert "Secret" not in good.text
    denied = batch(
        [
            {
                "tool": "item",
                "action": "update",
                "params": {"id": shared["item"]["id"], "title": "Rollback"},
            },
            {
                "tool": "item",
                "action": "update",
                "params": {"id": hidden["item"]["id"], "title": "Escape"},
            },
        ]
    )
    assert denied.status_code == 404, denied.text
    assert client.get(f"{API}/items/{shared['item']['id']}").json()["title"] == "Changed"
    denied = batch(
        [
            {
                "tool": "section",
                "action": "update",
                "params": {"id": shared["lst"]["section_id"], "position": 999},
            }
        ]
    )
    assert denied.status_code == 403
    for action in ("get", "versions"):
        denied = batch([{"tool": "doc", "action": action, "params": {"id": hidden["doc"]["id"]}}])
        assert denied.status_code == 404


async def test_mcp_read_write_and_revoked_access(project_world, client, alex):
    shared, hidden, _ = project_world["rows"]
    # Browser credentials are not returned to JS. Test the same underlying limited token
    # over the existing bearer MCP door, as a stolen/forwarded credential must not widen it.
    headers = {"Authorization": f"Bearer {client.cookies.get('slate_session')}"}
    async with http_mcp(headers) as mcp:
        found = ok(await call(mcp, "slate_item", "list"))
        assert "Secret" not in str(found)
        ok(await call(mcp, "slate_item", "update", id=shared["item"]["id"], title="MCP change"))
        assert "Secret" not in str(ok(await call(mcp, "slate_doc", "list", q="Secret")))
        ok(await call(mcp, "slate_doc", "update", id=shared["doc"]["id"], append_md="Guest edit"))
        for action, extra in [
            ("get", {}),
            ("versions", {}),
            ("version", {"version_no": 1}),
            ("restore", {"version_no": 1}),
            ("attach", {"filename": "x.txt", "content_base64": "eA=="}),
        ]:
            assert "not found" in err(
                await call(mcp, "slate_doc", action, id=hidden["doc"]["id"], **extra)
            )
        assert "not found" in err(
            await call(mcp, "slate_doc", "remove_file", file_id=hidden["file"]["id"])
        )
        assert "not found" in err(await call(mcp, "slate_item", "get", id=hidden["item"]["id"]))
        assert "guest" in err(
            await call(mcp, "slate_section", "create", name="Escape", audience="work")
        )
        assert (
            client.put(
                API + "/shares",
                json={
                    "account_id": project_world["me"]["account"]["id"],
                    "scope_kind": "section",
                    "scope_id": shared["lst"]["section_id"],
                    "level": None,
                },
                headers=alex,
            ).status_code
            == 200
        )
        assert "MCP change" not in str(ok(await call(mcp, "slate_item", "list")))
        assert "not found" in err(
            await call(mcp, "slate_item", "update", id=shared["item"]["id"], title="After revoke")
        )


def test_replay_live_and_notifications_after_share_removed(project_world, client, alex, db):
    from slate.api.events import Filters, _gate_check, _read_page

    def read_after(since, gate):
        return _read_page(since, Filters(), gate)

    _visible = _gate_check
    from slate.service.access import EventGate

    shared, hidden, _ = project_world["rows"]
    me = project_world["me"]
    gate = EventGate(me["account"]["id"], me["token_id"])
    gate.load(db)
    replay = read_after("", gate=gate)
    assert not any("Secret" in str(ev) for ev in replay)
    # Actor ownership must not preserve access to scoped content after revocation.
    assert (
        client.patch(
            f"{API}/items/{shared['item']['id']}", json={"title": "Guest authored"}, headers=BROWSER
        ).status_code
        == 200
    )
    own = db.scalar(
        select(Event)
        .where(Event.item_id == shared["item"]["id"], Event.actor_id == me["account"]["id"])
        .order_by(Event.id.desc())
    )
    assert _visible(gate, event_payload(own))
    assert (
        client.put(
            API + "/shares",
            json={
                "account_id": me["account"]["id"],
                "scope_kind": "section",
                "scope_id": shared["lst"]["section_id"],
                "level": None,
            },
            headers=alex,
        ).status_code
        == 200
    )
    assert not _visible(gate, event_payload(own))
    assert not any(ev.get("item_id") == shared["item"]["id"] for ev in read_after("", gate=gate))
    for path in ["/inbox", "/export", "/docs", "/events", "/items"]:
        result = client.get(API + path)
        assert "Guest authored" not in result.text and "Shared item" not in result.text
    assert client.get(f"{API}/attachments/{shared['attachment']['id']}").status_code == 404
    assert client.get(f"{API}/doc-files/{shared['file']['id']}").status_code == 404


def test_throttled_new_addresses_do_not_grow_limiter_without_bound():
    from fastapi import HTTPException

    from slate.api.collaboration import Attempts

    limiter = Attempts()
    for address in range(1200):
        try:
            limiter.take(str(address))
        except HTTPException as exc:
            assert exc.status_code == 429
    assert len(limiter.clients) <= 1024


def test_acceptance_limiter_is_bounded(client, monkeypatch):
    from slate.api import collaboration

    monkeypatch.setattr(collaboration, "attempts", collaboration.Attempts())
    for _ in range(10):
        assert (
            client.post(
                API + "/invitations/accept", json={"secret": "bad"}, headers=BROWSER
            ).status_code
            == 410
        )
    assert (
        client.post(
            API + "/invitations/accept", json={"secret": "bad"}, headers=BROWSER
        ).status_code
        == 429
    )


def test_mentions_do_not_cross_project_boundaries(project_world, client, alex):
    shared, hidden, _ = project_world["rows"]
    for row in (shared, hidden):
        title = row["item"]["title"]
        response = client.post(
            f"{API}/items/{row['item']['id']}/comments",
            json={"body_md": f"@guest {title} notification"},
            headers=alex,
        )
        assert response.status_code == 201, response.text
    inbox = client.get(API + "/inbox")
    assert inbox.status_code == 200
    assert "Shared item notification" in inbox.text
    assert "Secret" not in inbox.text
    assert (
        client.put(
            API + "/shares",
            headers=alex,
            json={
                "account_id": project_world["me"]["account"]["id"],
                "scope_kind": "section",
                "scope_id": shared["lst"]["section_id"],
                "level": None,
            },
        ).status_code
        == 200
    )
    assert "Shared item notification" not in client.get(API + "/inbox").text


def test_guest_admin_scope_does_not_manage_invitations(project_world, client, alex):
    me = project_world["me"]
    token = client.post(
        f"{API}/accounts/{me['account']['id']}/tokens", json={"scopes": ["admin"]}, headers=alex
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    sid = project_world["rows"][0]["lst"]["section_id"]
    assert client.get(f"{API}/invitations?section_id={sid}", headers=headers).status_code == 403
    assert (
        client.post(
            API + "/invitations",
            headers=headers,
            json={
                "section_id": sid,
                "handle": "intruder",
                "display_name": "Intruder",
            },
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"{API}/invitations/{project_world['invitation']['id']}", headers=headers
        ).status_code
        == 403
    )


def test_list_only_token_cannot_read_section_whiteboard(project_world, client, alex):
    row = project_world["rows"][0]
    sid = row["lst"]["section_id"]
    assert (
        client.patch(
            f"{API}/sections/{sid}",
            headers=alex,
            json={"whiteboard_url": "https://example.com/section-secret"},
        ).status_code
        == 200
    )
    me = client.get(API + "/me", headers=alex).json()
    token = client.post(
        f"{API}/accounts/{me['account']['id']}/tokens",
        headers=alex,
        json={"scopes": ["write"], "limit": {"list_ids": [row["lst"]["id"]]}},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{API}/sections/{sid}/project", headers=headers).status_code == 404
    for path in ["/sections", "/export", "/events?since=0&limit=1000"]:
        response = client.get(API + path, headers=headers)
        assert response.status_code == 200
        assert "section-secret" not in response.text


def test_files_are_not_cached_after_revocation(project_world, client):
    row = project_world["rows"][0]
    for path in [f"/doc-files/{row['file']['id']}", f"/attachments/{row['attachment']['id']}"]:
        assert client.get(API + path).headers["cache-control"] == "no-store"
