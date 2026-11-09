"""Capacity per account and the Box view's numbers: REST and access."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from conftest import API
from slate.models import Source
from slate.service import accounts, items, lists, sections, workload
from slate.service.core import Actor
from slate.service.errors import Invalid


@pytest.fixture
def actor(db) -> Actor:
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return Actor(account_id=account.id, source=Source.api)


@pytest.fixture
def build(db, actor):
    section = sections.create_section(db, actor, name="Work", audience="work")
    return lists.create_list(db, actor, section_id=section.id, name="Build")


@pytest.fixture
def agent(db, actor):
    return accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")


def _task(db, actor, build, title, **fields):
    return items.create_item(db, actor, list_id=build.id, title=title, **fields)


def test_defaults_and_setting_capacity(db, actor, agent):
    caps = {c["account_id"]: c for c in workload.list_capacities(db)}
    assert caps[actor.account_id]["hours_per_day"] == 8
    assert caps[actor.account_id]["work_days"] == [0, 1, 2, 3, 4]
    assert caps[agent.id]["work_days"] == [0, 1, 2, 3, 4, 5, 6]
    assert not caps[agent.id]["custom"]
    got = workload.set_capacity(db, actor, agent.id, {"hours_per_day": 4, "tasks_per_day": 2})
    assert got["custom"] and got["hours_per_day"] == 4 and got["tasks_per_day"] == 2
    got = workload.set_capacity(db, actor, agent.id, {"hours_per_day": None, "work_days": [0, 2]})
    assert got["hours_per_day"] is None and got["work_days"] == [0, 2]
    with pytest.raises(Invalid):
        workload.set_capacity(db, actor, agent.id, {"hours_per_day": 30})
    with pytest.raises(Invalid):
        workload.set_capacity(db, actor, agent.id, {"work_days": [7]})
    with pytest.raises(Invalid):
        workload.set_capacity(db, actor, agent.id, {"tasks_per_day": 2.5})
    assert not workload.reset_capacity(db, actor, agent.id)["custom"]
    assert "slate" not in {r.handle for r in workload.roster(db)}


def test_rest_and_permissions(client, alex, make_account, todo):
    agent, ah = make_account("agent")
    r = client.patch(
        f"{API}/accounts/{agent['id']}/capacity", json={"hours_per_day": 2}, headers=ah
    )
    assert r.status_code == 200 and r.json()["hours_per_day"] == 2
    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    r = client.patch(f"{API}/accounts/{me}/capacity", json={"hours_per_day": 1}, headers=ah)
    assert r.status_code == 403
    client.post(
        f"{API}/items",
        json={
            "list_id": todo["id"],
            "title": "Ship",
            "assignees": ["agent"],
            "estimate_minutes": 180,
        },
        headers=alex,
    )
    caps = client.get(f"{API}/capacities", headers=ah).json()
    assert {c["account_id"] for c in caps} >= {agent["id"], me}
    r = client.delete(f"{API}/accounts/{agent['id']}/capacity", headers=alex)
    assert r.json()["custom"] is False
    boxes = client.get(f"{API}/workload/boxes", params={"kinds": "agents"}, headers=ah).json()
    assert [b["account"] and b["account"]["handle"] for b in boxes["boxes"]] == ["agent", None]
    assert boxes["boxes"][0]["open"] == 1 and boxes["boxes"][0]["minutes"] == 180


def test_boxes_count_per_assignee(db, actor, build, agent):
    me = accounts.get_account(db, actor.account_id).handle
    _task(
        db, actor, build, "Late", assignees=[me], estimate_minutes=60, due_at="2026-10-15T22:00:00Z"
    )
    doing = _task(db, actor, build, "Doing", assignees=[me, "agent"], estimate_minutes=30)
    items.update_item(db, actor, doing.id, {"status": "in progress"})
    _task(db, actor, build, "Orphan")
    shipped = _task(db, actor, build, "Shipped", assignees=["agent"], estimate_minutes=999)
    items.update_item(db, actor, shipped.id, {"status": "done"})

    data = workload.boxes(db, now=datetime.now(UTC))
    boxes = {(b["account"] or {}).get("handle"): b for b in data["boxes"]}
    mine, box, nobody = boxes[me], boxes["agent"], boxes[None]
    assert (mine["open"], mine["active"], mine["overdue"], mine["minutes"]) == (1, 1, 1, 90)
    assert not mine["over"]
    assert (box["open"], box["active"], box["done"]) == (0, 1, 1)
    assert box["minutes"] == 30 and box["capacity_minutes"] == 7 * 480
    assert nobody["open"] == 1 and nobody["capacity_minutes"] is None
    workload.set_capacity(db, actor, agent.id, {"hours_per_day": 0.05})  # 21 minutes a week
    assert {(b["account"] or {}).get("handle"): b for b in workload.boxes(db)["boxes"]}["agent"][
        "over"
    ]
    with pytest.raises(Invalid):
        workload.boxes(db, done_days=0)
