from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from conftest import API, workspace_events
from slate.db import SessionLocal
from slate.models import utcnow
from slate.service import due, lists, recurrences, reminders
from slate.service.core import Actor, Source
from slate.service.errors import Invalid
from test_mcp import call, http_mcp, ok


def configure(client, alex, todo, **fields):
    response = client.patch(f"{API}/lists/{todo['id']}", headers=alex, json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def file(client, headers, **fields):
    response = client.post(
        f"{API}/items", headers=headers, json={"title": "Check the draft", **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_list_fields_and_events(client, alex, todo, make_account):
    agent, _ = make_account("my-agent")
    data = {
        "note_md": "**Drafts** belong here.",
        "default_assignee_id": agent["id"],
        "default_tags": ["draft"],
        "reference": True,
    }
    result = configure(client, alex, todo, **data)
    assert {key: result[key] for key in data} == data
    changes = workspace_events(client, alex, "list.updated")
    assert {e["field"]: e["after"] for e in changes if e["field"] in data} == data
    assert client.get(f"{API}/lists/{todo['id']}", headers=alex).json() == result
    blank = configure(
        client,
        alex,
        todo,
        note_md=None,
        default_assignee_id=None,
        default_tags=None,
        reference=False,
    )
    assert blank["note_md"] is None and blank["default_assignee_id"] is None
    assert blank["default_tags"] == [] and blank["reference"] is False
    created = client.post(
        f"{API}/lists",
        headers=alex,
        json={
            "section_id": todo["section_id"],
            "name": "Drafts",
            **data,
        },
    )
    assert created.status_code == 201, created.text
    created_id = created.json()["id"]
    assert {key: created.json()[key] for key in data} == data
    event = next(
        e for e in workspace_events(client, alex, "list.created") if e["subject_id"] == created_id
    )
    assert {key: event["after"][key] for key in data} == data
    assert not any(
        e["subject_id"] == created_id and e["field"] in data
        for e in workspace_events(client, alex, "list.updated")
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"note_md": "x" * 2001},
        {"default_assignee_id": "missing"},
        {"default_tags": ["a", "b", "c", "d", "e", "f"]},
        {"default_tags": [" "]},
        {"reference": None},
    ],
)
def test_invalid_list_fields_are_refused(client, alex, todo, fields):
    response = client.patch(f"{API}/lists/{todo['id']}", headers=alex, json=fields)
    assert response.status_code == 422, response.text


@pytest.mark.anyio
async def test_core_mcp_list_filing_fields_and_inbox(client, alex, todo, make_account):
    agent, headers = make_account("my-agent")
    settings = {
        "note_md": "Proofread the draft.",
        "default_assignee_id": agent["id"],
        "default_tags": ["draft"],
        "reference": True,
    }
    async with http_mcp(alex, tools=None) as mcp:
        created = ok(
            await call(
                mcp,
                "slate_list",
                "create",
                section_id=todo["section_id"],
                name="Drafts",
                **settings,
            )
        )
        assert {key: created[key] for key in settings} == settings
        list_id = created["id"]
        updated = ok(await call(mcp, "slate_list", "update", id=list_id, reference=False))
        assert updated["reference"] is False
        rows = ok(await call(mcp, "slate_list", "list"))
        assert next(row for row in rows if row["id"] == list_id) == updated
    client.put(
        f"{API}/settings/inbox", headers=alex, json={"inbox_list_id": list_id}
    ).raise_for_status()
    async with http_mcp(headers, tools="core,slate_checkin") as mcp:
        checked = ok(await call(mcp, "slate_checkin", "get"))
        expected = client.get(f"{API}/checkin", headers=headers).json()["lists"]
        # MCP omits nulls from its JSON responses, including an unset filing note.
        assert checked["lists"] == [
            {key: value for key, value in row.items() if value is not None} for row in expected
        ]
        row = next(row for row in checked["lists"] if row["id"] == list_id)
        assert (row["note_md"], row["kind"], row["reference"]) == (
            settings["note_md"],
            "tasks",
            False,
        )
        card = ok(await call(mcp, "slate_item", "create", title="MCP inbox draft"))
        assert card["list_id"] == list_id
        assert card["tags"] == ["draft"]
        assert [a["handle"] for a in card["assignees"]] == ["my-agent"]
    async with http_mcp(alex, tools=None) as mcp:
        cleared = ok(
            await call(mcp, "slate_list", "update", id=list_id, note_md=None, default_tags=None)
        )
        assert cleared["default_tags"] == [] and "note_md" not in cleared


@pytest.mark.anyio
async def test_rest_quick_add_split_and_mcp_use_defaults(client, alex, todo, make_account):
    agent, _ = make_account("my-agent")
    configure(client, alex, todo, default_assignee_id=agent["id"], default_tags=[" Draft "])
    card = file(client, alex, list_id=todo["id"], tags=["draft", "proofed"])
    assert card["tags"] == ["draft", "proofed"]
    assert [a["id"] for a in card["assignees"]] == [agent["id"]]
    quick = file(client, alex, list_id=todo["id"], assignees=["alex"])
    assert [a["handle"] for a in quick["assignees"]] == ["alex"]
    assert quick["tags"] == ["draft"]
    child = file(client, alex, parent_id=card["id"])
    assert child["assignees"] == card["assignees"]
    response = client.post(
        f"{API}/items/{card['id']}/split",
        headers=alex,
        json={"children": [{"title": "Split draft"}]},
    )
    assert response.status_code == 201, response.text
    assert response.json()[0]["assignees"] == card["assignees"]
    async with http_mcp(alex) as mcp:
        result = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="MCP draft"))
    assert result["assignees"] == card["assignees"] and result["tags"] == ["draft"]


