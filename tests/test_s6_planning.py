"""Tests for slice s6: later, parked, project next_step, weekly sweep, prefs & digest."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from jsonschema import Draft202012Validator

from slate import dates
from slate.models import Account, Event, utcnow
from slate.service import attention, due, push, reminders
from test_mcp import call, http_mcp, ok

API = "/api/v1"


def _make_section_and_list(client, headers, name="Work", list_name="Tasks", audience="work"):
    sec = client.post(f"{API}/sections", headers=headers, json={"name": name, "audience": audience})
    assert sec.status_code == 201, sec.text
    lst = client.post(
        f"{API}/lists", headers=headers, json={"section_id": sec.json()["id"], "name": list_name}
    )
    assert lst.status_code == 201, lst.text
    return sec.json(), lst.json()


# --- Later ---------------------------------------------------------------------------------


def test_later_by_start_date_and_lead_days_and_zero_pref(client, alex):
    _, lst = _make_section_and_list(client, alex)
    now = utcnow()
    far_due = (now + timedelta(days=30)).isoformat()
    near_due = (now + timedelta(days=3)).isoformat()
    future_start = (now + timedelta(days=2)).isoformat()
    past_start = (now - timedelta(days=1)).isoformat()

    by_start = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Starts later",
            "start_at": future_start,
            "due_at": near_due,
        },
    ).json()
    by_lead = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Due far out", "due_at": far_due},
    ).json()
    started_far = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Started already",
            "start_at": past_start,
            "due_at": far_due,
        },
    ).json()
    near = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Due soon", "due_at": near_due},
    ).json()
    done_far = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Done far", "due_at": far_due, "status": "Done"},
    ).json()

    assert by_start["later"] is True
    assert by_lead["later"] is True
    assert started_far["later"] is False
    assert near["later"] is False
    assert done_far["later"] is False

    # Setting lead_days = 0 turns off the due-date lead rule, while future start_at stays later.
    r = client.patch(f"{API}/me/prefs", headers=alex, json={"lead_days": 0})
    assert r.status_code == 200, r.text
    items_by_id = {
        i["id"]: i
        for i in client.get(f"{API}/items", headers=alex, params={"list_id": lst["id"]}).json()
    }
    assert items_by_id[by_start["id"]]["later"] is True
    assert items_by_id[by_lead["id"]]["later"] is False
    assert items_by_id[near["id"]]["later"] is False

    # Later cards are excluded from /today (even if waiting on caller).
    client.patch(f"{API}/me/prefs", headers=alex, json={"lead_days": 14})
    client.patch(
        f"{API}/items/{by_lead['id']}",
        headers=alex,
        json={"waiting_on_account_id": "alex"},
    )
    t = client.get(f"{API}/today", headers=alex).json()
    today_ids = {i["id"] for g in ("overdue", "today", "waiting", "asked") for i in t[g]}
    assert by_lead["id"] not in today_ids
    assert by_start["id"] not in today_ids


def test_reference_list_uses_live_reference_for_later_and_sweep(client, alex):
    _, lst = _make_section_and_list(client, alex)
    now = utcnow()
    cards = []
    for days in (30, -20):
        response = client.post(
            f"{API}/items",
            headers=alex,
            json={
                "list_id": lst["id"],
                "title": "Read the draft",
                "assignees": ["alex"],
                "due_at": (now + timedelta(days=days)).isoformat(),
            },
        )
        response.raise_for_status()
        cards.append(response.json()["id"])
    listing = f"{API}/items?list_id={lst['id']}"
    assert any(row["later"] for row in client.get(listing, headers=alex).json())
    assert [row["id"] for row in client.get(f"{API}/today/sweep", headers=alex).json()] == [
        cards[1]
    ]
    client.patch(
        f"{API}/lists/{lst['id']}", headers=alex, json={"reference": True}
    ).raise_for_status()
    assert not any(row["later"] for row in client.get(listing, headers=alex).json())
    assert client.get(f"{API}/items/{cards[0]}", headers=alex).json()["later"] is False
    assert client.get(f"{API}/today/sweep", headers=alex).json() == []


@pytest.mark.anyio
async def test_core_item_update_accepts_parking_fields(client, alex):
    _, lst = _make_section_and_list(client, alex)
    item = client.post(
        f"{API}/items", headers=alex, json={"list_id": lst["id"], "title": "Draft"}
    ).json()
    parking = {
        "parked_until": (dates.local_today() + timedelta(days=1)).isoformat(),
        "parked_note": "Review from the publisher",
    }
    async with http_mcp(alex, tools=None) as mcp:
        tool = next(t for t in (await mcp.list_tools()).tools if t.name == "slate_item")
        args = {"action": "update", "params": {"id": item["id"], **parking}}
        Draft202012Validator(tool.input_schema).validate(args)
        updated = ok(await call(mcp, "slate_item", "update", **args["params"]))
    assert {field: updated[field] for field in parking} == parking


@pytest.mark.parametrize("defer", ["parked", "later"])
def test_deferred_questions_leave_the_digest(client, alex, make_account, defer):
    _, lst = _make_section_and_list(client, alex)
    _, agent = make_account("my-agent")
    card = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Draft", "assignees": ["my-agent"]},
    ).json()
    route = f"{API}/items/{card['id']}"
    client.post(
        f"{route}/report",
        headers=agent,
        json={"state": "blocked", "waiting_on": "alex", "question": "Send the draft?"},
    ).raise_for_status()
    questions = client.get(f"{API}/me/digest", headers=alex).json()["questions"]
    assert [q["id"] for q in questions] == [card["id"]]
    until = (dates.local_today() + timedelta(days=2)).isoformat()
    changes = (
        {"start_at": until}
        if defer == "later"
        else {
            "parked_until": until,
            "parked_note": "Review from the publisher",
        }
    )
    client.patch(route, headers=alex, json=changes).raise_for_status()
    assert client.get(f"{API}/me/digest", headers=alex).json()["questions"] == []


# --- Parked --------------------------------------------------------------------------------


def test_parking_suppresses_a_due_alert_already_waiting_for_release(client, alex, db):
    _, lst = _make_section_and_list(client, alex)
    attention.release(db, utcnow())
    route = f"{API}/inbox/alerts"
    cursor = client.get(route, headers=alex).headers["X-Slate-Cursor"]
    card = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Draft", "due_at": utcnow().isoformat()},
    ).json()
    assert due.emit_due_events(db, now=utcnow()) > 0
    assert client.get(f"{API}/inbox", headers=alex, params={"reason": "due"}).json()
    client.patch(
        f"{API}/items/{card['id']}",
        headers=alex,
        json={
            "parked_until": (dates.local_today() + timedelta(days=1)).isoformat(),
            "parked_note": "Review from the publisher",
        },
    ).raise_for_status()
    assert client.get(f"{API}/inbox", headers=alex, params={"reason": "due"}).json() == []
    attention.release(db, utcnow())
    alerts = client.get(route, headers=alex, params={"after": cursor}).json()
    assert not any(alert["reason"] == "due" for alert in alerts)


def test_parked_validation_and_clear(client, alex):
    _, lst = _make_section_and_list(client, alex)
    item = client.post(
        f"{API}/items", headers=alex, json={"list_id": lst["id"], "title": "Dispute"}
    ).json()
    tomorrow = (dates.local_today() + timedelta(days=1)).isoformat()
    today_str = dates.local_today().isoformat()
    yesterday = (dates.local_today() - timedelta(days=1)).isoformat()

    # Setting one without the other fails.
    assert (
        client.patch(
            f"{API}/items/{item['id']}", headers=alex, json={"parked_until": tomorrow}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/items/{item['id']}", headers=alex, json={"parked_note": "Bank"}
        ).status_code
        == 422
    )
    # Date must be in the future (today and past fail).
    assert (
        client.patch(
            f"{API}/items/{item['id']}",
            headers=alex,
            json={"parked_until": today_str, "parked_note": "Bank"},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/items/{item['id']}",
            headers=alex,
            json={"parked_until": yesterday, "parked_note": "Bank"},
        ).status_code
        == 422
    )
    # Note > 140 chars fails.
    assert (
        client.patch(
            f"{API}/items/{item['id']}",
            headers=alex,
            json={"parked_until": tomorrow, "parked_note": "x" * 141},
        ).status_code
        == 422
    )

    # Valid park succeeds and appears on item view.
    r = client.patch(
        f"{API}/items/{item['id']}",
        headers=alex,
        json={"parked_until": tomorrow, "parked_note": "Bank chargeback"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["parked_until"] == tomorrow
    assert r.json()["parked_note"] == "Bank chargeback"

    # Clearing both succeeds.
    r2 = client.patch(
        f"{API}/items/{item['id']}",
        headers=alex,
        json={"parked_until": None, "parked_note": None},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["parked_until"] is None
    assert r2.json()["parked_note"] is None


def test_parked_hides_from_today_digest_due_and_work_next_then_unparks_at_0600(
    client, alex, make_account, db
):
    _, lst = _make_section_and_list(client, alex)
    agent, agent_h = make_account("my-agent", ("write",), kind="agent")
    watcher, watcher_h = make_account("sam", ("write",), kind="person")

    past_due = (utcnow() - timedelta(hours=3)).isoformat()
    tomorrow = dates.local_today() + timedelta(days=1)
    tomorrow_str = tomorrow.isoformat()

    item = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Chargeback",
            "due_at": past_due,
            "assignees": ["alex", "my-agent"],
        },
    ).json()
    w = client.post(f"{API}/items/{item['id']}/watch", headers=alex, json={"account": "sam"})
    assert w.status_code == 200, w.text

    # Park the card.
    r = client.patch(
        f"{API}/items/{item['id']}",
        headers=alex,
        json={"parked_until": tomorrow_str, "parked_note": "card issuer"},
    )
    assert r.status_code == 200, r.text

    # Left out of Today.
    t = client.get(f"{API}/today", headers=alex).json()
    assert item["id"] not in {
        i["id"] for g in ("overdue", "today", "waiting", "asked") for i in t[g]
    }

    # Left out of digest.
    d = client.get(f"{API}/me/digest", headers=alex).json()
    assert item["id"] not in {i["id"] for i in d["overdue"] + d["today"]}

    # Left out of due alerts.
    assert due.emit_due_events(db, now=utcnow()) == 0

    # Agent's work.next skips the parked card.
    nxt = client.post(f"{API}/work/next", headers=agent_h, json={})
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["item"] is None

    # Before 06:00 workspace time on parked_until: still parked.
    before_six = datetime.combine(tomorrow, time(5, 59), tzinfo=dates.LOCAL).astimezone(UTC)
    assert due.unpark_due(db, now=before_six) == 0

    # At 06:00 workspace time: unparks, clears fields, records item.unparked,
    # reaches person assignees + watchers.
    attention.release(db, utcnow())
    cursor = client.get(f"{API}/inbox/alerts", headers=alex).headers["X-Slate-Cursor"]
    at_six = datetime.combine(tomorrow, time(6, 0), tzinfo=dates.LOCAL).astimezone(UTC)
    due.run_due_checks(db, at_six)

    refreshed = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert refreshed["parked_until"] is None
    assert refreshed["parked_note"] is None
    attention.release(db, utcnow())
    alerts = client.get(f"{API}/inbox/alerts", headers=alex, params={"after": cursor}).json()
    [alert] = [row for row in alerts if row["reason"] == "reminder"]
    assert alert["entry"]["event"]["type"] == "item.unparked"
    assert alert["loudness"] == 3

    # Check inbox entries for alex (person assignee) and sam (person watcher), not agent.
    alex_inbox = client.get(f"{API}/inbox", headers=alex, params={"reason": "reminder"}).json()
    sam_inbox = client.get(f"{API}/inbox", headers=watcher_h, params={"reason": "reminder"}).json()
    alex_unpark = next(
        (
            e
            for e in alex_inbox
            if e["event"]["type"] == "item.unparked" and e["event"]["item_id"] == item["id"]
        ),
        None,
    )
    assert alex_unpark is not None
    assert alex_unpark["item_title"] == "Back today: Chargeback (was waiting on card issuer)"
    assert any(
        e["event"]["type"] == "item.unparked" and e["event"]["item_id"] == item["id"]
        for e in sam_inbox
    )

    # Check push notification wording.
    ev_id = next(e["event"]["id"] for e in alex_inbox if e["event"]["type"] == "item.unparked")
    ev = db.get(Event, ev_id)
    note = push.notification_for(db, ev, "reminder")
    assert note["title"] == "Back today: Chargeback (was waiting on card issuer)"
    assert note["body"] == "Back today: Chargeback (was waiting on card issuer)"


@pytest.mark.parametrize("hold", ["parked", "later"])
def test_run_now_overrides_the_planning_hold(client, alex, todo, make_account, db, hold):
    _, agent_headers = make_account("my-agent", ("write",), kind="agent")
    start_at = (utcnow() + timedelta(days=3)).isoformat()
    planning = (
        {
            "parked_until": (dates.local_today() + timedelta(days=3)).isoformat(),
            "parked_note": "Draft",
        }
        if hold == "parked"
        else {"start_at": start_at}
    )
    created = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": todo["id"], "title": "Draft", "assignees": ["my-agent"]},
    )
    created.raise_for_status()
    item_id = created.json()["id"]
    planned = client.patch(f"{API}/items/{item_id}", headers=alex, json=planning)
    planned.raise_for_status()
    item = planned.json()
    assert client.post(f"{API}/work/next", headers=agent_headers, json={}).json()["item"] is None
    requested = client.post(f"{API}/items/{item_id}/run", headers=alex)
    assert requested.status_code == 202, requested.text
    updated = client.get(f"{API}/items/{item_id}", headers=alex).json()
    if hold == "parked":
        assert updated["parked_until"] is None and updated["parked_note"] is None
        events = db.query(Event).filter_by(item_id=item_id).order_by(Event.id).all()
        assert [(e.type, e.field) for e in events[-3:]] == [
            ("item.updated", "parked_until"),
            ("item.updated", "parked_note"),
            ("item.run_requested", "run_requested_at"),
        ]
    else:
        assert updated["start_at"] == item["start_at"] and updated["later"] is True
    picked_up = client.post(f"{API}/work/next", headers=agent_headers, json={})
    assert picked_up.status_code == 200, picked_up.text
    assert picked_up.json()["item"]["id"] == item_id


def test_retired_chat_renudge_preference_is_ignored(client, alex):
    result = client.patch(
        f"{API}/me/prefs",
        headers=alex,
        json={"chat_renudge_minutes": 30, "escalate_after_minutes": 75},
    )
    result.raise_for_status()
    for prefs in [result.json(), client.get(f"{API}/me", headers=alex).json()["prefs"]]:
        assert "chat_renudge_minutes" not in prefs
        assert prefs["escalate_after_minutes"] == 75


def test_unpark_falls_back_to_owner_when_no_person_assignee_or_watcher(
    client, alex, make_account, db
):
    _, lst = _make_section_and_list(client, alex)
    make_account("my-agent", ("write",), kind="agent")
    tomorrow = dates.local_today() + timedelta(days=1)

    item = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Agent parked", "assignees": ["my-agent"]},
    ).json()
    # Remove alex as watcher (created auto-watch).
    u = client.post(f"{API}/items/{item['id']}/unwatch", headers=alex)
    assert u.status_code == 200, u.text
    client.patch(
        f"{API}/items/{item['id']}",
        headers=alex,
        json={"parked_until": tomorrow.isoformat(), "parked_note": "vendor reply"},
    )
    at_six = datetime.combine(tomorrow, time(6, 0), tzinfo=dates.LOCAL).astimezone(UTC)
    assert due.unpark_due(db, now=at_six) == 1

    alex_inbox = client.get(f"{API}/inbox", headers=alex, params={"reason": "reminder"}).json()
    assert any(
        e["event"]["type"] == "item.unparked" and e["event"]["item_id"] == item["id"]
        for e in alex_inbox
    )


# --- Projects & next_step ------------------------------------------------------------------


def test_project_next_step_selection_exclusions_and_projects_ordering(client, alex):
    sec_a, lst_a = _make_section_and_list(client, alex, name="Alpha", list_name="Tasks")
    sec_b, lst_b = _make_section_and_list(client, alex, name="Beta", list_name="Tasks")
    sec_c, _ = _make_section_and_list(client, alex, name="Plain", list_name="Tasks")

    # Mark Alpha and Beta as projects.
    r = client.patch(f"{API}/sections/{sec_a['id']}", headers=alex, json={"project": True})
    assert r.status_code == 200 and r.json()["project"] is True
    r = client.patch(f"{API}/sections/{sec_b['id']}", headers=alex, json={"project": True})
    assert r.status_code == 200 and r.json()["project"] is True

    now = utcnow()
    d1 = (now + timedelta(days=1)).isoformat()
    d2 = (now + timedelta(days=2)).isoformat()
    d3 = (now + timedelta(days=3)).isoformat()
    far = (now + timedelta(days=30)).isoformat()
    tomorrow = (dates.local_today() + timedelta(days=1)).isoformat()

    # Excluded cards in Alpha (all earlier than the real winner at d3):
    # 1. Closed
    client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst_a["id"],
            "title": "Closed",
            "due_at": d1,
            "assignees": ["alex"],
            "status": "Done",
        },
    )
    # 2. Unassigned
    client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "Unassigned", "due_at": d1},
    )
    # 3. No due_at
    client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "No due", "assignees": ["alex"]},
    )
    # 4. Parked
    parked = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "Parked", "due_at": d1, "assignees": ["alex"]},
    ).json()
    client.patch(
        f"{API}/items/{parked['id']}",
        headers=alex,
        json={"parked_until": tomorrow, "parked_note": "waiting"},
    )
    # 5. Later (due far out)
    client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "Later", "due_at": far, "assignees": ["alex"]},
    )
    # 6. Blocked by another open card
    blocker = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "Blocker", "due_at": far, "assignees": ["alex"]},
    ).json()
    blocked = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "Blocked", "due_at": d1, "assignees": ["alex"]},
    ).json()
    client.post(
        f"{API}/items/{blocker['id']}/links",
        headers=alex,
        json={"kind": "blocks", "item_id": blocked["id"]},
    )

    # Two eligible cards at the same due_at (d2): higher priority wins.
    low = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst_a["id"],
            "title": "Low prio",
            "due_at": d2,
            "priority": "low",
            "assignees": ["alex"],
            "position": 1,
        },
    ).json()
    high = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst_a["id"],
            "title": "High prio",
            "due_at": d2,
            "priority": "high",
            "assignees": ["alex"],
            "position": 5,
        },
    ).json()
    # Later eligible card (d3)
    client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst_a["id"], "title": "Later step", "due_at": d3, "assignees": ["alex"]},
    )

    # GET /projects lists only project sections, with next_step=null first (Beta before Alpha).
    projects = client.get(f"{API}/projects", headers=alex).json()
    assert [p["id"] for p in projects] == [sec_b["id"], sec_a["id"]]
    assert projects[0]["next_step"] is None
    assert projects[1]["next_step"]["item_id"] == high["id"]
    assert projects[1]["next_step"]["title"] == "High prio"
    assert len(projects[1]["next_step"]["assignee_ids"]) == 1
    assert projects[1]["next_step"]["due_at"] is not None

    # GET /sections includes project and next_step; non-project Plain has next_step None.
    all_secs = {s["id"]: s for s in client.get(f"{API}/sections", headers=alex).json()}
    assert all_secs[sec_a["id"]]["next_step"]["item_id"] == high["id"]
    assert all_secs[sec_b["id"]]["next_step"] is None
    assert all_secs[sec_c["id"]]["project"] is False
    assert all_secs[sec_c["id"]]["next_step"] is None

    # Same due & priority tie-break: position wins.
    client.patch(f"{API}/items/{high['id']}", headers=alex, json={"status": "Done"})
    low2 = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst_a["id"],
            "title": "Low pos 0",
            "due_at": d2,
            "priority": "low",
            "assignees": ["alex"],
            "position": 0,
        },
    ).json()
    alpha = next(
        p for p in client.get(f"{API}/projects", headers=alex).json() if p["id"] == sec_a["id"]
    )
    assert alpha["next_step"]["item_id"] == low2["id"]
    assert low["id"] != low2["id"]


# --- Sweep & Digest ------------------------------------------------------------------------


def test_sweep_endpoint_and_digest_on_and_off_sweep_day(client, alex, db):
    sec, lst = _make_section_and_list(client, alex, name="Launch", list_name="Tasks")
    client.patch(f"{API}/sections/{sec['id']}", headers=alex, json={"project": True})

    # Fixed Friday (weekday 4) and Thursday (weekday 3) in workspace timezone.
    friday = datetime(2030, 5, 10, 9, 0, tzinfo=dates.LOCAL).astimezone(UTC)
    assert friday.astimezone(dates.LOCAL).weekday() == 4
    thursday = friday - timedelta(days=1)

    old_1 = (friday - timedelta(days=30)).isoformat()
    old_2 = (friday - timedelta(days=20)).isoformat()
    recent_overdue = (friday - timedelta(days=2)).isoformat()

    i_oldest = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Oldest stale",
            "due_at": old_1,
            "assignees": ["alex"],
        },
    ).json()
    i_older = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Older stale", "due_at": old_2, "assignees": ["alex"]},
    ).json()
    i_recent = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Recent overdue",
            "due_at": recent_overdue,
            "assignees": ["alex"],
        },
    ).json()

    # Stale cards do not trigger due alerts.
    assert due.emit_due_events(db, now=friday) > 0  # recent_overdue fires
    from slate.models import Event

    stale_due_events = (
        db.query(Event)
        .filter(
            Event.item_id.in_([i_oldest["id"], i_older["id"]]),
            Event.type.in_(["item.due_soon", "item.due", "item.overdue"]),
        )
        .all()
    )
    assert stale_due_events == []

    # GET /today/sweep returns stale cards oldest first on any day.
    # (Using real clock: create items relative to utcnow() to test REST /today/sweep.)
    now = utcnow()
    s1 = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Now stale 1",
            "due_at": (now - timedelta(days=25)).isoformat(),
            "assignees": ["alex"],
        },
    ).json()
    s2 = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Now stale 2",
            "due_at": (now - timedelta(days=16)).isoformat(),
            "assignees": ["alex"],
        },
    ).json()
    sweep_res = client.get(f"{API}/today/sweep", headers=alex)
    assert sweep_res.status_code == 200, sweep_res.text
    sweep_ids = [i["id"] for i in sweep_res.json()]
    assert sweep_ids == [s1["id"], s2["id"]]

    # Digest OFF sweep day (Thursday): stale cards are out of overdue and there is no `decide`.
    account = db.query(Account).filter(Account.handle == "alex").one()
    thu_digest = reminders.digest_for(db, account, now=thursday)
    thu_overdue_ids = {i["id"] for i in thu_digest["overdue"]}
    assert i_recent["id"] in thu_overdue_ids
    assert i_oldest["id"] not in thu_overdue_ids
    assert i_older["id"] not in thu_overdue_ids
    assert not thu_digest.get("decide")

    # Digest ON sweep day (Friday): `decide` lists stale cards oldest first, still out of `overdue`.
    fri_digest = reminders.digest_for(db, account, now=friday)
    fri_overdue_ids = {i["id"] for i in fri_digest["overdue"]}
    assert i_recent["id"] in fri_overdue_ids
    assert i_oldest["id"] not in fri_overdue_ids
    decide_ids = [i["id"] for i in fri_digest["decide"]]
    assert decide_ids == [s1["id"], s2["id"], i_oldest["id"], i_older["id"]]

    # Project with no next step appears in digest under projects_without_next_step
    # (and does not push).
    empty_sec, _ = _make_section_and_list(client, alex, name="Empty Project", list_name="Tasks")
    client.patch(f"{API}/sections/{empty_sec['id']}", headers=alex, json={"project": True})
    fri_digest2 = reminders.digest_for(db, account, now=friday)
    missing_names = [p["name"] for p in fri_digest2["projects_without_next_step"]]
    assert "Empty Project" in missing_names


def test_prefs_lead_days_and_sweep_weekday_validation(client, alex):
    assert client.patch(f"{API}/me/prefs", headers=alex, json={"lead_days": 7}).status_code == 200
    assert client.patch(f"{API}/me/prefs", headers=alex, json={"lead_days": 0}).status_code == 200
    assert client.patch(f"{API}/me/prefs", headers=alex, json={"lead_days": -1}).status_code == 422
    assert (
        client.patch(f"{API}/me/prefs", headers=alex, json={"lead_days": True}).status_code == 422
    )

    assert (
        client.patch(f"{API}/me/prefs", headers=alex, json={"sweep_weekday": 0}).status_code == 200
    )
    assert (
        client.patch(f"{API}/me/prefs", headers=alex, json={"sweep_weekday": 6}).status_code == 200
    )
    assert (
        client.patch(f"{API}/me/prefs", headers=alex, json={"sweep_weekday": 7}).status_code == 422
    )
    assert (
        client.patch(f"{API}/me/prefs", headers=alex, json={"sweep_weekday": -1}).status_code == 422
    )
    assert (
        client.patch(f"{API}/me/prefs", headers=alex, json={"sweep_weekday": False}).status_code
        == 422
    )


# --- Correction round 1 regression tests ---------------------------------------------------


def test_later_recomputes_on_cached_item_when_clock_advances(client, alex, monkeypatch):
    from slate.service import item_cache, items

    _, lst = _make_section_and_list(client, alex)
    t0 = utcnow()
    monkeypatch.setattr(items, "utcnow", lambda: t0)
    monkeypatch.setattr(item_cache, "utcnow", lambda: t0, raising=False)

    # Due 15 days from t0 -> later=True under default lead_days=14.
    card = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Enters window tomorrow",
            "due_at": (t0 + timedelta(days=15)).isoformat(),
        },
    ).json()
    first = client.get(f"{API}/items", headers=alex, params={"list_id": lst["id"]}).json()
    assert first[0]["id"] == card["id"] and first[0]["later"] is True

    # Advance the clock by 2 days with NO item event in between: cache hit must say later=False.
    t1 = t0 + timedelta(days=2)
    monkeypatch.setattr(items, "utcnow", lambda: t1)
    monkeypatch.setattr(item_cache, "utcnow", lambda: t1, raising=False)
    second = client.get(f"{API}/items", headers=alex, params={"list_id": lst["id"]}).json()
    assert second[0]["id"] == card["id"] and second[0]["later"] is False


def test_unpark_rechecks_under_write_lock_after_concurrent_repark(client, alex, db, monkeypatch):
    from conftest import raw_write

    _, lst = _make_section_and_list(client, alex)
    tomorrow = dates.local_today() + timedelta(days=1)
    next_week = (dates.local_today() + timedelta(days=7)).isoformat()
    card = client.post(
        f"{API}/items", headers=alex, json={"list_id": lst["id"], "title": "Reparked"}
    ).json()
    client.patch(
        f"{API}/items/{card['id']}",
        headers=alex,
        json={"parked_until": tomorrow.isoformat(), "parked_note": "first wait"},
    )

    # Simulate another request committing a later parked_until right after the outer select.
    real_actor = due.system_actor
    did_update = False

    def racing_actor(session):
        nonlocal did_update
        if not did_update:
            did_update = True
            raw_write(
                "UPDATE items SET parked_until = :u, parked_note = :n WHERE id = :id",
                {"u": next_week, "n": "extended wait", "id": card["id"]},
            )
        return real_actor(session)

    monkeypatch.setattr(due, "system_actor", racing_actor)
    at_six = datetime.combine(tomorrow, time(6, 0), tzinfo=dates.LOCAL).astimezone(UTC)
    assert due.unpark_due(db, now=at_six) == 0

    refreshed = client.get(f"{API}/items/{card['id']}", headers=alex).json()
    assert refreshed["parked_until"] == next_week
    assert refreshed["parked_note"] == "extended wait"


def test_stale_only_suppresses_due_alerts_for_person_assigned_and_never_run_requested(
    client, alex, make_account, db
):
    _, lst = _make_section_and_list(client, alex)
    make_account("my-agent", ("write",), kind="agent")
    now = utcnow()
    old_due = (now - timedelta(days=15)).isoformat()

    unassigned = client.post(
        f"{API}/items",
        headers=alex,
        json={"list_id": lst["id"], "title": "Unassigned old", "due_at": old_due},
    ).json()
    agent_only = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Agent-only old",
            "due_at": old_due,
            "assignees": ["my-agent"],
        },
    ).json()
    both = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": lst["id"],
            "title": "Person + agent old",
            "due_at": old_due,
            "assignees": ["alex", "my-agent"],
        },
    ).json()

    assert due.emit_due_events(db, now=now) > 0

    def ev_types(item_id):
        return {
            e.type
            for e in db.query(Event)
            .filter(
                Event.item_id == item_id,
                Event.type.in_(["item.due_soon", "item.due", "item.overdue", "item.run_requested"]),
            )
            .all()
        }

    # Unassigned card is not stale -> still gets overdue.
    assert "item.overdue" in ev_types(unassigned["id"])
    # Agent-only card is not stale -> still gets run_requested.
    assert "item.run_requested" in ev_types(agent_only["id"])
    # Person + agent card: due/overdue suppressed, but run_requested still fires.
    both_types = ev_types(both["id"])
    assert "item.run_requested" in both_types
    assert "item.overdue" not in both_types
    assert "item.due" not in both_types


def test_closing_card_clears_parked_fields(client, alex, db):
    _, lst = _make_section_and_list(client, alex)
    statuses = [*lst["statuses"], {"name": "Dropped", "kind": "cancelled"}]
    client.put(f"{API}/lists/{lst['id']}/statuses", headers=alex, json={"statuses": statuses})

    tomorrow = dates.local_today() + timedelta(days=1)
    done_card = client.post(
        f"{API}/items", headers=alex, json={"list_id": lst["id"], "title": "Will finish"}
    ).json()
    cancel_card = client.post(
        f"{API}/items", headers=alex, json={"list_id": lst["id"], "title": "Will cancel"}
    ).json()
    for cid in (done_card["id"], cancel_card["id"]):
        client.patch(
            f"{API}/items/{cid}",
            headers=alex,
            json={"parked_until": tomorrow.isoformat(), "parked_note": "waiting"},
        )

    r1 = client.patch(f"{API}/items/{done_card['id']}", headers=alex, json={"status": "Done"})
    assert r1.status_code == 200
    assert r1.json()["parked_until"] is None and r1.json()["parked_note"] is None

    r2 = client.patch(f"{API}/items/{cancel_card['id']}", headers=alex, json={"status": "Dropped"})
    assert r2.status_code == 200
    assert r2.json()["parked_until"] is None and r2.json()["parked_note"] is None

    at_six = datetime.combine(tomorrow, time(6, 0), tzinfo=dates.LOCAL).astimezone(UTC)
    assert due.unpark_due(db, now=at_six) == 0


def test_next_step_for_restricted_caller_does_not_substitute_later_visible_card(client, alex):
    sec, private_list = _make_section_and_list(
        client, alex, name="Secret Project", list_name="Private"
    )
    shared_list = client.post(
        f"{API}/lists", headers=alex, json={"section_id": sec["id"], "name": "Shared"}
    ).json()
    client.patch(
        f"{API}/sections/{sec['id']}", headers=alex, json={"project": True, "private": True}
    )

    guest_acct = client.post(
        f"{API}/accounts",
        headers=alex,
        json={"handle": "guest1", "display_name": "Guest", "kind": "person", "role": "guest"},
    ).json()
    guest_tok = client.post(
        f"{API}/accounts/{guest_acct['id']}/tokens",
        headers=alex,
        json={"scopes": ["read"], "label": "guest"},
    ).json()["token"]
    guest_h = {"Authorization": f"Bearer {guest_tok}"}
    client.put(
        f"{API}/shares",
        headers=alex,
        json={
            "account_id": guest_acct["id"],
            "scope_kind": "list",
            "scope_id": shared_list["id"],
            "level": "view",
        },
    )

    now = utcnow()
    # Earlier real next step is on Private (invisible to guest).
    early = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": private_list["id"],
            "title": "Real next step",
            "due_at": (now + timedelta(days=1)).isoformat(),
            "assignees": ["alex"],
        },
    ).json()
    # Later card is on Shared (visible to guest).
    client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": shared_list["id"],
            "title": "Later visible step",
            "due_at": (now + timedelta(days=3)).isoformat(),
            "assignees": ["alex"],
        },
    )

    owner_proj = client.get(f"{API}/projects", headers=alex).json()[0]
    assert owner_proj["next_step"]["item_id"] == early["id"]

    # Guest must NOT be shown the later visible card as the project's next step.
    guest_proj = client.get(f"{API}/projects", headers=guest_h).json()[0]
    assert guest_proj["next_step"] is None
