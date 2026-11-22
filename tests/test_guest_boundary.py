"""A project's guest never sees a workspace agent, through any door.

The workspace agent (`my-agent`, a member-role agent) works on a shared project's cards, chat
and pages. Every GET route is then called as the project's guest (a person) and as the
project's own agent (a guest-role agent), with every id the fixture made, and no response may
carry the workspace agent's handle or id. A new route that returns event data, a snapshot or
an account id without the guest scrub fails here without anyone listing it.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from conftest import API
from slate.models import Item, Source, utcnow
from slate.service import access, attention, due
from slate.service.core import Actor, apply_change, event_payload
from test_mcp import call, http_mcp, ok

STREAMS = {"/api/v1/events/stream", "/api/v1/checkin/stream"}


def _account(client, alex, handle, kind, role):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": kind, "role": role},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _ok(r, *codes):
    assert r.status_code in (codes or (200, 201)), r.text
    return r.json() if r.content else None


@pytest.fixture
def world(client, alex):
    sec = _ok(
        client.post(f"{API}/sections", json={"name": "Project", "audience": "work"}, headers=alex)
    )
    _ok(client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex))
    todo = _ok(
        client.post(f"{API}/lists", json={"section_id": sec["id"], "name": "Todo"}, headers=alex)
    )
    agent, ah = _account(client, alex, "my-agent", "agent", "member")
    helper, hh = _account(client, alex, "other-agent", "agent", "guest")
    guest, gh = _account(client, alex, "guest", "person", "guest")
    for acct in (agent, helper, guest):
        _ok(
            client.put(
                f"{API}/shares",
                json={
                    "account_id": acct["id"],
                    "scope_kind": "section",
                    "scope_id": sec["id"],
                    "level": "edit",
                },
                headers=alex,
            )
        )
    # A tag that lives only on a list the guest cannot see.
    elsewhere = _ok(
        client.post(f"{API}/sections", json={"name": "Elsewhere", "audience": "work"}, headers=alex)
    )
    hidden = _ok(
        client.post(
            f"{API}/lists", json={"section_id": elsewhere["id"], "name": "Hidden"}, headers=alex
        )
    )
    _ok(
        client.post(
            f"{API}/items",
            json={"list_id": hidden["id"], "title": "Elsewhere", "tags": ["payroll-secret"]},
            headers=alex,
        )
    )

    card = _ok(
        client.post(
            f"{API}/items",
            json={"list_id": todo["id"], "title": "Shared card", "assignees": ["alex"]},
            headers=alex,
        )
    )
    url = f"{API}/items/{card['id']}"
    _ok(client.patch(url, json={"assignees": ["alex", "my-agent", "guest"]}, headers=alex))
    _ok(client.patch(url, json={"waiting_on_account_id": agent["id"]}, headers=alex))
    _ok(client.patch(url, json={"waiting_on_account_id": None}, headers=alex))
    made = _ok(
        client.post(
            f"{API}/items", json={"list_id": todo["id"], "title": "Agent's card"}, headers=ah
        )
    )
    _ok(client.post(f"{url}/links", json={"kind": "relates", "item_id": made["id"]}, headers=ah))
    # Standing: the workspace agent's own line on its card (hidden author), and the project
    # agent's line on the shared card, whose move would otherwise be the workspace agent.
    _ok(
        client.post(
            f"{API}/items/{made['id']}/report",
            json={"state": "blocked", "status": "my-agent checked the totals"},
            headers=ah,
        )
    )
    _ok(
        client.post(
            f"{url}/report",
            json={"state": "blocked", "status": "Checked the shared card"},
            headers=hh,
        )
    )
    checklist = _ok(
        client.post(f"{url}/checklists", json={"name": "Steps", "items": []}, headers=alex)
    )
    _ok(
        client.post(
            f"{API}/checklists/{checklist['id']}/items",
            json={"title": "Agent's step", "assignee": "my-agent"},
            headers=alex,
        )
    )
    _ok(client.post(f"{url}/comments", json={"body_md": "Card note"}, headers=alex))
    _ok(
        client.post(
            f"{url}/comments", json={"body_md": "Agent?", "assignee": "my-agent"}, headers=alex
        )
    )
    _ok(client.post(f"{url}/comments", json={"body_md": "On it"}, headers=ah))
    _ok(
        client.post(
            f"{url}/comments", json={"body_md": "Helper?", "assignee": "other-agent"}, headers=gh
        )
    )

    key = f"section:{sec['id']}"
    msgs = f"{API}/channels/{key}/messages"
    question = _ok(client.post(msgs, json={"body_md": "A question from the guest"}, headers=gh))
    _ok(client.post(msgs, json={"body_md": "An answer", "parent_id": question["id"]}, headers=ah))
    _ok(client.post(msgs, json={"body_md": "@my-agent members only"}, headers=alex))
    _ok(client.post(msgs, json={"body_md": "Hello all"}, headers=alex))

    doc = _ok(
        client.post(
            f"{API}/docs",
            json={"list_id": todo["id"], "title": "Agent page", "body_md": "Draft"},
            headers=ah,
        )
    )
    _ok(
        client.post(f"{API}/docs/{doc['id']}/comments", json={"body_md": "Page note"}, headers=alex)
    )
    _ok(client.patch(f"{API}/sections/{sec['id']}", json={"project": True}, headers=alex))
    _ok(
        client.patch(url, json={"due_at": (utcnow() + timedelta(days=1)).isoformat()}, headers=alex)
    )
    _ok(
        client.patch(
            f"{API}/items/{made['id']}",
            json={"due_at": (utcnow() + timedelta(hours=1)).isoformat(), "assignees": ["my-agent"]},
            headers=alex,
        )
    )
    _ok(
        client.patch(
            f"{API}/lists/{todo['id']}",
            json={"default_assignee_id": agent["id"], "note_md": "Shared filing note"},
            headers=alex,
        )
    )

    return {
        "agent": agent,
        "agent_headers": ah,
        "hidden_ids": [elsewhere["id"], hidden["id"]],
        "guests": {"guest": gh, "other-agent": hh},
        "ids": {
            "item_id": [card["id"], made["id"]],
            "section_id": [sec["id"]],
            "list_id": [todo["id"]],
            "doc_id": [doc["id"]],
            "key": [key, f"agent:{helper['id']}"],
            "message_id": [question["id"]],
            "account_id": [guest["id"], helper["id"]],
            "version_no": ["1"],
        },
    }


def _leaks(text: str, agent: dict) -> list[str]:
    return [m for m in (agent["id"], agent["handle"]) if m.lower() in text.lower()]


def _calls(client, ids):
    """Every GET route with each id the fixture knows; a route with an unknown parameter is
    called once with a placeholder (it answers 404, which is fine)."""
    spec = client.get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        if "get" not in ops or path in STREAMS:
            continue
        names = re.findall(r"{(\w+)}", path)
        combos = [{}]
        for name in names:
            combos = [{**c, name: v} for c in combos for v in ids.get(name, ["nothing"])]
        for combo in combos:
            yield path.format(**combo)


EXTRA = [
    "/api/v1/events?limit=1000",
    "/api/v1/events?tail=true&limit=1000",
    "/api/v1/audit?limit=500",
    "/api/v1/audit.csv",
    "/api/v1/export?format=csv",
    "/api/v1/inbox?limit=200",
    "/api/v1/inbox/alerts?after=0",
    "/api/v1/items?fields=id,standing,stalled,ask,later,parked_until,parked_note",
    "/api/v1/search?q=card",
    "/api/v1/search?q=agent",
    "/api/v1/checkin?since=0",
]


def test_no_get_route_shows_a_guest_the_workspace_agent(client, world):
    calls = [*_calls(client, world["ids"]), *EXTRA]
    assert len(calls) > 80
    leaked = []
    for who, headers in world["guests"].items():
        for path in calls:
            r = client.get(path, headers=headers)
            assert r.status_code < 500, (who, path, r.text)
            found = _leaks(r.text, world["agent"])
            if found:
                leaked.append((who, path, found))
            assert not any(hidden_id in r.text for hidden_id in world["hidden_ids"]), (who, path)
    assert leaked == [], "\n".join(map(str, leaked))


@pytest.mark.parametrize("who", ["guest", "other-agent"])
def test_added_view_fields_follow_the_shared_project(client, alex, world, who):
    headers = world["guests"][who]
    (section_id,) = world["ids"]["section_id"]
    (list_id,) = world["ids"]["list_id"]
    card_id, agent_card_id = world["ids"]["item_id"]
    shared = _ok(client.get(f"{API}/lists/{list_id}", headers=headers))
    assert shared["note_md"] == "Shared filing note"
    assert shared["default_assignee_id"] is None
    assert shared["default_tags"] == []
    assert shared["reference"] is False
    [project] = _ok(client.get(f"{API}/projects", headers=headers))
    assert project["id"] == section_id and project["project"] is True
    assert project["next_step"]["item_id"] == card_id
    assert world["agent"]["id"] not in project["next_step"]["assignee_ids"]
    views = {
        row["id"]: row
        for row in _ok(
            client.get(
                f"{API}/items?fields=id,standing,stalled,ask,later,parked_until,parked_note",
                headers=headers,
            )
        )
    }
    assert views[agent_card_id]["standing"] is None
    assert views[card_id]["standing"]["line"] == "Checked the shared card"
    assert views[card_id]["standing"]["move_id"] != world["agent"]["id"]
    assert not views[card_id]["stalled"] and not views[card_id]["later"]
    assert views[card_id]["parked_until"] is None and views[card_id]["parked_note"] is None
    for path in ("/settings/inbox", "/health"):
        _ok(client.get(f"{API}{path}", headers=headers), 403)
    _ok(client.get(f"{API}/today/sweep", headers=headers))
    prefs = _ok(client.get(f"{API}/me", headers=headers))["prefs"]
    assert "inbox_list_id" not in prefs
    checkin = _ok(client.get(f"{API}/checkin", headers=headers))
    assert any(
        row["id"] == list_id and row["note_md"] == shared["note_md"] for row in checkin["lists"]
    )
    _ok(client.put(f"{API}/settings/inbox", json={"inbox_list_id": list_id}, headers=alex))
    _ok(client.get(f"{API}/settings/inbox", headers=headers), 403)


def test_the_project_agents_work_context_has_no_workspace_agent(client, alex, world):
    hh = world["guests"]["other-agent"]
    card = world["ids"]["item_id"][0]
    _ok(client.patch(f"{API}/items/{card}", json={"assignees": ["other-agent"]}, headers=alex))
    r = client.post(f"{API}/work/next", json={}, headers=hh)
    assert r.status_code in (200, 204), r.text
    assert _leaks(r.text, world["agent"]) == []


@pytest.mark.parametrize("who", ["guest", "other-agent"])
async def test_guest_mcp_views_match_the_rest_boundary(world, who):
    headers = world["guests"][who]
    async with http_mcp(headers) as mcp:
        for tool, action in (
            ("slate_item", "list"),
            ("slate_list", "list"),
            ("slate_section", "list"),
            ("slate_checkin", "get"),
            ("slate_inbox", "list"),
        ):
            value = ok(await call(mcp, tool, action))
            rendered = str(value)
            assert _leaks(rendered, world["agent"]) == [], (who, tool, action)
            assert not any(key in rendered for key in world["hidden_ids"]), (who, tool, action)


@pytest.mark.parametrize("who", ["guest", "other-agent"])
def test_workspace_agent_stall_and_failure_are_invisible(client, db, world, who):
    card_id = world["ids"]["item_id"][1]
    agent_id = world["agent"]["id"]
    item = db.get(Item, card_id)
    with apply_change(db, Actor(agent_id, Source.api)) as cx:
        item.stalled_at = utcnow()
        stalled = cx.record(
            "item.stalled", item_id=card_id, after={"agent_id": agent_id}, covers=[item]
        )
        failure = cx.record(
            "item.run_failed", item_id=card_id, after={"note": "Private execution details"}
        )
    headers = world["guests"][who]
    viewer = next(
        i for i in client.get(f"{API}/items", headers=headers).json() if i["id"] == card_id
    )
    assert viewer["stalled"] is False
    gate = access.EventGate(_ok(client.get(f"{API}/me", headers=headers))["account"]["id"])
    gate.load(db)
    assert gate.project(db, event_payload(stalled)) is None
    assert gate.project(db, event_payload(failure)) is None


def test_digest_snapshot_checks_the_question_not_just_the_card(client, db, world):
    card_id = world["ids"]["item_id"][0]
    viewer = _ok(client.get(f"{API}/me", headers=world["guests"]["guest"]))["account"]["id"]
    hidden_ask = _ok(
        client.post(
            f"{API}/items/{card_id}/comments",
            json={"body_md": "For the workspace agent only", "assignee": "my-agent"},
            headers=world["agent_headers"],
        )
    )
    with apply_change(db, due.system_actor(db)) as cx:
        cx.record(
            "account.digest",
            subject_id=viewer,
            after={
                "today": [{"id": card_id, "title": "Shared card"}],
                "questions": [
                    {
                        "id": card_id,
                        "comment_id": hidden_ask["id"],
                        "question": hidden_ask["body_md"],
                    }
                ],
            },
        )
    logged = _ok(client.get(f"{API}/events?type=account.digest", headers=world["guests"]["guest"]))
    [digest] = logged["events"]
    assert digest["after"]["questions"] == []


def test_a_guest_receives_a_digest_with_only_visible_questions(client, alex, db, world):
    headers = world["guests"]["guest"]
    account = _ok(client.get(f"{API}/me", headers=headers))["account"]
    card_id = world["ids"]["item_id"][0]
    ask = _ok(
        client.post(
            f"{API}/items/{card_id}/comments",
            json={"body_md": "Confirm the deadline", "assignee": account["handle"]},
            headers=alex,
        )
    )
    attention.release(db, utcnow())  # the cursor starts at existing history
    with apply_change(db, due.system_actor(db)) as cx:
        cx.record(
            "account.digest",
            subject_id=account["id"],
            after={
                "date": "2026-11-21",
                "counts": {"overdue": 0, "today": 0, "waiting": 0},
                "overdue": [],
                "today": [],
                "waiting": [],
                "questions": [
                    {
                        "id": card_id,
                        "comment_id": ask["id"],
                        "title": "Shared card",
                        "question": "Confirm the deadline",
                        "at": ask["created_at"],
                        "age_minutes": 0,
                    }
                ],
            },
        )
    attention.release(db, utcnow())
    alerts = _ok(client.get(f"{API}/inbox/alerts?after=0", headers=headers))
    assert any(a["reason"] == "digest" and "Confirm the deadline" in a["body"] for a in alerts)


def test_parked_notice_does_not_name_a_recipient_who_lost_access(client, alex, db, world):
    card_id, _ = world["ids"]["item_id"]
    (section_id,) = world["ids"]["section_id"]
    former, _ = _account(client, alex, "former", "person", "guest")
    share = _ok(
        client.put(
            f"{API}/shares",
            json={
                "account_id": former["id"],
                "scope_kind": "section",
                "scope_id": section_id,
                "level": "view",
            },
            headers=alex,
        )
    )
    item = db.get(Item, card_id)
    actor = due.system_actor(db)
    with apply_change(db, actor) as cx:
        cx.set(item, "parked_until", "2026-01-01", "item.updated")
    _ok(client.get(f"{API}/items/{card_id}", headers=world["guests"]["guest"]))
    _ok(
        client.patch(
            f"{API}/items/{card_id}", json={"assignees": ["guest", "former"]}, headers=alex
        )
    )
    _ok(client.delete(f"{API}/shares/{share['id']}", headers=alex), 204)
    assert due.unpark_due(db, now=utcnow()) == 1
    listed = _ok(client.get(f"{API}/events?type=item.unparked", headers=world["guests"]["guest"]))
    [event] = listed["events"]
    assert world["ids"]["account_id"][0] in event["after"]["notify"]
    assert former["id"] not in event["after"]["notify"]


def test_a_list_only_guest_does_not_get_a_project_wide_next_step(client, alex, world):
    section = _ok(
        client.post(
            f"{API}/sections", json={"name": "Second project", "audience": "work"}, headers=alex
        )
    )
    _ok(
        client.patch(
            f"{API}/sections/{section['id']}", json={"project": True, "private": True}, headers=alex
        )
    )
    shared = _ok(
        client.post(
            f"{API}/lists", json={"name": "Shared", "section_id": section["id"]}, headers=alex
        )
    )
    _ok(
        client.put(
            f"{API}/shares",
            json={
                "account_id": world["ids"]["account_id"][0],
                "scope_kind": "list",
                "scope_id": shared["id"],
                "level": "view",
            },
            headers=alex,
        )
    )
    _ok(
        client.post(
            f"{API}/items",
            json={
                "list_id": shared["id"],
                "title": "Visible step",
                "due_at": (utcnow() + timedelta(days=1)).isoformat(),
                "assignees": ["alex"],
            },
            headers=alex,
        )
    )
    projects = _ok(client.get(f"{API}/projects", headers=world["guests"]["guest"]))
    project = next(row for row in projects if row["id"] == section["id"])
    assert project["next_step"] is None


@pytest.mark.parametrize("who", ["guest", "other-agent"])
def test_parking_respects_the_guest_scheduling_boundary(client, world, who):
    headers = world["guests"][who]
    _, agent_card = world["ids"]["item_id"]
    changes = {
        "parked_until": (utcnow() + timedelta(days=2)).date().isoformat(),
        "parked_note": "Waiting for materials",
    }
    refused = client.patch(f"{API}/items/{agent_card}", json=changes, headers=headers)
    assert refused.status_code == 403
    assert _leaks(refused.text, world["agent"]) == []
    [list_id] = world["ids"]["list_id"]
    owned = _ok(
        client.post(
            f"{API}/items",
            json={"list_id": list_id, "title": "Guest task", "assignees": [who]},
            headers=headers,
        )
    )
    assert [a["handle"] for a in owned["assignees"]] == [who]
    parked = _ok(client.patch(f"{API}/items/{owned['id']}", json=changes, headers=headers))
    assert parked["parked_until"] == changes["parked_until"]


def test_an_escalation_does_not_expose_a_source_after_a_task_moves(client, alex, db, world):
    headers = world["guests"]["guest"]
    account_id = world["ids"]["account_id"][0]
    card_id = world["ids"]["item_id"][0]
    with apply_change(db, due.system_actor(db)) as cx:
        source = cx.record("item.updated", item_id=card_id, field="title")
        escalation = cx.record(
            "inbox.escalated",
            subject_id=account_id,
            after={"event_id": source.id, "reason": "waiting", "minutes": 120},
        )
    _ok(
        client.post(
            f"{API}/items/bulk",
            json={"ids": [card_id], "changes": {"list_id": world["hidden_ids"][1]}},
            headers=alex,
        )
    )
    logged = _ok(client.get(f"{API}/events?type=inbox.escalated", headers=headers))["events"]
    assert all(not row["after"] for row in logged)
    gate = access.EventGate(account_id)
    gate.load(db)
    assert gate.project(db, event_payload(escalation)) is None


def test_system_transitions_do_not_expose_workspace_agent_activity(client, db, world):
    card_id = world["ids"]["item_id"][1]
    agent_id = world["agent"]["id"]
    headers = world["guests"]["guest"]
    account_id = world["ids"]["account_id"][0]
    with apply_change(db, due.system_actor(db)) as cx:
        idle = cx.record(
            "item.reported",
            item_id=card_id,
            field="agent_state",
            before={"by": agent_id, "state": "working", "note": "Workspace-only progress"},
            after={"state": "idle", "reason": "stale"},
        )
        retry = cx.record(
            "item.run_requested",
            item_id=card_id,
            after={"for": [agent_id], "reason": "stalled"},
        )
    events = _ok(client.get(f"{API}/events?item_id={card_id}", headers=headers))["events"]
    assert {e["id"] for e in events}.isdisjoint({idle.id, retry.id})
    gate = access.EventGate(account_id)
    gate.load(db)
    assert all(gate.project(db, event_payload(ev)) is None for ev in (idle, retry))


def test_only_the_person_asked_can_answer_the_project_agents_option(client, world):
    card_id, _ = world["ids"]["item_id"]
    helper = world["guests"]["other-agent"]
    guest = world["guests"]["guest"]
    _ok(
        client.post(
            f"{API}/items/{card_id}/report",
            json={
                "state": "blocked",
                "waiting_on": "guest",
                "question": "Use this date?",
                "options": ["Yes", "No"],
            },
            headers=helper,
        )
    )
    card = _ok(client.get(f"{API}/items/{card_id}", headers=guest))
    assert card["ask"]["question"] == "Use this date?"
    assert card["ask"]["options"] == ["Yes", "No"]
    [ask] = [
        c for c in _ok(client.get(f"{API}/items/{card_id}/comments", headers=guest)) if c["ask"]
    ]
    path = f"{API}/comments/{ask['id']}/answer"
    _ok(client.post(path, json={"option": "Yes"}, headers=helper), 403)
    reply = _ok(client.post(path, json={"option": "Yes"}, headers=guest))
    assert reply["body_md"] == "Yes"
    assert _leaks(reply["body_md"], world["agent"]) == []


def test_guests_do_not_see_multiple_questions_from_a_workspace_agent(client, alex, world):
    card, _ = world["ids"]["item_id"]
    guest = world["guests"]["guest"]
    questions = [{"question": "Check the totals?"}, {"question": "Use the Friday date?"}]
    path = f"{API}/items/{card}"
    _ok(
        client.post(
            f"{path}/report",
            json={"state": "blocked", "questions": questions},
            headers=world["agent_headers"],
        )
    )
    assert len(_ok(client.get(path, headers=alex))["ask"]["questions"]) == 2
    assert _ok(client.get(path, headers=guest))["ask"] is None
    comments = _ok(client.get(f"{path}/comments", headers=guest))
    assert all(
        comment["ask"] is None and "Check the totals?" not in comment["body_md"]
        for comment in comments
    )


def test_guest_agent_cannot_bypass_explicit_assignee_reach_with_a_closing_ask(client, alex, world):
    card, _ = world["ids"]["item_id"]
    path = f"{API}/items/{card}"
    before = _ok(client.get(f"{path}/comments", headers=alex))
    headers = world["guests"]["other-agent"]
    plain = client.post(
        f"{path}/comments", json={"assignee": "my-agent", "body_md": "Report."}, headers=headers
    )
    assert plain.status_code == 422, plain.text
    response = client.post(
        f"{path}/comments",
        json={"assignee": "my-agent", "body_md": "Report.\nSLATE: ask guest: Which file?"},
        headers=headers,
    )
    assert response.status_code == plain.status_code, response.text
    assert response.json() == plain.json()
    assert _ok(client.get(f"{path}/comments", headers=alex)) == before


def test_a_guest_still_sees_the_card_change_without_the_agent(client, alex, world):
    gh = world["guests"]["guest"]
    card = world["ids"]["item_id"][0]
    want = [([], ["alex"]), (["alex"], ["alex", "guest"])]
    for path, key in (
        (f"/api/v1/audit?type=item.assigned&item_id={card}", "entries"),
        (f"/api/v1/events?type=item.assigned&item_id={card}", "events"),
    ):
        rows = sorted(client.get(path, headers=gh).json()[key], key=lambda e: e["id"])
        assert [(e["before"], e["after"]) for e in rows] == want, path
    feed = client.get(f"{API}/items/{card}/activity", params={"include": "events"}, headers=gh)
    assigned = [e["event"] for e in feed.json() if e["event"]["type"] == "item.assigned"]
    assert [(e["before"], e["after"]) for e in assigned] == want
    csv = client.get(f"{API}/audit.csv", params={"type": "item.assigned"}, headers=gh).text
    assert "guest" in csv and "my-agent" not in csv


def test_members_still_see_the_workspace_agent(client, alex, world):
    card = world["ids"]["item_id"][0]
    for path in (
        f"/api/v1/audit?type=item.assigned&item_id={card}",
        f"/api/v1/items/{card}/activity",
        f"/api/v1/items/{card}",
        f"/api/v1/items/{card}/checklists",
        "/api/v1/export",
    ):
        assert _leaks(client.get(path, headers=alex).text, world["agent"]), path


def test_a_guests_export_lists_only_tags_it_can_reach(client, alex, world):
    gh = world["guests"]["guest"]
    assert client.get(f"{API}/tags", headers=gh).json() == []
    assert [t["name"] for t in client.get(f"{API}/export", headers=gh).json()["tags"]] == []
    everything = client.get(f"{API}/export", headers=alex).json()
    assert [t["name"] for t in everything["tags"]] == ["payroll-secret"]


def test_a_guests_inbox_names_no_workspace_agent(client, alex, world):
    gh = world["guests"]["guest"]
    entries = client.get(f"{API}/inbox", params={"limit": 200}, headers=gh).json()
    assigned = [e for e in entries if e["event"]["type"] == "item.assigned"]
    assert assigned and all("my-agent" not in e["event"]["after"] for e in assigned)