def test_ai_and_recurrence_creation_use_defaults(client, alex, todo, make_account, db, monkeypatch):
    from slate.service import aiadd

    agent, _ = make_account("my-agent")
    configure(client, alex, todo, default_assignee_id=agent["id"], default_tags=["draft"])
    monkeypatch.setattr(aiadd, "enabled", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        aiadd, "call_model", lambda s, t: {"title": "Check the draft", "body_md": ""}
    )
    added = client.post(
        f"{API}/items/ai", headers=alex, json={"list_id": todo["id"], "text": "Check the draft."}
    )
    assert added.status_code == 201, added.text
    assert added.json()["assignees"][0]["id"] == agent["id"]
    assert added.json()["tags"] == ["draft"]

    now = datetime(2026, 11, 21, 12, tzinfo=UTC)
    for title, assignees in (("Inherited", []), ("Explicit", ["alex"])):
        response = client.post(
            f"{API}/recurrences",
            headers=alex,
            json={
                "list_id": todo["id"],
                "title_template": title,
                "rrule": "FREQ=DAILY",
                "dtstart": now.isoformat(),
                "assignees": assignees,
                "tags": ["daily"],
            },
        )
        assert response.status_code == 201, response.text
    # Advance past the next 12:00 UTC occurrence so lead_days=0 materializes at any hour of the day.
    recurrences.materialize(db, utcnow() + timedelta(days=1))
    rows = client.get(f"{API}/items", headers=alex).json()
    for prefix, handle in (("Inherited", "my-agent"), ("Explicit", "alex")):
        matching = [row for row in rows if row["title"].startswith(prefix)]
        assert matching
        assert all(row["tags"] == ["daily", "draft"] for row in matching)
        assert all([a["handle"] for a in row["assignees"]] == [handle] for row in matching)


