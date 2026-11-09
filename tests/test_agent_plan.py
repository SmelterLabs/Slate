"""The agent plan ("about to"): `GET /agents/plan`, MCP `slate_work plan`, the digest's
"Agents today" block, and `run_requested_at` on the item view."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from conftest import API
from run_proofs import remember
from slate import dates
from slate.models import Account, Source, utcnow
from slate.service import agents, reminders
from slate.service.core import Actor
from test_mcp import call, http_mcp, ok
from test_permissions import share

DAY = "2026-11-03"
# 02:00 Chicago on the day: the 06:00 run of a date-only task due that day is four hours off.
NOW = datetime.fromisoformat(f"{DAY}T02:00:00-06:00").astimezone(UTC)


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _plan(db, **kw):
    kw.setdefault("now", NOW)
    return agents.plan(db, **kw)


def _by_handle(plan):
    return {a["handle"]: a for a in plan}


def test_plan_lists_what_each_agent_will_run_in_the_next_day(client, alex, todo, make_account, db):
    agent, ch = make_account("agent")
    make_account("agent2")
    today = _item(client, alex, todo["id"], "Date only today", due_at=DAY, assignees=["agent"])
    noon = _item(
        client, alex, todo["id"], "At noon", due_at=f"{DAY}T12:00:00-06:00", assignees=["agent"]
    )
    _item(client, alex, todo["id"], "In three days", due_at="2026-11-06", assignees=["agent"])
    undated = _item(client, alex, todo["id"], "No due date", assignees=["agent2"])
    _item(client, alex, todo["id"], "Nobody's", due_at=DAY)
    _item(client, alex, todo["id"], "A person's", due_at=DAY, assignees=["alex"])

    plan = _by_handle(_plan(db))
    assert list(plan) == ["agent2", "agent"]  # agent2's undated task is takeable now: soonest
    assert plan["agent"]["id"] == agent["id"] and plan["agent"]["display_name"] == "Agent"
    nxt = plan["agent"]["next"]
    assert [n["item_id"] for n in nxt] == [today["id"], noon["id"]]
    assert (
        nxt[0]["reason"] == "due"
        and nxt[0]["list"] == "Todo"
        and nxt[0]["title"] == "Date only today"
    )
    assert nxt[0]["at"] == datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # 06:00 CST
    assert nxt[0]["due_at"] == dates.parse_when(DAY)
    assert nxt[1]["at"] == datetime(2026, 11, 3, 18, 0, tzinfo=UTC)  # the time itself
    assert plan["agent2"]["next"] == [
        {
            "item_id": undated["id"],
            "title": "No due date",
            "list": "Todo",
            "due_at": None,
            "reason": "due",
            "at": None,
        }
    ]
    assert plan["agent"]["leases"] == [] and plan["agent2"]["leases"] == []


def test_run_now_asks_and_leases_show_with_their_reason(client, alex, todo, make_account, db):
    agent, ch = make_account("agent")
    later = _item(client, alex, todo["id"], "Later", due_at="2026-11-10", assignees=["agent"])
    asked = _item(client, alex, todo["id"], "Question", due_at="2026-11-10")
    held = _item(client, alex, todo["id"], "Held", assignees=["agent"])

    r = client.post(f"{API}/items/{later['id']}/run", headers=alex)
    assert r.status_code == 202, r.text
    r = client.post(
        f"{API}/items/{asked['id']}/comments",
        json={"body_md": "Which colour?", "assignee": "agent"},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/work/next", json={"item_id": held["id"]}, headers=ch)
    assert r.status_code == 200 and r.json()["item"]["id"] == held["id"], r.text
    remember(client, ch, r.json())

    (me,) = _plan(db, now=None)  # a real clock: the Run now and the Ask were stamped just now
    by_id = {n["item_id"]: n for n in me["next"]}
    assert by_id[later["id"]]["reason"] == "run_requested"
    assert by_id[later["id"]]["at"] is not None
    assert by_id[asked["id"]]["reason"] == "ask"
    assert held["id"] not in by_id  # it holds it already: that is the lease
    (lease,) = me["leases"]
    assert lease["item_id"] == held["id"] and lease["title"] == "Held"
    assert 0 <= lease["age_seconds"] < 60 and lease["until"] > lease["claimed_at"]


def test_the_route_and_mcp_action(client, alex, todo, make_account):
    make_account("agent")
    _item(client, alex, todo["id"], "Soon", assignees=["agent"])
    r = client.get(f"{API}/agents/plan", headers=alex)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["horizon_hours"] == 24
    assert [a["handle"] for a in body["agents"]] == ["agent"]
    assert body["agents"][0]["next"][0]["title"] == "Soon"


async def test_plan_through_mcp(client, alex, todo, make_account):
    make_account("agent")
    _item(client, alex, todo["id"], "Soon", assignees=["agent"])
    async with http_mcp(alex) as mcp:
        out = ok(await call(mcp, "slate_work", "plan"))
    assert [a["handle"] for a in out["agents"]] == ["agent"]


def test_a_restricted_caller_sees_only_agents_and_items_in_reach(client, alex, todo, make_account):
    # A project's agent is a guest agent; a guest never sees a workspace agent (test below).
    agent, _ = make_account("agent")
    assert (
        client.put(
            f"{API}/accounts/{agent['id']}/role", json={"role": "guest"}, headers=alex
        ).status_code
        == 200
    )
    make_account("agent3")
    r = client.post(f"{API}/sections", json={"name": "Work", "audience": "work"}, headers=alex)
    other = client.post(
        f"{API}/lists", json={"section_id": r.json()["id"], "name": "Clients"}, headers=alex
    ).json()
    _item(client, alex, todo["id"], "Shared job", assignees=["agent"])
    _item(client, alex, other["id"], "Private job", assignees=["agent"])
    _item(client, alex, other["id"], "Agent3's private job", assignees=["agent3"])
    guest = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    # A write token: `next` follows the work loop's scope rule, lists the caller may edit.
    tok = client.post(
        f"{API}/accounts/{guest['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    gh = {"Authorization": f"Bearer {tok['token']}"}
    assert client.get(f"{API}/agents/plan", headers=gh).json()["agents"] == []

    share(client, alex, guest["id"], "list", todo["id"], "view")
    body = client.get(f"{API}/agents/plan", headers=gh).json()
    assert [a["handle"] for a in body["agents"]] == ["agent"]  # in reach, nothing it may edit
    assert body["agents"][0]["next"] == []

    share(client, alex, guest["id"], "list", todo["id"], "edit")
    body = client.get(f"{API}/agents/plan", headers=gh).json()
    assert [a["handle"] for a in body["agents"]] == ["agent"]
    assert [n["title"] for n in body["agents"][0]["next"]] == ["Shared job"]


def test_the_digest_names_what_the_agents_run_today(client, alex, todo, make_account, db):
    make_account("agent")
    _item(client, alex, todo["id"], "Morning run", due_at="2030-01-09", assignees=["agent"])
    _item(client, alex, todo["id"], "Next week", due_at="2030-01-16", assignees=["agent"])
    _item(client, alex, todo["id"], "Always ready", assignees=["agent"])  # undated: now
    account = db.scalar(select(Account).where(Account.handle == "alex"))
    seven = datetime(2030, 1, 9, 13, 0, tzinfo=UTC)  # 07:00 Chicago
    body = reminders.digest_for(db, account, now=seven)
    assert body["counts"] == {"overdue": 0, "today": 0, "waiting": 0}
    (agent,) = body["agents"]
    assert agent["handle"] == "agent"
    assert [n["title"] for n in agent["next"]] == ["Always ready", "Morning run"]
    assert agent["next"][0]["at"] is None
    assert agent["next"][1]["at"].startswith("2030-01-09T12:00")  # 06:00 CST, as a string
    # Only agent news is still news: the digest goes out, and the preview (today's clock,
    # where only the undated task is due) shows the block too.
    assert reminders.send_digests(db, now=seven) == 1
    preview = client.get(f"{API}/me/digest", headers=alex).json()
    assert [n["title"] for n in preview["agents"][0]["next"]] == ["Always ready"]
    assert preview["agents"][0]["next"][0]["reason"] == "due"


def test_the_digest_leaves_the_block_out_when_no_agent_has_anything(client, alex, todo, db):
    account = db.scalar(select(Account).where(Account.handle == "alex"))
    body = reminders.digest_for(db, account, now=utcnow())
    assert "agents" not in body


def test_the_item_view_carries_run_requested_at_until_the_take(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Later", due_at="2026-12-01", assignees=["agent"])
    assert item["run_requested_at"] is None
    assert client.post(f"{API}/items/{item['id']}/run", headers=alex).status_code == 202
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["run_requested_at"] is not None
    listed = client.get(f"{API}/items", params={"list_id": todo["id"]}, headers=alex).json()
    assert listed[0]["run_requested_at"] == got["run_requested_at"]
    r = client.post(f"{API}/work/next", json={"item_id": item["id"]}, headers=ch)
    assert r.status_code == 200 and r.json()["item"]["id"] == item["id"], r.text
    assert r.json()["item"]["run_requested_at"] is None


def test_plan_from_the_service_layer(db):
    from slate.service import accounts, items, lists, sections

    alex, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=alex.id, source=Source.api)
    accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    section = sections.create_section(db, actor, name="Ops", audience="ai")
    todo = lists.create_list(db, actor, section_id=section.id, name="Todo")
    items.create_item(db, actor, list_id=todo.id, title="Job", assignees=["agent"])
    before = utcnow()
    (agent,) = agents.plan(db, now=before)
    assert agent["next"][0]["title"] == "Job"
    assert agents.plan(db, now=before, until=before + timedelta(hours=1)) == agents.plan(
        db, now=before
    )


def test_a_guest_never_sees_a_workspace_agent_on_the_plan(client, alex, todo, make_account):
    make_account("agent")
    _item(client, alex, todo["id"], "Shared job", assignees=["agent"])
    guest = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    tok = client.post(
        f"{API}/accounts/{guest['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    gh = {"Authorization": f"Bearer {tok['token']}"}
    share(client, alex, guest["id"], "list", todo["id"], "edit")
    assert client.get(f"{API}/agents/plan", headers=gh).json()["agents"] == []
    roster = client.get(f"{API}/agents", params={"kind": "all"}, headers=gh).json()
    assert "agent" not in str(roster).lower()
