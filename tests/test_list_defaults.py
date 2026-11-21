from datetime import UTC, datetime, timedelta

import pytest

from conftest import API, workspace_events
from slate.models import utcnow
from slate.service import due, recurrences, reminders
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
        client, alex, todo, note_md=None, default_assignee_id=None, default_tags=[], reference=False
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
    assert {key: created.json()[key] for key in data} == data


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
    monkeypatch.setattr(aiadd, "enabled", lambda: True)
    monkeypatch.setattr(aiadd, "fill_later", lambda *_: None)
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
    recurrences.materialize(db, utcnow())
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
    assert "This list:\nProofread the draft." in brief
    taken = client.post(f"{API}/work/next", headers=agent, json={"item_id": card["id"]})
    assert taken.status_code == 200, taken.text
    assert taken.json()["brief"] == brief
    second = file(client, agent, assignees=["my-agent"])
    async with http_mcp(agent) as mcp:
        result = ok(await call(mcp, "slate_work", "next", item_id=second["id"]))
    assert "This list:\nProofread the draft." in result["brief"]
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