@pytest.mark.anyio
async def test_inbox_setting_and_list_note(client, alex, todo, make_account):
    _, agent = make_account("my-agent")
    assert client.post(f"{API}/items", headers=agent, json={"title": "Unrouted"}).status_code == 422
    assert (
        client.put(
            f"{API}/settings/inbox", headers=agent, json={"inbox_list_id": todo["id"]}
        ).status_code
        == 403
    )
    saved = client.put(f"{API}/settings/inbox", headers=alex, json={"inbox_list_id": todo["id"]})
    assert saved.status_code == 200 and saved.json() == {"inbox_list_id": todo["id"]}
    assert client.get(f"{API}/settings/inbox", headers=alex).json() == saved.json()
    card = file(client, agent, assignees=["my-agent"])
    assert card["list_id"] == todo["id"]
    configure(client, alex, todo, note_md="Proofread the draft.")
    brief = client.get(f"{API}/items/{card['id']}/brief", headers=agent).json()["text"]
    note = "This list:\nProofread the draft."
    assert brief.index(note) < brief.index("How to work this task:")
    taken = client.post(f"{API}/work/next", headers=agent, json={"item_id": card["id"]})
    assert taken.status_code == 200, taken.text
    assert taken.json()["brief"] == brief
    second = file(client, agent, assignees=["my-agent"])
    async with http_mcp(agent) as mcp:
        result = ok(await call(mcp, "slate_work", "next", item_id=second["id"]))
    assert note in result["brief"]
    checkin = client.get(f"{API}/checkin", headers=agent).json()
    assert (
        next(row for row in checkin["lists"] if row["id"] == todo["id"])["note_md"]
        == "Proofread the draft."
    )
    client.put(
        f"{API}/settings/inbox", headers=alex, json={"inbox_list_id": None}
    ).raise_for_status()
    assert client.post(f"{API}/items", headers=agent, json={"title": "Unrouted"}).status_code == 422


def test_reference_cards_are_visible_but_not_work(client, alex, todo, make_account, db):
    account, agent = make_account("my-agent")
    card = file(
        client,
        alex,
        list_id=todo["id"],
        assignees=["alex", "my-agent"],
        due_at=(utcnow() - timedelta(days=2)).isoformat(),
    )
    # Existing due entries are hidden when the list becomes reference material.
    due.emit_due_events(db, now=utcnow())
    assert any(e["reason"] == "due" for e in client.get(f"{API}/inbox", headers=alex).json())
    roster = client.get(f"{API}/agents", headers=alex).json()
    assert next(a for a in roster if a["account"]["id"] == account["id"])["open_assigned"] == 1
    configure(client, alex, todo, reference=True)
    assert client.get(f"{API}/items/{card['id']}", headers=alex).status_code == 200
    assert [
        row["id"]
        for row in client.get(f"{API}/items", headers=alex, params={"q": "Check the draft"}).json()
    ] == [card["id"]]
    assert client.get(f"{API}/stats", headers=alex).json()["open_items"] == 0
    roster = client.get(f"{API}/agents", headers=alex).json()
    assert next(a for a in roster if a["account"]["id"] == account["id"])["open_assigned"] == 0
    day = client.get(f"{API}/today", headers=alex).json()
    assert not any(day[key] for key in ("overdue", "today", "waiting", "asked"))
    assert client.get(f"{API}/me/digest", headers=alex).json()["counts"]["overdue"] == 0
    assert client.get(f"{API}/checkin", headers=agent).json()["queue_total"] == 0
    assert (
        client.post(f"{API}/work/next", headers=agent, json={"item_id": card["id"]}).json()["item"]
        is None
    )
    assert all(e["reason"] != "due" for e in client.get(f"{API}/inbox", headers=alex).json())
    assert client.post(f"{API}/items/{card['id']}/run", headers=alex).status_code == 422
    file(client, alex, list_id=todo["id"], due_at=(utcnow() - timedelta(days=1)).isoformat())
    assert due.emit_due_events(db, now=utcnow()) == 0


def test_reference_cards_only_fire_reminders_set_for_a_fixed_time(client, alex, todo, db):
    configure(client, alex, todo, reference=True)
    now = utcnow()
    card = file(client, alex, list_id=todo["id"], due_at=(now - timedelta(days=1)).isoformat())
    route = f"{API}/items/{card['id']}/reminders"
    client.post(route, headers=alex, json={"before_minutes": 0}).raise_for_status()
    client.post(
        route, headers=alex, json={"at": (now + timedelta(hours=1)).isoformat()}
    ).raise_for_status()
    assert reminders.fire_reminders(db, now=now + timedelta(hours=2)) == 1


