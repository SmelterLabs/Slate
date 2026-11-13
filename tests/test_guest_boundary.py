"""A project's guest never sees a workspace agent, through any door.

The workspace agent (`my-agent`, a member-role agent) works on a shared project's cards, chat
and pages. Every GET route is then called as the project's guest (a person) and as the
project's own agent (a guest-role agent), with every id the fixture made, and no response may
carry the workspace agent's handle or id. A new route that returns event data, a snapshot or
an account id without the guest scrub fails here without anyone listing it.
"""

from __future__ import annotations

import re

import pytest

from conftest import API

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

    return {
        "agent": agent,
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
    assert leaked == [], "\n".join(map(str, leaked))


def test_the_project_agents_work_context_has_no_workspace_agent(client, alex, world):
    hh = world["guests"]["other-agent"]
    card = world["ids"]["item_id"][0]
    _ok(client.patch(f"{API}/items/{card}", json={"assignees": ["other-agent"]}, headers=alex))
    r = client.post(f"{API}/work/next", json={}, headers=hh)
    assert r.status_code in (200, 204), r.text
    assert _leaks(r.text, world["agent"]) == []


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
