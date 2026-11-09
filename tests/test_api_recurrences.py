"""/api/v1/recurrences."""

from __future__ import annotations

from conftest import API

ROUTES = {
    ("get", "/api/v1/recurrences"),
    ("post", "/api/v1/recurrences"),
    ("get", "/api/v1/recurrences/{rule_id}"),
    ("patch", "/api/v1/recurrences/{rule_id}"),
    ("post", "/api/v1/recurrences/{rule_id}/pause"),
    ("post", "/api/v1/recurrences/{rule_id}/resume"),
    ("post", "/api/v1/recurrences/{rule_id}/run-now"),
    ("get", "/api/v1/recurrences/preview"),
    ("post", "/api/v1/recurrences/{rule_id}/adopt"),
    ("get", "/api/v1/recurrences/upcoming"),
}

BULK_TRASH = {
    "title_template": "Bulk trash {date}",
    "rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE",
    "dtstart": "2030-01-02",
    "tags": ["home"],
}


def _create(client, h, todo, **fields):
    r = client.post(f"{API}/recurrences", json={"list_id": todo["id"], **fields}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_openapi_lists_every_recurrence_route(client):
    paths = client.get("/openapi.json").json()["paths"]
    present = {(verb, path) for path, ops in paths.items() for verb in ops}
    assert present >= ROUTES, sorted(ROUTES - present)


def test_create_returns_the_rule_with_next_occurrences(client, alex, todo):
    rule = _create(client, alex, todo, **BULK_TRASH)
    assert rule["rrule"] == "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE"
    assert rule["description"] == "every 2 weeks on Wednesday"
    assert rule["timezone"] == "America/Chicago"
    assert rule["lead_days"] == 14
    assert rule["missed_policy"] == "spawn_always"
    assert rule["tags"] == ["home"]
    assert rule["next_run_at"] == "2030-01-02T06:00:00Z"
    nxt = rule["next_occurrences"]
    assert [n["local"] for n in nxt[:2]] == [
        "2030-01-02T00:00:00-06:00",
        "2030-01-16T00:00:00-06:00",
    ]
    assert nxt[0]["utc"] == "2030-01-02T06:00:00Z"
    assert len(nxt) == 5


def test_create_refuses_a_bad_rrule_in_plain_words(client, alex, todo):
    r = client.post(
        f"{API}/recurrences",
        json={"list_id": todo["id"], "title_template": "x", "rrule": "EVERY=TUESDAY"},
        headers=alex,
    )
    assert r.status_code == 422
    assert "FREQ" in r.json()["detail"]


def test_list_and_filter(client, alex, todo):
    a = _create(client, alex, todo, **BULK_TRASH)
    _create(client, alex, todo, title_template="Daily", rrule="FREQ=DAILY")
    r = client.get(f"{API}/recurrences", headers=alex)
    assert r.status_code == 200
    assert len(r.json()) == 2
    client.patch(f"{API}/recurrences/{a['id']}", json={"ended": True}, headers=alex)
    assert len(client.get(f"{API}/recurrences", headers=alex).json()) == 1
    everything = client.get(f"{API}/recurrences", params={"include_ended": True}, headers=alex)
    assert len(everything.json()) == 2
    assert client.get(f"{API}/recurrences", params={"list_id": "nope"}, headers=alex).json() == []


def test_patch_pause_resume(client, alex, todo):
    rule = _create(client, alex, todo, **BULK_TRASH)
    url = f"{API}/recurrences/{rule['id']}"
    r = client.patch(url, json={"lead_days": 3, "priority": "high"}, headers=alex)
    assert r.status_code == 200, r.text
    assert (r.json()["lead_days"], r.json()["priority"]) == (3, "high")
    paused = client.post(f"{url}/pause", headers=alex).json()
    assert paused["paused_at"] is not None
    assert client.post(f"{url}/run-now", headers=alex).status_code == 422
    resumed = client.post(f"{url}/resume", headers=alex).json()
    assert resumed["paused_at"] is None
    ended = client.patch(url, json={"ended": True}, headers=alex).json()
    assert ended["ended_at"] is not None and ended["next_occurrences"] == []
    assert client.patch(url, json={"ended": False}, headers=alex).status_code == 422
    assert client.patch(url, json={"bogus": 1}, headers=alex).status_code == 422
    assert client.patch(f"{API}/recurrences/missing", json={}, headers=alex).status_code == 404


def test_run_now_spawns_and_returns_the_items(client, alex, todo):
    rule = _create(
        client, alex, todo, title_template="Daily {iso_date}", rrule="FREQ=DAILY", lead_days=2
    )
    r = client.post(f"{API}/recurrences/{rule['id']}/run-now", headers=alex)
    assert r.status_code == 200, r.text
    spawned = r.json()["spawned"]
    assert len(spawned) == 3  # today, tomorrow, the day after
    assert all(i["recurrence_id"] == rule["id"] for i in spawned)
    again = client.post(f"{API}/recurrences/{rule['id']}/run-now", headers=alex).json()
    assert again["spawned"] == []
    items = client.get(f"{API}/items", params={"list_id": todo["id"]}, headers=alex).json()
    assert len(items) == 3


def test_scopes(client, alex, todo, make_account):
    _, reader = make_account("reader", scopes=("read",))
    rule = _create(client, alex, todo, **BULK_TRASH)
    assert client.get(f"{API}/recurrences", headers=reader).status_code == 200
    assert client.get(f"{API}/recurrences").status_code == 401
    for path in ("pause", "resume", "run-now"):
        assert (
            client.post(f"{API}/recurrences/{rule['id']}/{path}", headers=reader).status_code == 403
        )
    r = client.post(
        f"{API}/recurrences", json={"list_id": todo["id"], **BULK_TRASH}, headers=reader
    )
    assert r.status_code == 403


def test_get_one_rule(client, alex, todo):
    rule = _create(client, alex, todo, **BULK_TRASH)
    r = client.get(f"{API}/recurrences/{rule['id']}", headers=alex)
    assert r.status_code == 200 and r.json() == rule
    assert client.get(f"{API}/recurrences/nope", headers=alex).status_code == 404


# --- Preview (the recurrence editor's live preview) ----------------------------------------


def test_preview_returns_dates_and_plain_english(client, alex):
    r = client.get(
        f"{API}/recurrences/preview",
        params={"rrule": "FREQ=MONTHLY;BYDAY=2WE,4WE", "dtstart": "2030-01-01"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "every month on the 2nd and 4th Wednesday"
    assert body["rrule"] == "FREQ=MONTHLY;BYDAY=2WE,4WE"
    assert body["timezone"] == "America/Chicago"
    assert [o["local"][:10] for o in body["occurrences"]] == [
        "2030-01-09",
        "2030-01-23",
        "2030-02-13",
        "2030-02-27",
        "2030-03-13",
    ]
    assert body["occurrences"][0]["due_at"] == "2030-01-09T23:00:00Z"  # 17:00 CST


def test_preview_count_timezone_and_last_day(client, alex):
    r = client.get(
        f"{API}/recurrences/preview",
        params={
            "rrule": "FREQ=MONTHLY;BYMONTHDAY=-1",
            "dtstart": "2030-01-01",
            "timezone": "Europe/London",
            "count": 3,
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "every month on the last day"
    assert [o["local"][:10] for o in body["occurrences"]] == [
        "2030-01-31",
        "2030-02-28",
        "2030-03-31",
    ]


def test_preview_refuses_bad_rules_in_plain_words(client, alex):
    url = f"{API}/recurrences/preview"
    r = client.get(url, params={"rrule": "EVERY=TUESDAY"}, headers=alex)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str) and "FREQ" in r.json()["detail"]
    r = client.get(url, params={"rrule": "FREQ=HOURLY"}, headers=alex)
    assert r.status_code == 422
    r = client.get(url, params={"rrule": "FREQ=DAILY", "timezone": "Mars/Base"}, headers=alex)
    assert r.status_code == 422
    r = client.get(url, params={"rrule": "FREQ=DAILY", "count": 0}, headers=alex)
    assert r.status_code == 422
    assert client.get(url, params={"rrule": "FREQ=DAILY"}).status_code == 401


def test_preview_of_an_exhausted_rule_is_empty(client, alex):
    r = client.get(
        f"{API}/recurrences/preview",
        params={"rrule": "FREQ=DAILY;COUNT=2", "dtstart": "2020-01-01"},
        headers=alex,
    )
    assert r.status_code == 200
    assert r.json()["occurrences"] == []
    assert r.json()["description"] == "every day, 2 times"


# --- Adopting an existing item as the current occurrence -----------------------------------


def _item(client, h, todo, **fields):
    r = client.post(f"{API}/items", json={"list_id": todo["id"], **fields}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_from_an_item_keeps_it_as_the_current_occurrence(client, alex, todo):
    item = _item(client, alex, todo, title="Bulk trash", due_at="2030-01-09")  # a Wednesday
    rule = _create(
        client,
        alex,
        todo,
        title_template="Bulk trash",
        rrule="FREQ=WEEKLY;BYDAY=WE",
        adopt_item_id=item["id"],
    )
    assert rule["dtstart"] == "2030-01-09T06:00:00Z"  # the item's day, midnight CST
    assert rule["next_run_at"] == "2030-01-16T06:00:00Z"  # the one after the item
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["recurrence_id"] == rule["id"]
    assert got["occurrence_at"] == "2030-01-09T06:00:00Z"
    assert got["due_at"] == "2030-01-09T23:00:00Z"  # unchanged


def test_adopting_an_item_due_off_the_rule_keeps_its_due_date(client, alex, todo):
    item = _item(client, alex, todo, title="Trash", due_at="2030-01-08")  # a Tuesday
    rule = _create(client, alex, todo, title_template="Trash", rrule="FREQ=WEEKLY;BYDAY=WE")
    r = client.post(
        f"{API}/recurrences/{rule['id']}/adopt", json={"item_id": item["id"]}, headers=alex
    )
    assert r.status_code == 200, r.text
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["recurrence_id"] == rule["id"]
    assert got["occurrence_at"] == "2030-01-08T23:00:00Z"  # its own due instant
    assert got["due_at"] == "2030-01-08T23:00:00Z"


def test_adopting_an_undated_item_gives_it_the_next_due_date(client, alex, todo):
    item = _item(client, alex, todo, title="Water plants")
    rule = _create(
        client,
        alex,
        todo,
        title_template="Water plants",
        rrule="FREQ=DAILY",
        dtstart="2030-02-01",
        adopt_item_id=item["id"],
    )
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["recurrence_id"] == rule["id"]
    assert got["due_at"] == "2030-02-01T23:00:00Z"
    assert got["occurrence_at"] == "2030-02-01T06:00:00Z"
    assert rule["next_run_at"] == "2030-02-02T06:00:00Z"


def test_adopt_refusals(client, alex, todo):
    item = _item(client, alex, todo, title="Trash", due_at="2030-01-09")
    one = _create(client, alex, todo, title_template="A", rrule="FREQ=DAILY", dtstart="2030-01-01")
    two = _create(client, alex, todo, title_template="B", rrule="FREQ=DAILY", dtstart="2030-01-01")
    url = f"{API}/recurrences/{one['id']}/adopt"
    assert client.post(url, json={"item_id": item["id"]}, headers=alex).status_code == 200
    # Again: no change.
    assert client.post(url, json={"item_id": item["id"]}, headers=alex).status_code == 200
    r = client.post(
        f"{API}/recurrences/{two['id']}/adopt", json={"item_id": item["id"]}, headers=alex
    )
    assert r.status_code == 422 and "already" in r.json()["detail"]
    assert client.post(url, json={"item_id": "nope"}, headers=alex).status_code == 404
    client.patch(f"{API}/recurrences/{two['id']}", json={"ended": True}, headers=alex)
    other = _item(client, alex, todo, title="Other")
    r = client.post(
        f"{API}/recurrences/{two['id']}/adopt", json={"item_id": other["id"]}, headers=alex
    )
    assert r.status_code == 422


def test_options_round_trip(client, alex, todo):
    rule = _create(
        client,
        alex,
        todo,
        title_template="Filter",
        rrule="FREQ=MONTHLY",
        trigger="on_done",
        reopen=True,
        skip_weekends=True,
    )
    assert (rule["trigger"], rule["reopen"], rule["skip_weekends"]) == ("on_done", True, True)
    assert rule["copy_checklists"] is True
    assert rule["next_occurrences"] == []
    assert "counted from when each is done" in rule["description"]
    r = client.patch(f"{API}/recurrences/{rule['id']}", json={"trigger": "schedule"}, headers=alex)
    assert r.status_code == 422
    assert "on_done" in r.json()["detail"]


def test_upcoming_and_preview_options(client, alex, todo):
    rule = _create(client, alex, todo, **BULK_TRASH)
    r = client.get(
        f"{API}/recurrences/upcoming",
        params={"start": "2030-01-01", "end": "2030-01-31"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert [u["local_date"] for u in r.json()] == ["2030-01-02", "2030-01-16", "2030-01-30"]
    assert r.json()[0]["recurrence_id"] == rule["id"]
    r = client.get(
        f"{API}/recurrences/preview",
        params={"rrule": "FREQ=WEEKLY;BYDAY=SA", "dtstart": "2030-01-01", "skip_weekends": "true"},
        headers=alex,
    )
    assert r.json()["occurrences"][0]["local"].startswith("2030-01-07")