@pytest.mark.parametrize("reference", [False, True])
def test_pending_due_fallback_only_releases_for_work_lists(client, alex, todo, db, reference):
    from slate.service import attention

    route = f"{API}/inbox/alerts"
    attention.release(db, utcnow())
    cursor = client.get(route, headers=alex).headers["X-Slate-Cursor"]
    card = file(client, alex, list_id=todo["id"], due_at=(utcnow() - timedelta(days=2)).isoformat())
    assert card["assignees"] == []
    assert due.emit_due_events(db, now=utcnow()) > 0
    entries = client.get(f"{API}/inbox", headers=alex).json()
    assert any(e["reason"] == "due" and e["event"]["item_id"] == card["id"] for e in entries)
    configure(client, alex, todo, reference=reference)
    entries = client.get(f"{API}/inbox", headers=alex).json()
    assert any(e["reason"] == "due" for e in entries) is not reference
    attention.release(db, utcnow())
    alerts = client.get(route, headers=alex, params={"after": cursor}).json()
    assert any(alert["reason"] == "due" for alert in alerts) is not reference


def test_guest_filing_and_split_apply_the_hidden_default(client, alex, todo, make_account):
    agent, _ = make_account("my-agent")
    guest = client.post(
        f"{API}/accounts",
        headers=alex,
        json={"kind": "person", "role": "guest", "handle": "guest", "display_name": "Guest"},
    ).json()
    token = client.post(
        f"{API}/accounts/{guest['id']}/tokens", headers=alex, json={"scopes": ["write"]}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        f"{API}/shares",
        headers=alex,
        json={
            "account_id": guest["id"],
            "scope_kind": "list",
            "scope_id": todo["id"],
            "level": "edit",
        },
    ).raise_for_status()
    configure(client, alex, todo, default_assignee_id=agent["id"])
    visible = client.get(f"{API}/lists", headers=headers).json()
    assert next(row for row in visible if row["id"] == todo["id"])["default_assignee_id"] is None
    assert (
        client.get(f"{API}/lists/{todo['id']}", headers=headers).json()["default_assignee_id"]
        is None
    )
    rejected = client.patch(
        f"{API}/lists/{todo['id']}", headers=headers, json={"default_assignee_id": agent["id"]}
    )
    assert rejected.status_code in (403, 422)
    assert agent["id"] not in rejected.text and agent["handle"] not in rejected.text

    card = file(client, headers, list_id=todo["id"], body_md="Review the draft.")
    split = client.post(
        f"{API}/items/{card['id']}/split", headers=headers, json={"children": [{"title": "Child"}]}
    )
    assert split.status_code == 201, split.text
    for item in (card, split.json()[0]):
        assert item["assignees"] == []
        full = client.get(f"{API}/items/{item['id']}", headers=alex).json()
        assert [a["id"] for a in full["assignees"]] == [agent["id"]]


@pytest.mark.parametrize("invalid", ["disabled", "system"])
def test_ineligible_account_cannot_be_a_default_owner(
    client, alex, todo, make_account, db, invalid
):
    if invalid == "system":
        ref = due.system_actor(db).account_id
        db.commit()
    else:
        acct, _ = make_account("my-agent")
        ref = acct["id"]
        configure(client, alex, todo, default_assignee_id=ref)
        client.patch(
            f"{API}/accounts/{ref}", headers=alex, json={"disabled": True}
        ).raise_for_status()
        assert file(client, alex, list_id=todo["id"])["assignees"] == []
    rejected = client.patch(
        f"{API}/lists/{todo['id']}", headers=alex, json={"default_assignee_id": ref}
    )
    assert rejected.status_code == 422
    assert ref not in rejected.text


def test_filing_fields_cannot_write_other_list_state(db, client, alex, todo):
    actor = Actor(client.get(f"{API}/me", headers=alex).json()["account"]["id"], Source.api)
    with pytest.raises(Invalid, match="filing"):
        lists.update_list(db, actor, todo["id"], filing={"name": "Changed through filing"})
    with pytest.raises(Invalid, match="filing"):
        lists.create_list(
            db, actor, section_id=todo["section_id"], name="New", filing={"private": True}
        )


@pytest.mark.parametrize("kind", ["archived list", "archived section", "drawer", "reference"])
def test_inbox_list_is_rechecked_at_use_and_set(client, alex, todo, kind):
    setting = {"inbox_list_id": todo["id"]}
    client.put(f"{API}/settings/inbox", headers=alex, json=setting).raise_for_status()
    if kind == "archived section":
        client.patch(
            f"{API}/sections/{todo['section_id']}", headers=alex, json={"archived": True}
        ).raise_for_status()
    else:
        changes = {
            "archived list": {"archived": True},
            "drawer": {"kind": "drawer"},
            "reference": {"reference": True},
        }
        configure(client, alex, todo, **changes[kind])
    rejected = client.post(f"{API}/items", headers=alex, json={"title": "Unrouted"})
    assert (
        rejected.status_code == 422
        and rejected.json()["detail"] == "list_id is required (or parent_id)"
    )
    assert client.put(f"{API}/settings/inbox", headers=alex, json=setting).status_code == 422


