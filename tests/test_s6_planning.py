"""Tests for slice s6: later, parked, project next_step, weekly sweep, prefs & digest."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from slate import dates
from slate.models import Account, Event, utcnow
from slate.service import due, push, reminders

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


# --- Parked --------------------------------------------------------------------------------


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
    at_six = datetime.combine(tomorrow, time(6, 0), tzinfo=dates.LOCAL).astimezone(UTC)
    assert due.unpark_due(db, now=at_six) == 1

    refreshed = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert refreshed["parked_until"] is None
    assert refreshed["parked_note"] is None

    # Check inbox entries for alex (person assignee) and sam (person watcher), not agent.
    alex_inbox = client.get(f"{API}/inbox", headers=alex, params={"reason": "reminder"}).json()
    sam_inbox = client.get(f"{API}/inbox", headers=watcher_h, params={"reason": "reminder"}).json()
    assert any(
        e["event"]["type"] == "item.unparked" and e["event"]["item_id"] == item["id"]
        for e in alex_inbox
    )
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
