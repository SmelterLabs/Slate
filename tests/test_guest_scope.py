"""Guest scope (docs/collaboration-security.md): a project guest (cookie session limited to one
section) or a list-limited token must not reach past its section through a shared agent's chat
history, workspace tags, NULL-scoped rows, or other accounts' watches and reminders."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import API
from slate.app import create_app
from slate.models import Account, DocTag, Message, Share, Token
from slate.service import access

BROWSER = {"X-Slate-Client": "browser"}


@pytest.fixture(autouse=True)
def fresh_attempts(monkeypatch):
    """Every test accepts an invitation; the acceptance limiter must not count across them."""
    from slate.api import collaboration

    monkeypatch.setattr(collaboration, "attempts", collaboration.Attempts())


@pytest.fixture
def world(client, alex):
    """Alex's private Example Project (list Tasks) and a private Secret list, an agent `bot`
    shared into the project (unlimited and list-limited tokens), `guest` on a cookie session
    scoped to the project, and a workspace tag on a private item."""
    project = client.post(
        f"{API}/sections", json={"name": "Example Project", "audience": "work"}, headers=alex
    ).json()
    tasks = client.post(
        f"{API}/lists", json={"section_id": project["id"], "name": "Tasks"}, headers=alex
    ).json()
    priv = client.post(
        f"{API}/sections", json={"name": "Private", "audience": "personal"}, headers=alex
    ).json()
    secret = client.post(
        f"{API}/lists", json={"section_id": priv["id"], "name": "Secret"}, headers=alex
    ).json()
    for sid in (project["id"], priv["id"]):
        assert (
            client.patch(f"{API}/sections/{sid}", json={"private": True}, headers=alex).status_code
            == 200
        )
    tag = client.post(f"{API}/tags", json={"name": "clienttag"}, headers=alex).json()
    secret_item = client.post(
        f"{API}/items",
        json={"list_id": secret["id"], "title": "Secret task", "tags": ["clienttag"]},
        headers=alex,
    ).json()
    task = client.post(
        f"{API}/items", json={"list_id": tasks["id"], "title": "Update the schedule"}, headers=alex
    ).json()

    bot = client.post(
        f"{API}/accounts",
        json={"handle": "bot", "display_name": "Bot", "kind": "agent", "role": "guest"},
        headers=alex,
    ).json()
    bot_tok = client.post(
        f"{API}/accounts/{bot['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    bot_ltd = client.post(
        f"{API}/accounts/{bot['id']}/tokens",
        json={"scopes": ["write"], "limit": {"list_ids": [tasks["id"]]}},
        headers=alex,
    ).json()

    agent = client.post(
        f"{API}/accounts",
        json={"handle": "agent", "display_name": "Agent", "kind": "agent"},
        headers=alex,
    ).json()
    ltd = client.post(
        f"{API}/accounts/{agent['id']}/tokens",
        json={"scopes": ["write"], "limit": {"section_ids": [project["id"]]}},
        headers=alex,
    ).json()
    _share(client, alex, agent["id"], "section", project["id"])

    inv = client.post(
        f"{API}/invitations",
        json={"section_id": project["id"], "handle": "guest", "display_name": "Guest"},
        headers=alex,
    ).json()
    guest = TestClient(create_app())  # its own cookie jar
    r = guest.post(f"{API}/invitations/accept", json={"secret": inv["secret"]}, headers=BROWSER)
    assert r.status_code == 200, r.text
    return {
        "project": project,
        "tasks": tasks,
        "priv": priv,
        "secret": secret,
        "tag": tag,
        "secret_item": secret_item,
        "task": task,
        "bot": bot,
        "bot_h": {"Authorization": f"Bearer {bot_tok['token']}"},
        "bot_ltd_h": {"Authorization": f"Bearer {bot_ltd['token']}"},
        # Another agent's token limited to the project (like the cookie session): restricted, and
        # not the chat's own agent.
        "ltd_h": {"Authorization": f"Bearer {ltd['token']}"},
        "guest_id": r.json()["account_id"],
        "guest": guest,
    }


def _share(client, alex, account_id, kind, scope_id, level="edit"):
    r = client.put(
        f"{API}/shares",
        json={"account_id": account_id, "scope_kind": kind, "scope_id": scope_id, "level": level},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _post(client, h, key, body):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _bodies(client, h, key, **params):
    r = client.get(f"{API}/channels/{key}/messages", params=params, headers=h)
    assert r.status_code == 200, r.text
    return [m["body_md"] for m in r.json()]


# --- 1. A shared agent's chat starts at the share ------------------------------------------


def test_a_shared_agents_chat_shows_guests_only_what_came_after_the_share(client, alex, world):
    bot, key, g = world["bot"], f"agent:{world['bot']['id']}", world["guest"]
    _post(client, alex, key, "PRIVATE: tax numbers and the Secret list plan")
    _post(client, world["bot_h"], key, "bot reply mentioning Secret task")
    share = _share(client, alex, bot["id"], "section", world["project"]["id"])
    _post(client, alex, key, "Project specs are in")
    _post(client, world["bot_h"], key, "On it")

    # The guest and the list-limited token read from the share on; Alex reads it all.
    assert _bodies(g, {}, key) == ["Project specs are in", "On it"]
    assert _bodies(g, {}, key, after="0") == ["Project specs are in", "On it"]
    assert _bodies(client, world["ltd_h"], key) == ["Project specs are in", "On it"]
    # The agent itself, even on a list-limited token, keeps its whole conversation.
    assert len(_bodies(client, world["bot_ltd_h"], key)) == 4
    assert len(_bodies(client, alex, key)) == 4
    # Events about the earlier messages are out of reach too (the stream's replay path).
    r = g.get(f"{API}/events", params={"type": "message.posted", "limit": 50})
    heard = [e["after"]["body_md"] for e in r.json()["events"] if e["after"].get("channel") == key]
    assert heard == ["Project specs are in", "On it"]
    unread = g.get(f"{API}/channels/{key}").json()["unread"]
    assert unread == 2

    # Re-granting later moves the line: only what comes after the new share is visible.
    assert client.delete(f"{API}/shares/{share['id']}", headers=alex).status_code == 204
    _share(client, alex, bot["id"], "section", world["project"]["id"])
    _post(client, alex, key, "after the re-grant")
    assert _bodies(g, {}, key) == ["after the re-grant"]


def test_project_members_read_a_shared_agents_whole_chat(client, alex, world):
    """A project agent's chat is its project's: a member on the project reads all of it (no
    share date), a member outside the private project none of it."""
    key = f"agent:{world['bot']['id']}"
    _post(client, alex, key, "before")
    _share(client, alex, world["bot"]["id"], "section", world["project"]["id"])
    _post(client, alex, key, "after")
    heads = []
    for handle in ("member", "outsider"):
        member = client.post(
            f"{API}/accounts",
            json={"handle": handle, "display_name": handle.title(), "kind": "person"},
            headers=alex,
        ).json()
        tok = client.post(
            f"{API}/accounts/{member['id']}/tokens", json={"scopes": ["write"]}, headers=alex
        ).json()
        heads.append((member, {"Authorization": f"Bearer {tok['token']}"}))
    (member, mh), (_, oh) = heads
    _share(client, alex, member["id"], "section", world["project"]["id"])
    assert _bodies(client, mh, key) == ["before", "after"]
    r = client.get(f"{API}/channels/{key}/messages", headers=oh)
    assert r.status_code in (403, 404), r.text


def test_the_live_gate_drops_agent_chat_from_before_the_share(client, alex, world, db):
    from slate.service.access import EventGate

    key = f"agent:{world['bot']['id']}"
    _share(client, alex, world["bot"]["id"], "section", world["project"]["id"])
    since = db.scalar(select(Share.created_at).where(Share.account_id == world["bot"]["id"]))
    me = world["guest"].get(f"{API}/me").json()
    gate = EventGate(world["guest_id"], me["token_id"])
    gate.load(db)

    def heard(at):
        payload = {
            "type": "message.posted",
            "subject_id": "m1",
            "after": {"channel": key},
            "at": at.isoformat().replace("+00:00", "Z"),
        }
        return gate.allows(db, payload)

    assert heard(since + timedelta(seconds=1))
    assert not heard(since - timedelta(seconds=1))


# --- 2. Workspace tags -------------------------------------------------------------------------


def test_a_guest_cannot_delete_rename_or_merge_tags(client, alex, world):
    g, tag = world["guest"], world["tag"]
    other = client.post(f"{API}/tags", json={"name": "other"}, headers=alex).json()
    assert g.delete(f"{API}/tags/{tag['id']}", headers=BROWSER).status_code == 403
    r = g.patch(f"{API}/tags/{tag['id']}", json={"name": "renamed"}, headers=BROWSER)
    assert r.status_code == 403, r.text
    r = g.post(f"{API}/tags/{tag['id']}/merge", json={"into": other["id"]}, headers=BROWSER)
    assert r.status_code == 403, r.text
    ltd = world["bot_ltd_h"]
    assert client.delete(f"{API}/tags/{tag['id']}", headers=ltd).status_code == 403
    # The owner's private item keeps its tag; the owner still manages tags.
    item = client.get(f"{API}/items/{world['secret_item']['id']}", headers=alex).json()
    assert item["tags"] == ["clienttag"]
    assert client.delete(f"{API}/tags/{other['id']}", headers=alex).status_code == 204


def test_a_guest_still_creates_and_applies_tags(client, alex, world):
    g = world["guest"]
    r = g.post(f"{API}/tags", json={"name": "rush"}, headers=BROWSER)
    assert r.status_code == 201, r.text
    r = g.patch(f"{API}/items/{world['task']['id']}", json={"tags": ["rush"]}, headers=BROWSER)
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["rush"]


def test_restricted_callers_list_only_tags_in_reach_or_their_own(client, alex, world):
    g = world["guest"]
    client.post(f"{API}/tags", json={"name": "unused"}, headers=alex)
    client.post(f"{API}/tags", json={"name": "ontask"}, headers=alex)
    client.patch(f"{API}/items/{world['task']['id']}", json={"tags": ["ontask"]}, headers=alex)
    g.post(f"{API}/tags", json={"name": "mine"}, headers=BROWSER)
    names = {t["name"]: t["item_count"] for t in g.get(f"{API}/tags").json()}
    assert names == {"ontask": 1, "mine": 0}
    ltd = [t["name"] for t in client.get(f"{API}/tags", headers=world["ltd_h"]).json()]
    assert ltd == ["ontask"]
    everything = {t["name"] for t in client.get(f"{API}/tags", headers=alex).json()}
    assert everything == {"clienttag", "unused", "ontask", "mine"}


# --- 3. NULL-scoped rows ------------------------------------------------------------------------


def test_workspace_wide_rows_are_hidden_from_restricted_callers(client, alex, world):
    g = world["guest"]
    r = client.post(
        f"{API}/automations",
        json={
            "name": "ws rule",
            "trigger": {"type": "status_changed"},
            "actions": [{"type": "webhook", "url": "https://hooks.example.com/secret-path"}],
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    ws_rule = r.json()["id"]
    r = client.post(
        f"{API}/automations",
        json={
            "name": "project rule",
            "section_id": world["project"]["id"],
            "trigger": {"type": "status_changed"},
            "actions": [{"type": "add_tag", "tag": "done"}],
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    project_rule = r.json()["id"]
    for h in ({}, world["ltd_h"]):
        rules = {
            a["id"] for a in (g if not h else client).get(f"{API}/automations", headers=h).json()
        }
        assert rules == {project_rule}
        c = g if not h else client
        assert c.get(f"{API}/automations/{ws_rule}", headers=h).status_code == 404
    # Members (General) keep workspace-wide rows.
    assert {a["id"] for a in client.get(f"{API}/automations", headers=alex).json()} == {
        ws_rule,
        project_rule,
    }


def test_saved_views_follow_their_scope(client, alex, world):
    g = world["guest"]

    def view(name, kind, scope_id=None, h=alex, c=client):
        r = c.post(
            f"{API}/views",
            json={"name": name, "scope_kind": kind, "scope_id": scope_id, "config": {}},
            headers=h,
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    secret_view = view("Secret things", "list", world["secret"]["id"])
    priv_view = view("Private section", "section", world["priv"]["id"])
    ws_view = view("Everything", "everything")
    tasks_view = view("Tasks board", "list", world["tasks"]["id"])
    project_view = view("Project", "section", world["project"]["id"])
    own = view("My lens", "everything", h=BROWSER, c=g)

    # Slate's own landing views (This week, Agenda) are generic lenses: everyone's.
    from slate.service.views import SEED_VIEWS

    seen = {v["id"] for v in g.get(f"{API}/views").json()}
    assert seen == {tasks_view, project_view, own, *(s["id"] for s in SEED_VIEWS)}
    assert g.get(f"{API}/views/{secret_view}").status_code == 404
    assert g.get(f"{API}/views/{priv_view}").status_code == 404
    assert g.get(f"{API}/views/{ws_view}").status_code == 404
    assert len(client.get(f"{API}/views", headers=alex).json()) == 6 + len(SEED_VIEWS)


def test_doc_tags_follow_the_doc(client, alex, world, db):
    g = world["guest"]
    r = client.post(
        f"{API}/docs",
        json={
            "title": "Private page",
            "body_md": "x",
            "list_id": world["secret"]["id"],
            "tags": ["docword"],
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    assert "docword" not in [t["name"] for t in g.get(f"{API}/tags").json()]
    # Through the service: the read filter hides the row itself.
    tag_id = next(
        t["id"] for t in client.get(f"{API}/tags", headers=alex).json() if t["name"] == "docword"
    )
    guest = db.get(Account, world["guest_id"])
    found = access.compute(db, guest, db.get(Token, g.get(f"{API}/me").json()["token_id"]))
    access.attach(db, found)
    assert db.scalars(select(DocTag).where(DocTag.tag_id == tag_id)).all() == []
    assert (
        db.scalars(
            select(DocTag).where(DocTag.tag_id == tag_id).execution_options(slate_unfiltered=True)
        ).all()
        != []
    )


# --- 4. Watchers and reminders are self-only below edit -------------------------------------


def _viewer(client, alex, world, handle="guest2"):
    acct = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    _share(client, alex, acct["id"], "section", world["project"]["id"], level="view")
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def test_below_edit_a_caller_only_watches_and_unwatches_itself(client, alex, world):
    _, vh = _viewer(client, alex, world)
    task = world["task"]["id"]
    assert client.post(f"{API}/items/{task}/watch", headers=vh).status_code == 200
    r = client.post(f"{API}/items/{task}/unwatch", json={"account": "alex"}, headers=vh)
    assert r.status_code == 403, r.text
    assert "alex" in [
        w["handle"] for w in client.get(f"{API}/items/{task}/watchers", headers=alex).json()
    ]
    r = client.post(f"{API}/items/{task}/watch", json={"account": "guest"}, headers=vh)
    assert r.status_code == 403, r.text
    r = client.put(f"{API}/items/{task}/watchers", json={"watchers": ["guest2"]}, headers=vh)
    assert r.status_code == 403, r.text
    assert client.post(f"{API}/items/{task}/unwatch", headers=vh).status_code == 200
    # At edit (the guest's cookie session) anyone can add or remove watchers.
    g = world["guest"]
    r = g.post(f"{API}/items/{task}/unwatch", json={"account": "alex"}, headers=BROWSER)
    assert r.status_code == 200, r.text
    assert "alex" not in [w["handle"] for w in r.json()]


def test_being_unwatched_by_someone_else_is_inbox_news(client, alex, world):
    g, task = world["guest"], world["task"]["id"]
    g.post(f"{API}/items/{task}/unwatch", json={"account": "alex"}, headers=BROWSER)
    inbox = client.get(f"{API}/inbox", headers=alex).json()
    entries = inbox["entries"] if isinstance(inbox, dict) else inbox
    mine = [e for e in entries if e["event"]["type"] == "item.unwatched"]
    assert [e["reason"] for e in mine] == ["watching"]
    # Unwatching yourself is not news.
    client.post(f"{API}/items/{task}/watch", headers=alex)
    client.post(f"{API}/items/{task}/unwatch", headers=alex)
    inbox = client.get(f"{API}/inbox", headers=alex).json()
    entries = inbox["entries"] if isinstance(inbox, dict) else inbox
    assert len([e for e in entries if e["event"]["type"] == "item.unwatched"]) == 1


def test_reminders_for_someone_else_need_edit(client, alex, world):
    _, vh = _viewer(client, alex, world)
    task = world["task"]["id"]
    r = client.post(
        f"{API}/items/{task}/reminders",
        json={"account": "alex", "at": "2030-01-01T00:00:00Z"},
        headers=vh,
    )
    assert r.status_code == 403, r.text
    r = client.post(
        f"{API}/items/{task}/reminders", json={"at": "2030-01-01T00:00:00Z"}, headers=vh
    )
    assert r.status_code == 201, r.text
    g = world["guest"]
    r = g.post(
        f"{API}/items/{task}/reminders",
        json={"account": "alex", "at": "2030-01-01T00:00:00Z"},
        headers=BROWSER,
    )
    assert r.status_code == 201, r.text


# --- 5. Stats and handle enumeration -----------------------------------------------------------


def test_stats_are_for_owners_and_admins(client, alex, world):
    assert world["guest"].get(f"{API}/stats").status_code == 403
    assert client.get(f"{API}/stats", headers=world["bot_ltd_h"]).status_code == 403
    assert client.get(f"{API}/stats", headers=world["bot_h"]).status_code == 403
    assert client.get(f"{API}/stats", headers=alex).status_code == 200


def test_restricted_callers_cannot_enumerate_handles(client, alex, world):
    g, task = world["guest"], world["task"]["id"]
    client.post(
        f"{API}/accounts",
        json={"handle": "stranger", "display_name": "Stranger", "kind": "person"},
        headers=alex,
    )
    unknown = g.post(f"{API}/items/{task}/watch", json={"account": "nosuchhandle"}, headers=BROWSER)
    outside = g.post(f"{API}/items/{task}/watch", json={"account": "stranger"}, headers=BROWSER)
    assert unknown.status_code == outside.status_code == 422
    assert unknown.json()["detail"] == "no account 'nosuchhandle'"
    assert outside.json()["detail"] == "no account 'stranger'"
    # Participants of the project resolve as before.
    r = g.post(f"{API}/items/{task}/watch", json={"account": "alex"}, headers=BROWSER)
    assert r.status_code == 200, r.text
    assert (
        client.post(
            f"{API}/items/{task}/watch", json={"account": "stranger"}, headers=alex
        ).status_code
        == 200
    )


def test_a_guest_attaches_files_inside_the_shared_project(client, alex, world):
    """An upload writes two rows; the blob has no item_id of its own, so the scope gate reaches
    its task through the attachment instead of refusing it at the guest catch-all."""
    import base64

    task = world["task"]["id"]
    for who, headers in ((world["guest"], BROWSER), (client, world["ltd_h"])):
        r = who.post(
            f"{API}/items/{task}/attachments/base64",
            json={"filename": "notes.md", "content_base64": base64.b64encode(b"# ok").decode()},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        att = r.json()
        got = who.get(f"{API}/attachments/{att['id']}", headers=headers)
        assert got.status_code == 200 and got.content == b"# ok"
        assert who.delete(f"{API}/attachments/{att['id']}", headers=headers).status_code == 204
    # Outside the share it is still a wall.
    secret = world["secret_item"]["id"]
    r = world["guest"].post(
        f"{API}/items/{secret}/attachments/base64",
        json={"filename": "x.md", "content_base64": base64.b64encode(b"x").decode()},
        headers=BROWSER,
    )
    assert r.status_code in (403, 404), r.text


def test_message_rows_before_the_share_stay_in_the_table(db, client, alex, world):
    """The filter hides, it never deletes: Alex's history with the agent is intact."""
    key = f"agent:{world['bot']['id']}"
    _post(client, alex, key, "before")
    _share(client, alex, world["bot"]["id"], "section", world["project"]["id"])
    assert _bodies(world["guest"], {}, key) == []
    assert db.scalars(select(Message.body_md).where(Message.channel == key)).all() == ["before"]
