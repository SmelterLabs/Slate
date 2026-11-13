"""Items, comments, filters, stale and today over /api/v1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time

from conftest import API, raw_write


def _item(client, h, list_id, title="Thing", **fields):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **fields}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _ids(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [i["id"] for i in resp.json()]


def _query(client, h, **params) -> list[str]:
    return _ids(client.get(f"{API}/items", params=params, headers=h))


# --- create: exactly one item ---------------------------------------------------------------


def test_create_makes_exactly_one_item_and_no_children(client, alex, todo):
    item = _item(
        client, alex, todo["id"], body_md="- step one\n- step two\n- step three", priority="high"
    )
    assert item["parent_id"] is None
    assert item["subtask_count"] == 0
    assert item["status"]["name"] == "To do"  # the list's default
    assert item["created_by"] == client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    assert _query(client, alex, list_id=todo["id"]) == [item["id"]]
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["subtasks"] == []


def test_create_needs_list_and_title(client, alex, todo):
    assert client.post(f"{API}/items", json={"title": "x"}, headers=alex).status_code == 422
    r = client.post(f"{API}/items", json={"list_id": todo["id"], "title": "  "}, headers=alex)
    assert r.status_code == 422
    r = client.post(f"{API}/items", json={"list_id": "nope", "title": "x"}, headers=alex)
    assert r.status_code == 404
    r = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "x", "status": "nope"}, headers=alex
    )
    assert r.status_code == 422


def test_create_with_parent_makes_one_subtask(client, alex, todo):
    parent = _item(client, alex, todo["id"])
    child = client.post(
        f"{API}/items", json={"parent_id": parent["id"], "title": "child"}, headers=alex
    ).json()
    assert child["parent_id"] == parent["id"] and child["list_id"] == todo["id"]
    detail = client.get(f"{API}/items/{parent['id']}", headers=alex).json()
    assert [s["id"] for s in detail["subtasks"]] == [child["id"]]
    assert detail["subtask_count"] == 1


def test_subtasks_nest_four_deep(client, alex, todo, db):
    """A chain of a top task and four subtasks; a fifth level is refused by the service and,
    under it, by the `items_depth_insert` trigger. Each level lists only its own subtasks."""
    chain = [_item(client, alex, todo["id"])]
    for n in range(4):
        chain.append(_item(client, alex, todo["id"], parent_id=chain[-1]["id"], title=f"L{n + 1}"))
    assert [c["parent_id"] for c in chain[1:]] == [c["id"] for c in chain[:-1]]
    r = client.post(
        f"{API}/items", json={"parent_id": chain[4]["id"], "title": "too deep"}, headers=alex
    )
    assert r.status_code == 422
    assert "depth exceeds 4" in r.json()["detail"]
    with pytest.raises(Exception, match="item depth exceeds 4"):
        # Straight SQL, under the service layer: the database holds the rule on its own.
        raw_write(
            "INSERT INTO items (id, list_id, parent_id, kind, title, body_md, due_date_only,"
            " position, created_by, created_at, updated_at) VALUES ('deep12345678', :l, :p,"
            " 'task', 'too deep', '', 0, 9, :a, '2026-01-01T00:00:00Z',"
            " '2026-01-01T00:00:00Z')",
            {"l": todo["id"], "p": chain[4]["id"], "a": chain[0]["created_by"]},
        )
    detail = client.get(f"{API}/items/{chain[1]['id']}", headers=alex).json()
    assert [s["id"] for s in detail["subtasks"]] == [chain[2]["id"]]
    assert detail["subtasks"][0]["subtask_count"] == 1
    r = client.post(f"{API}/items", json={"parent_id": "nope", "title": "x"}, headers=alex)
    assert r.status_code == 422


# --- split ----------------------------------------------------------------------------------


def test_split_creates_children_in_one_call(client, alex, todo):
    parent = _item(client, alex, todo["id"])
    r = client.post(
        f"{API}/items/{parent['id']}/split",
        json={"children": [{"title": "a"}, {"title": "b", "priority": "low"}, {"title": "c"}]},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    kids = r.json()
    assert [k["title"] for k in kids] == ["a", "b", "c"]
    assert {k["parent_id"] for k in kids} == {parent["id"]}
    detail = client.get(f"{API}/items/{parent['id']}", headers=alex).json()
    assert [s["title"] for s in detail["subtasks"]] == ["a", "b", "c"]
    split_events = [e for e in detail["events"] if e["type"] == "item.split"]
    assert split_events[0]["after"] == [k["id"] for k in kids]


def test_split_a_subtask_four_levels_down_is_422(client, alex, todo):
    parent = _item(client, alex, todo["id"])
    child = _item(client, alex, todo["id"], parent_id=parent["id"])
    r = client.post(
        f"{API}/items/{child['id']}/split", json={"children": [{"title": "a"}]}, headers=alex
    )
    assert r.status_code == 201, r.text  # a subtask can be split: subtasks nest four deep
    deep = r.json()[0]
    deeper = client.post(
        f"{API}/items/{deep['id']}/split", json={"children": [{"title": "b"}]}, headers=alex
    ).json()[0]
    deepest = client.post(
        f"{API}/items/{deeper['id']}/split", json={"children": [{"title": "c"}]}, headers=alex
    ).json()[0]  # depth 4: the last level allowed
    r = client.post(
        f"{API}/items/{deepest['id']}/split", json={"children": [{"title": "d"}]}, headers=alex
    )
    assert r.status_code == 422 and "4 deep" in r.json()["detail"]
    assert (
        client.post(
            f"{API}/items/nope/split", json={"children": [{"title": "a"}]}, headers=alex
        ).status_code
        == 404
    )


# --- read -----------------------------------------------------------------------------------


def test_get_item_has_everything(client, alex, todo):
    item = _item(client, alex, todo["id"], tags=["h3"], assignees=["alex"])
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=alex)
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["tags"] == ["h3"]
    assert [a["handle"] for a in detail["assignees"]] == ["alex"]
    assert [c["body_md"] for c in detail["comments"]] == ["hi"]
    assert [e["type"] for e in detail["events"]][:1] == ["item.created"]
    assert client.get(f"{API}/items/nope", headers=alex).status_code == 404


def test_detail_shows_only_the_last_20_events(client, alex, todo):
    item = _item(client, alex, todo["id"])
    for n in range(25):
        client.patch(f"{API}/items/{item['id']}", json={"title": f"t{n}"}, headers=alex)
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert len(detail["events"]) == 20
    assert detail["events"][-1]["after"] == "t24"
    assert (
        len(client.get(f"{API}/items/{item['id']}/events", headers=alex).json()) == 27
    )  # + the creator's auto-watch


# --- update ---------------------------------------------------------------------------------


def test_an_item_is_always_a_task(client, alex, todo, db):
    """Docs are their own records; an item of kind `doc` is refused on every way in."""
    from slate.models import Source
    from slate.service import items as items_svc
    from slate.service.core import Actor
    from slate.service.errors import Invalid

    r = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Spec", "kind": "doc"}, headers=alex
    )
    assert r.status_code == 422, r.text
    item = _item(client, alex, todo["id"])
    r = client.patch(f"{API}/items/{item['id']}", json={"kind": "doc"}, headers=alex)
    assert r.status_code == 422, r.text
    assert client.get(f"{API}/items", params={"kind": "doc"}, headers=alex).status_code == 422
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    with pytest.raises(Invalid):
        items_svc.update_item(db, Actor(me["id"], Source.api), item["id"], {"kind": "doc"})
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["kind"] == "task"


def test_patch_fields_and_events_carry_actor_and_session_ref(client, alex, todo):
    item = _item(client, alex, todo["id"])
    r = client.patch(
        f"{API}/items/{item['id']}",
        json={"title": "New", "priority": "urgent", "acceptance_md": "ships", "kind": "task"},
        headers={**alex, "X-Slate-Session-Ref": "run-42"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["title"], body["priority"], body["acceptance_md"], body["kind"]) == (
        "New",
        "urgent",
        "ships",
        "task",
    )
    events = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    updated = [e for e in events if e["type"] == "item.updated"]
    assert {e["field"] for e in updated} == {"title", "priority", "acceptance_md"}
    assert {(e["source"], e["session_ref"]) for e in updated} == {("api", "run-42")}


def test_patch_null_clears_and_unknown_field_is_422(client, alex, todo):
    item = _item(client, alex, todo["id"], priority="high", due_at="2026-11-01")
    r = client.patch(
        f"{API}/items/{item['id']}", json={"priority": None, "due_at": None}, headers=alex
    )
    assert r.json()["priority"] is None and r.json()["due_at"] is None
    r = client.patch(f"{API}/items/{item['id']}", json={"colour": "red"}, headers=alex)
    assert r.status_code == 422
    assert client.patch(f"{API}/items/nope", json={"title": "x"}, headers=alex).status_code == 404


def test_body_edit_writes_a_version(client, alex, todo):
    item = _item(client, alex, todo["id"], body_md="v1")
    client.patch(
        f"{API}/items/{item['id']}", json={"body_md": "v2", "body_note": "tightened"}, headers=alex
    )
    client.patch(f"{API}/items/{item['id']}", json={"body_md": "v2"}, headers=alex)  # no change
    versions = client.get(f"{API}/items/{item['id']}/versions", headers=alex).json()
    assert [(v["version_no"], v["body_md"], v["note"]) for v in versions] == [
        (1, "v1", None),
        (2, "v2", "tightened"),
    ]
    body_events = [
        e
        for e in client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
        if e["field"] == "body_md"
    ]
    assert [(e["before"], e["after"]) for e in body_events] == [("v1", "v2")]


def test_done_sets_completed_at_and_leaving_done_clears_it(client, alex, todo):
    item = _item(client, alex, todo["id"])
    assert item["completed_at"] is None
    done = client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=alex).json()
    assert done["status"]["kind"] == "done" and done["completed_at"] is not None
    active_id = todo["statuses"][1]["id"]
    back = client.patch(
        f"{API}/items/{item['id']}", json={"status": active_id}, headers=alex
    ).json()
    assert back["status"]["name"] == "In progress" and back["completed_at"] is None
    types = [e["type"] for e in client.get(f"{API}/items/{item['id']}/events", headers=alex).json()]
    assert types.count("item.status_changed") == 2
    r = client.patch(f"{API}/items/{item['id']}", json={"status": "nope"}, headers=alex)
    assert r.status_code == 422


def test_assignees_replace_the_set_and_name_the_diff(client, alex, todo, make_account):
    make_account("agent")
    make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["alex", "agent"])
    r = client.patch(
        f"{API}/items/{item['id']}", json={"assignees": ["agent", "agent2"]}, headers=alex
    )
    assert [a["handle"] for a in r.json()["assignees"]] == ["agent", "agent2"]
    assigned = [
        e
        for e in client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
        if e["type"] == "item.assigned"
    ]
    assert [(e["before"], e["after"]) for e in assigned] == [
        ([], ["agent", "alex"]),
        (["agent", "alex"], ["agent", "agent2"]),
    ]
    bad = client.patch(f"{API}/items/{item['id']}", json={"assignees": ["nobody"]}, headers=alex)
    assert bad.status_code == 422


def test_tags_replace_the_set_and_are_auto_created(client, alex, todo):
    item = _item(client, alex, todo["id"])
    r = client.patch(f"{API}/items/{item['id']}", json={"tags": ["AI Project", "h3"]}, headers=alex)
    assert r.json()["tags"] == ["ai project", "h3"]
    assert {t["name"] for t in client.get(f"{API}/tags", headers=alex).json()} == {
        "ai project",
        "h3",
    }
    r = client.patch(f"{API}/items/{item['id']}", json={"tags": ["h3"]}, headers=alex)
    assert r.json()["tags"] == ["h3"]


def test_waiting_on_by_handle(client, alex, todo):
    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    item = _item(client, alex, todo["id"], waiting_on_account_id="alex")
    assert item["waiting_on_account_id"] == me


def test_closing_an_item_clears_waiting_on(client, alex, todo):
    item = _item(client, alex, todo["id"], waiting_on_account_id="alex")
    done = client.patch(f"{API}/items/{item['id']}", json={"status": "Done"}, headers=alex).json()
    assert done["waiting_on_account_id"] is None
    cleared = [
        e
        for e in client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
        if e["field"] == "waiting_on_account_id"
    ]
    assert cleared[-1]["after"] is None
    # Reopening does not bring it back; an open item keeps whatever is set.
    active_id = todo["statuses"][1]["id"]
    back = client.patch(f"{API}/items/{item['id']}", json={"status": active_id}, headers=alex)
    assert back.json()["waiting_on_account_id"] is None


# --- dates ----------------------------------------------------------------------------------


def test_date_only_due_is_17h_chicago_stored_utc(client, alex, todo):
    cdt = _item(client, alex, todo["id"], due_at="2026-10-23")
    cst = _item(client, alex, todo["id"], due_at="2026-12-01")
    assert cdt["due_at"] == "2026-10-23T22:00:00Z"
    assert cst["due_at"] == "2026-12-01T23:00:00Z"
    iso = _item(client, alex, todo["id"], due_at="2026-10-23T08:00:00Z")
    assert iso["due_at"] == "2026-10-23T08:00:00Z"
    r = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "x", "due_at": "friday"},
        headers=alex,
    )
    assert r.status_code == 422 and "ISO 8601" in r.json()["detail"]


# --- archive and delete ---------------------------------------------------------------------


def test_archive_hides_and_include_archived_shows(client, alex, todo):
    item = _item(client, alex, todo["id"])
    kid = _item(client, alex, todo["id"], parent_id=item["id"])
    r = client.post(f"{API}/items/{item['id']}/archive", headers=alex)
    assert r.json()["archived_at"] is not None
    assert _query(client, alex, list_id=todo["id"]) == []
    assert set(_query(client, alex, list_id=todo["id"], include_archived="true")) == {
        item["id"],
        kid["id"],
    }
    assert client.get(f"{API}/items/{item['id']}", headers=alex).status_code == 200
    client.post(f"{API}/items/{item['id']}/unarchive", headers=alex)
    assert set(_query(client, alex, list_id=todo["id"])) == {item["id"], kid["id"]}


def test_archived_list_hides_its_items(client, alex, todo):
    _item(client, alex, todo["id"])
    client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=alex)
    assert _query(client, alex) == []
    assert len(_query(client, alex, include_archived="true")) == 1


def test_delete_is_an_undo_for_the_creator_within_10_minutes(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"], body_md="x", tags=["h3"])
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "c"}, headers=alex)
    assert client.delete(f"{API}/items/{item['id']}", headers=agent).status_code == 422
    assert client.delete(f"{API}/items/{item['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/items/{item['id']}", headers=alex).status_code == 404
    with freeze_time(datetime.now(UTC)) as frozen:
        old = _item(client, alex, todo["id"])
        frozen.tick(timedelta(minutes=11))
        r = client.delete(f"{API}/items/{old['id']}", headers=alex)
    assert r.status_code == 422 and "archive" in r.json()["detail"]


def test_delete_leaves_an_item_deleted_event(client, alex, todo, db):
    from sqlalchemy import select

    from slate.models import Event

    item = _item(client, alex, todo["id"])
    client.delete(f"{API}/items/{item['id']}", headers=alex)
    types = [e.type for e in db.scalars(select(Event).where(Event.item_id == item["id"]))]
    assert types[0] == "item.created" and types[-1] == "item.deleted"


# --- comments -------------------------------------------------------------------------------


def test_comment_add_and_edit_own(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"])
    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "first"}, headers=alex)
    assert r.status_code == 201
    cid = r.json()["id"]
    r = client.patch(f"{API}/comments/{cid}", json={"body_md": "edited"}, headers=alex)
    assert r.json()["body_md"] == "edited" and r.json()["edited_at"] is not None
    assert (
        client.patch(f"{API}/comments/{cid}", json={"body_md": "x"}, headers=agent).status_code
        == 403
    )
    assert (
        client.patch(f"{API}/comments/nope", json={"body_md": "x"}, headers=alex).status_code == 404
    )
    assert (
        client.post(f"{API}/items/nope/comments", json={"body_md": "x"}, headers=alex).status_code
        == 404
    )
    types = [e["type"] for e in client.get(f"{API}/items/{item['id']}/events", headers=alex).json()]
    assert "item.commented" in types and "comment.edited" in types


# --- filters --------------------------------------------------------------------------------


def test_query_filters(client, alex, todo, make_account):
    make_account("agent")
    other_section = client.post(
        f"{API}/sections", json={"name": "AI", "audience": "ai"}, headers=alex
    ).json()
    ideas = client.post(
        f"{API}/lists", json={"section_id": other_section["id"], "name": "Ideas"}, headers=alex
    ).json()
    a = _item(client, alex, todo["id"], "Buy milk", tags=["personal"], due_at="2026-11-01")
    b = _item(client, alex, todo["id"], "Fix faucet", assignees=["agent"], due_at="2026-11-10")
    c = _item(client, alex, ideas["id"], "Slate board", body_md="Svelte milk")
    client.patch(f"{API}/items/{b['id']}", json={"status": "In progress"}, headers=alex)

    assert set(_query(client, alex, list_id=todo["id"])) == {a["id"], b["id"]}
    assert _query(client, alex, section_id=other_section["id"]) == [c["id"]]
    assert _query(client, alex, status="In progress") == [b["id"]]
    assert _query(client, alex, status=todo["statuses"][1]["id"]) == [b["id"]]
    assert _query(client, alex, assignee="agent") == [b["id"]]
    assert _query(client, alex, tag="Personal") == [a["id"]]
    assert _query(client, alex, due_before="2026-11-05") == [a["id"]]
    assert _query(client, alex, due_after="2026-11-05") == [b["id"]]
    assert set(_query(client, alex, q="MILK")) == {a["id"], c["id"]}
    assert set(_query(client, alex, kind="task")) == {a["id"], b["id"], c["id"]}
    assert _query(client, alex, tag="nope") == []
    assert _query(client, alex, assignee="nobody") == []
    assert _query(client, alex, list_id=todo["id"], tag="personal", q="milk") == [a["id"]]


# --- stale ----------------------------------------------------------------------------------


def test_stale_when_due_pushed_three_times(client, alex, todo):
    pushed = _item(client, alex, todo["id"], due_at="2026-11-01")
    twice = _item(client, alex, todo["id"], due_at="2026-11-01")
    for day in ("2026-11-02", "2026-11-03", "2026-11-04"):
        client.patch(f"{API}/items/{pushed['id']}", json={"due_at": day}, headers=alex)
    for day in ("2026-11-02", "2026-11-03"):
        client.patch(f"{API}/items/{twice['id']}", json={"due_at": day}, headers=alex)
    assert _query(client, alex, stale="true") == [pushed["id"]]
    client.patch(f"{API}/items/{pushed['id']}", json={"status": "done"}, headers=alex)
    assert _query(client, alex, stale="true") == []  # done is never stale


def test_stale_when_open_and_untouched_for_30_days(client, alex, todo):
    with freeze_time("2026-09-01T12:00:00Z") as frozen:
        quiet = _item(client, alex, todo["id"])
        busy = _item(client, alex, todo["id"])
        active = _item(client, alex, todo["id"], status="In progress")
        frozen.move_to("2026-09-25T12:00:00Z")
        client.post(f"{API}/items/{busy['id']}/comments", json={"body_md": "poke"}, headers=alex)
        frozen.move_to("2026-10-02T12:00:00Z")
        assert _query(client, alex, stale="true") == [quiet["id"]]
    assert active["id"] not in _query(client, alex, stale="true")


# --- today ----------------------------------------------------------------------------------


def test_today_groups_and_sorts(client, alex, todo, make_account):
    _, agent = make_account("agent")
    with freeze_time("2026-10-23T15:00:00Z"):  # 10:00 in Chicago (CDT)
        overdue = _item(client, agent, todo["id"], "late", due_at="2026-10-22")
        today_low = _item(client, agent, todo["id"], "low", due_at="2026-10-23", priority="low")
        today_urgent = _item(
            client,
            agent,
            todo["id"],
            "urgent",
            due_at="2026-10-23T23:30:00-05:00",
            priority="urgent",
        )
        _item(client, agent, todo["id"], "tomorrow", due_at="2026-10-24")
        waiting = _item(client, agent, todo["id"], "ask alex", waiting_on_account_id="alex")
        _item(client, agent, todo["id"], "ask agent", waiting_on_account_id="agent")
        done = _item(client, agent, todo["id"], "finished", due_at="2026-10-22", status="done")
        archived = _item(client, agent, todo["id"], "gone", due_at="2026-10-22")
        client.post(f"{API}/items/{archived['id']}/archive", headers=agent)

        r = client.get(f"{API}/today", headers=alex)
        assert r.status_code == 200, r.text
        view = r.json()
    assert view["day_ends_at"] == "2026-10-24T05:00:00Z"
    assert [i["id"] for i in view["overdue"]] == [overdue["id"]]
    assert [i["id"] for i in view["today"]] == [today_urgent["id"], today_low["id"]]
    assert [i["id"] for i in view["waiting"]] == [waiting["id"]]
    shown = {i["id"] for g in ("overdue", "today", "waiting") for i in view[g]}
    assert done["id"] not in shown and archived["id"] not in shown


def test_today_after_the_due_hour_moves_to_overdue(client, alex, todo):
    with freeze_time("2026-12-01T12:00:00Z") as frozen:  # 06:00 CST
        item = _item(client, alex, todo["id"], due_at="2026-12-01")  # 17:00 CST = 23:00Z
        assert [i["id"] for i in client.get(f"{API}/today", headers=alex).json()["today"]] == [
            item["id"]
        ]
        frozen.move_to("2026-12-01T23:30:00Z")
        view = client.get(f"{API}/today", headers=alex).json()
    assert [i["id"] for i in view["overdue"]] == [item["id"]]
    assert view["today"] == []


def test_items_carry_comment_count_for_board_cards(client, alex, todo):
    item = _item(client, alex, todo["id"])
    assert item["comment_count"] == 0
    for body in ("first", "second"):
        r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": body}, headers=alex)
        assert r.status_code in (200, 201)
    listed = client.get(f"{API}/items", params={"list_id": todo["id"]}, headers=alex).json()
    assert listed[0]["comment_count"] == 2


def test_today_fields_id_returns_only_ids(client, alex, todo):
    client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "late", "due_at": "2020-01-01"},
        headers=alex,
    )
    full = client.get(f"{API}/today", headers=alex).json()
    ids = client.get(f"{API}/today", params={"fields": "id"}, headers=alex).json()
    assert [i["id"] for i in full["overdue"]] == [i["id"] for i in ids["overdue"]] != []
    assert ids["overdue"] == [{"id": full["overdue"][0]["id"]}]
    assert ids["now"] and ids["day_ends_at"] == full["day_ends_at"]
    trimmed = client.get(f"{API}/today", params={"fields": "title"}, headers=alex).json()
    assert trimmed["overdue"] == [{"id": full["overdue"][0]["id"], "title": "late"}]


# --- external_ref: one card per thing a filer tracks ----------------------------------------


def test_external_ref_unique_409(client, alex, todo):
    first = _item(client, alex, todo["id"], external_ref="probe:disk-full")
    r = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "again", "external_ref": "probe:disk-full"},
        headers=alex,
    )
    assert r.status_code == 409, r.text
    assert r.json() == {
        "detail": f"external_ref already used by {first['id']}",
        "existing_id": first["id"],
        "existing_state": "open",
    }

    def again():
        r = client.post(
            f"{API}/items",
            json={"list_id": todo["id"], "title": "again", "external_ref": "probe:disk-full"},
            headers=alex,
        )
        assert r.status_code == 409 and r.json()["existing_id"] == first["id"], r.text
        return r.json()

    # A closed, archived or trashed card still holds its reference; the refusal says to
    # reopen it rather than file a new one.
    client.patch(f"{API}/items/{first['id']}", json={"status": "Done"}, headers=alex)
    body = again()
    assert body["existing_state"] == "closed"
    assert "reopen that card instead of filing a new one" in body["detail"]
    client.post(f"{API}/items/{first['id']}/archive", headers=alex)
    body = again()
    assert body["existing_state"] == "archived" and "unarchive and reopen" in body["detail"]
    client.post(f"{API}/items/{first['id']}/trash", headers=alex)
    body = again()
    assert body["existing_state"] == "trashed" and "restore and reopen" in body["detail"]
    # Blank is no reference: any number of items may have none.
    _item(client, alex, todo["id"], external_ref="")
    assert _item(client, alex, todo["id"], external_ref="  ")["external_ref"] is None


def test_external_ref_filter(client, alex, todo):
    a = _item(client, alex, todo["id"], external_ref="probe:a")
    _item(client, alex, todo["id"], external_ref="probe:ab")
    _item(client, alex, todo["id"])
    assert _query(client, alex, external_ref="probe:a") == [a["id"]]
    assert _query(client, alex, external_ref="probe:") == []


def test_patch_external_ref_taken_409(client, alex, todo):
    a = _item(client, alex, todo["id"], external_ref="probe:a")
    b = _item(client, alex, todo["id"])
    r = client.patch(f"{API}/items/{b['id']}", json={"external_ref": "probe:a"}, headers=alex)
    assert r.status_code == 409, r.text
    assert r.json()["existing_id"] == a["id"]
    # Setting an item's own reference again is not a conflict.
    r = client.patch(f"{API}/items/{a['id']}", json={"external_ref": "probe:a"}, headers=alex)
    assert r.status_code == 200, r.text
    # Bulk update and split go through the same check.
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [b["id"]], "changes": {"external_ref": "probe:a"}},
        headers=alex,
    )
    assert r.status_code == 409, r.text
    r = client.post(
        f"{API}/items/{b['id']}/split",
        json={"children": [{"title": "x", "external_ref": "probe:a"}]},
        headers=alex,
    )
    assert r.status_code == 409, r.text


# --- bulk: the same validation as PATCH ----------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"position": None},
        {"position": "abc"},
        {"title": 5},
        {"due_at": 12345},
        {"estimate_minutes": "lots"},
        {"assignees": "alex"},
        {"tags": "x"},
        {"add_tags": "abc"},
        {"remove_tags": "abc"},
        {"body_md": 7},
        {"archive": "yes"},
        {"archive": None},
        {"status_kind": "nope"},
        {"no_such_field": 1},
    ],
)
def test_bulk_refuses_wrong_types_like_patch(client, alex, todo, changes):
    item = _item(client, alex, todo["id"], "A")
    r = client.post(
        f"{API}/items/bulk", json={"ids": [item["id"]], "changes": changes}, headers=alex
    )
    assert r.status_code == 422, r.text
    after = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert after["tags"] == [] and after["title"] == "A" and after["archived_at"] is None


def test_bulk_tag_changes_read_every_item_tags_in_one_query(client, alex, todo):
    from sqlalchemy import event

    from slate.db import engine

    ids = [_item(client, alex, todo["id"], f"t{n}", tags=["old"])["id"] for n in range(30)]
    count = {"n": 0}

    def on_exec(*_a, **_k):  # noqa: ANN002, ANN003
        count["n"] += 1

    def statements(changes: dict) -> int:
        count["n"] = 0
        r = client.post(f"{API}/items/bulk", json={"ids": ids, "changes": changes}, headers=alex)
        assert r.status_code == 200, r.text
        return count["n"]

    event.listen(engine, "before_cursor_execute", on_exec)
    try:
        plain = statements({"priority": "high"})
        tagged = statements({"add_tags": ["x"], "remove_tags": ["old"]})
    finally:
        event.remove(engine, "before_cursor_execute", on_exec)
    # Writing the tags costs about six statements per item; reading them must add none.
    assert tagged < plain + 8 * len(ids), (plain, tagged)  # was ~16 per item
    tags = {i["id"]: i["tags"] for i in client.get(f"{API}/items", headers=alex).json()}
    assert all(tags[i] == ["x"] for i in ids)
