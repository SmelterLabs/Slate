"""Run now and the due gate: POST /items/{id}/run, /work/next with item_id, and an agent's task
with a due date waiting for its run time (06:00 Chicago for a date-only due date)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import API
from run_proofs import remember, run_fields
from slate import dates
from slate import db as slate_db
from slate.models import Account, Source
from slate.service import work
from slate.service.core import Actor


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _run(client, h, item_id):
    return client.post(f"{API}/items/{item_id}/run", headers=h)


def _tomorrow() -> str:
    return (datetime.now(dates.LOCAL).date() + timedelta(days=1)).isoformat()


# --- Run now --------------------------------------------------------------------------------


def test_run_records_item_run_requested_for_the_agent_assignees(client, alex, todo, make_account):
    agent, _ = make_account("agent")
    make_account("member", kind="person")
    item = _item(client, alex, todo["id"], "Send invoice", assignees=["agent", "member"])

    r = _run(client, alex, item["id"])
    assert r.status_code == 202, r.text
    ev = r.json()
    assert ev["type"] == "item.run_requested" and ev["item_id"] == item["id"]
    assert ev["after"] == {"for": [agent["id"]], "reason": "now"}
    with slate_db.SessionLocal() as s:
        alex_id = s.query(Account.id).filter(Account.handle == "alex").scalar()
    assert ev["actor_id"] == alex_id


def test_run_is_refused_without_an_agent_or_when_done(client, alex, todo, make_account):
    make_account("agent")
    make_account("member", kind="person")
    people_only = _item(client, alex, todo["id"], "Call mum", assignees=["member"])
    assert _run(client, alex, people_only["id"]).status_code == 422

    done = _item(client, alex, todo["id"], "Old", assignees=["agent"])
    r = client.post(f"{API}/items/{done['id']}/report", json={"state": "done"}, headers=alex)
    assert r.status_code == 200, r.text
    assert _run(client, alex, done["id"]).status_code == 422

    archived = _item(client, alex, todo["id"], "Gone", assignees=["agent"])
    r = client.post(f"{API}/items/{archived['id']}/archive", headers=alex)
    assert r.status_code in (200, 204), r.text
    assert _run(client, alex, archived["id"]).status_code == 422


def test_run_needs_a_write_token(client, alex, todo, make_account):
    make_account("agent")
    _, reader = make_account("agent2", scopes=("read",))
    item = _item(client, alex, todo["id"], "Send invoice", assignees=["agent"])
    assert _run(client, reader, item["id"]).status_code == 403


def test_run_while_an_agent_is_working_on_it_says_who_has_it(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Send invoice", assignees=["agent"])
    assert _next(client, ch)["item"]["id"] == item["id"]
    r = _run(client, alex, item["id"])
    assert r.status_code == 409
    assert r.json()["detail"] == "Agent already has it (working under a minute)"

    # Blocked or finished: Run now is allowed again.
    r = client.post(
        f"{API}/work/{item['id']}/finish",
        json=run_fields(client, ch, item["id"], **{"outcome": "review"}),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert _run(client, alex, item["id"]).status_code == 202


# --- The due gate (gap A) ------------------------------------------------------------------


def test_an_agents_task_due_later_waits_and_one_with_no_due_date_does_not(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    later = _item(client, alex, todo["id"], "Later", assignees=["agent"], due_at=_tomorrow())
    assert _next(client, ch)["item"] is None

    now_task = _item(client, alex, todo["id"], "No due date", assignees=["agent"])
    got = _next(client, ch)
    assert got["item"]["id"] == now_task["id"]
    assert got["remaining"] == 0  # the due-later task is not counted either
    assert later["id"] != got["item"]["id"]


def test_an_overdue_agent_task_is_takeable(client, alex, todo, make_account):
    _, ch = make_account("agent")
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    item = _item(client, alex, todo["id"], "Late", assignees=["agent"], due_at=past)
    assert _next(client, ch)["item"]["id"] == item["id"]


def test_run_now_opens_the_gate_until_the_next_take(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Later", assignees=["agent"], due_at=_tomorrow())
    assert _next(client, ch)["item"] is None

    assert _run(client, alex, item["id"]).status_code == 202
    got = _next(client, ch)
    assert got["item"]["id"] == item["id"]

    # Giving it back: the Run now was used by that take, so it waits for its run time again.
    r = client.post(
        f"{API}/work/{item['id']}/finish",
        json=run_fields(client, ch, item["id"], **{"outcome": "release"}),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    r = client.patch(f"{API}/items/{item['id']}", json={"assignees": ["agent"]}, headers=alex)
    assert r.status_code == 200, r.text
    assert _next(client, ch)["item"] is None


def test_people_and_the_pool_are_not_gated(client, alex, todo, make_account):
    _, dh = make_account("member", kind="person")
    item = _item(client, alex, todo["id"], "Later", assignees=["member"], due_at=_tomorrow())
    assert _next(client, dh)["item"]["id"] == item["id"]

    _, ch = make_account("agent")
    pooled = _item(client, alex, todo["id"], "Pool", due_at=_tomorrow())
    assert _next(client, ch, pool=True)["item"]["id"] == pooled["id"]


def test_date_only_due_opens_at_six_chicago_on_the_day(db):
    from slate.service import accounts, items, lists, sections

    alex, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=alex.id, source=Source.api)
    agent = accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    section = sections.create_section(db, actor, name="Ops", audience="ai")
    todo = lists.create_list(db, actor, section_id=section.id, name="Todo")
    day = "2026-11-03"
    dated = items.create_item(
        db, actor, list_id=todo.id, title="Date only", due_at=day, assignees=["agent"]
    )
    timed = items.create_item(
        db,
        actor,
        list_id=todo.id,
        title="At noon",
        due_at=f"{day}T12:00:00-06:00",
        assignees=["agent"],
    )
    # A time of exactly 17:00 given on purpose is a time, not a date-only due date.
    five = items.create_item(
        db,
        actor,
        list_id=todo.id,
        title="At five",
        due_at=f"{day}T17:00:00-06:00",
        assignees=["agent"],
    )
    assert dated.due_date_only and not timed.due_date_only and not five.due_date_only

    def takeable(local_hm: str) -> set[str]:
        now = datetime.fromisoformat(f"{day}T{local_hm}:00-06:00").astimezone(UTC)
        q = work.eligible(db, agent.id, now=now).with_only_columns(work.Item.id)
        return set(db.scalars(q))

    assert takeable("05:59") == set()
    assert takeable("06:00") == {dated.id}
    assert takeable("11:59") == {dated.id}
    assert takeable("12:00") == {dated.id, timed.id}
    assert takeable("18:00") == {dated.id, timed.id, five.id}


def test_run_at_rule():
    day_due = dates.parse_when("2026-11-03")  # 17:00 CST = 23:00 UTC
    assert dates.is_date_only_input("2026-11-03") and dates.is_date_only_input(" 2026-11-03 ")
    assert not dates.is_date_only_input("2026-11-03T17:00:00-06:00")
    assert not dates.is_date_only_input(day_due)  # a datetime always carries a time
    assert dates.run_at(day_due, True) == datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # 06:00 CST
    assert dates.run_at(day_due, False) == day_due  # 17:00 given as a time runs at 17:00
    summer = dates.parse_when("2026-07-01")  # 17:00 CDT = 22:00 UTC
    assert dates.run_at(summer, True) == datetime(2026, 7, 1, 11, 0, tzinfo=UTC)  # 06:00 CDT
    timed = dates.parse_when("2026-11-03T09:30:00-06:00")
    assert dates.run_at(timed, False) == timed
    # Between 06:00 and 17:00 Chicago, today's 17:00 is the one early date-only due instant.
    assert dates.early_date_only_due(datetime(2026, 11, 3, 15, 0, tzinfo=UTC)) == day_due
    assert dates.early_date_only_due(datetime(2026, 11, 3, 11, 59, tzinfo=UTC)) is None
    assert dates.early_date_only_due(datetime(2026, 11, 3, 23, 0, tzinfo=UTC)) is None


# --- Run the task that was pressed (gap B) -------------------------------------------------


def test_next_with_item_id_takes_that_task_or_nothing(client, alex, todo, make_account):
    _, ch = make_account("agent")
    urgent = _item(client, alex, todo["id"], "Urgent", assignees=["agent"], priority="urgent")
    pressed = _item(client, alex, todo["id"], "Low", assignees=["agent"], priority="low")
    assert _run(client, alex, pressed["id"]).status_code == 202

    got = _next(client, ch, item_id=pressed["id"])
    assert got["item"]["id"] == pressed["id"]

    other = _item(client, alex, todo["id"], "Not mine")
    assert _next(client, ch, item_id=other["id"], resume=False)["item"] is None
    assert urgent["id"] != pressed["id"]


def test_next_with_item_id_resumes_it_when_already_held(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Mine", assignees=["agent"])
    first = _next(client, ch, item_id=item["id"])
    again = _next(client, ch, item_id=item["id"], run_id=first["lease"]["run_id"])
    assert first["item"]["id"] == again["item"]["id"] == item["id"]
    assert again["resumed"] is True


async def test_mcp_item_run_and_work_next_item_id(alex, make_account):
    from test_mcp import call, http_mcp, make_list, ok

    _, ch = make_account("agent")
    async with http_mcp(alex) as mcp:
        todo = await make_list(mcp)
        item = ok(
            await call(
                mcp,
                "slate_item",
                "create",
                list_id=todo["id"],
                title="Later",
                assignees=["agent"],
                due_at=_tomorrow(),
            )
        )
        ev = ok(await call(mcp, "slate_item", "run", id=item["id"]))
        assert ev["type"] == "item.run_requested"
    async with http_mcp(ch) as mcp:
        got = ok(await call(mcp, "slate_work", "next", item_id=item["id"]))
        assert got["item"]["id"] == item["id"]


def test_due_date_only_follows_how_the_due_date_was_given(client, alex, todo):
    item = _item(client, alex, todo["id"], "Flag", due_at="2026-11-03")
    assert item["due_date_only"] is True
    r = client.patch(
        f"{API}/items/{item['id']}", json={"due_at": "2026-11-03T17:00:00-06:00"}, headers=alex
    )
    assert r.json()["due_date_only"] is False  # same instant, now an explicit time
    events = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    flag = [e for e in events if e["field"] == "due_date_only"]
    assert flag and flag[-1]["after"] is False
    r = client.patch(f"{API}/items/{item['id']}", json={"due_at": None}, headers=alex)
    assert r.json()["due_date_only"] is False
    r = client.patch(f"{API}/items/{item['id']}", json={"due_at": "2026-11-04"}, headers=alex)
    assert r.json()["due_date_only"] is True