def test_inbox_list_is_unavailable_to_hidden_and_view_only_tokens(client, alex, todo, make_account):
    agent, _ = make_account("my-agent")
    other = client.post(
        f"{API}/lists", headers=alex, json={"section_id": todo["section_id"], "name": "Other"}
    ).json()
    configure(client, alex, todo, private=True)
    client.put(
        f"{API}/settings/inbox", headers=alex, json={"inbox_list_id": todo["id"]}
    ).raise_for_status()
    for allowed in ([other["id"]], [todo["id"]]):
        token = client.post(
            f"{API}/accounts/{agent['id']}/tokens",
            headers=alex,
            json={"scopes": ["write"], "limit": {"list_ids": allowed}},
        ).json()["token"]
        rejected = client.post(
            f"{API}/items", headers={"Authorization": f"Bearer {token}"}, json={"title": "Unrouted"}
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == "list_id is required (or parent_id)"
        client.put(
            f"{API}/shares",
            headers=alex,
            json={
                "account_id": agent["id"],
                "scope_kind": "list",
                "scope_id": todo["id"],
                "level": "view",
            },
        ).raise_for_status()


def test_checkin_identifies_work_reference_and_drawer_lists(client, alex, todo):
    reference = client.post(
        f"{API}/lists",
        headers=alex,
        json={"section_id": todo["section_id"], "name": "Reading", "reference": True},
    ).json()
    drawer = client.post(
        f"{API}/lists",
        headers=alex,
        json={"section_id": todo["section_id"], "name": "Files", "kind": "drawer"},
    ).json()
    rows = client.get(f"{API}/checkin", headers=alex).json()["lists"]
    assert {row["id"]: (row["kind"], row["reference"]) for row in rows} == {
        todo["id"]: ("tasks", False),
        reference["id"]: ("tasks", True),
        drawer["id"]: ("drawer", False),
    }


@pytest.mark.parametrize("which", ["due", "retry", "reminder"])
def test_scheduler_rechecks_reference_under_its_write_lock(
    client, alex, todo, make_account, db, monkeypatch, which
):
    now = utcnow()
    _, agent = make_account("my-agent")
    card = file(
        client,
        alex,
        list_id=todo["id"],
        assignees=["my-agent"],
        due_at=None if which == "retry" else (now - timedelta(days=1)).isoformat(),
    )
    if which == "retry":
        taken = client.post(f"{API}/work/next", headers=agent, json={"item_id": card["id"]}).json()
        client.post(
            f"{API}/work/{card['id']}/finish",
            headers=agent,
            json={"outcome": "failed", "note": "Unavailable", "run_id": taken["lease"]["run_id"]},
        ).raise_for_status()
    if which == "reminder":
        client.post(
            f"{API}/items/{card['id']}/reminders", headers=alex, json={"before_minutes": 0}
        ).raise_for_status()
    actor = Actor(client.get(f"{API}/me", headers=alex).json()["account"]["id"], Source.api)
    module = reminders if which == "reminder" else due
    tick = {
        "due": due.emit_due_events,
        "retry": due.retry_failed_runs,
        "reminder": reminders.fire_reminders,
    }[which]
    change = module.apply_change
    entered = False
    due.system_actor(
        db
    )  # Ensure the interleaving lands after selection, not on system account creation.
    db.commit()

    @contextmanager
    def race(session, system):
        nonlocal entered
        if not entered:
            with SessionLocal() as other:
                lists.update_list(other, actor, todo["id"], filing={"reference": True})
            entered = True
        with change(session, system) as cx:
            yield cx

    monkeypatch.setattr(module, "apply_change", race)
    assert tick(db, now=now + timedelta(minutes=10)) == 0
    assert entered, "the tick must reach its write scope before the reference toggle"
