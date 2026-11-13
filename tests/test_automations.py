"""Automations: triggers, conditions, actions, the engine's cursor and the run log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from slate.models import Automation, Event, Item, Source
from slate.service import automations, comments, due, items, lists
from slate.service.core import Actor

API = "/api/v1"


@pytest.fixture
def world(client, alex, todo, db):
    """Alex, a Todo list (To do / In progress / Done), a second list, and the engine started."""
    me = client.get(f"{API}/me", headers=alex).json()
    actor = Actor(account_id=me["account"]["id"], source=Source.api)
    other = lists.create_list(db, actor, section_id=todo["section_id"], name="Later")
    automations.start(db)
    return {"actor": actor, "todo": todo, "other": other, "h": alex}


def _rule(client, world, **body):
    body.setdefault("name", "Rule")
    body.setdefault("list_id", world["todo"]["id"])
    r = client.post(f"{API}/automations", json=body, headers=world["h"])
    assert r.status_code == 201, r.text
    return r.json()


def _item(db, world, **fields):
    fields.setdefault("title", "Ship it")
    return items.create_item(db, world["actor"], list_id=world["todo"]["id"], **fields)


def _view(db, item_id):
    db.expire_all()
    return items.views(db, [db.get(Item, item_id)])[0]


def test_status_change_runs_actions_as_the_slate_agent(client, db, world):
    rule = _rule(
        client,
        world,
        trigger={"type": "status_changed", "to_kind": "done"},
        actions=[
            {"type": "add_tag", "tag": "Shipped"},
            {"type": "set_priority", "priority": "low"},
        ],
    )
    item = _item(db, world)
    assert automations.process(db) == 0  # creating it fires nothing here
    items.update_item(db, world["actor"], item.id, {"status": "Done"})
    assert automations.process(db) == 1

    v = _view(db, item.id)
    assert v["tags"] == ["shipped"] and v["priority"] == "low"
    mine = list(db.scalars(select(Event).where(Event.session_ref == f"automation:{rule['id']}")))
    assert mine and {e.source for e in mine} == {Source.system}
    slate = db.scalar(select(Event.actor_id).where(Event.session_ref == f"automation:{rule['id']}"))
    assert slate != world["actor"].account_id

    runs = client.get(f"{API}/automations/{rule['id']}/runs", headers=world["h"]).json()
    assert [r["status"] for r in runs] == ["ok"]
    assert [r["action"] for r in runs[0]["results"]] == ["add_tag", "set_priority"]
    assert runs[0]["item_title"] == "Ship it"
    assert runs[0]["triggered_by"] == world["actor"].account_id
    got = client.get(f"{API}/automations/{rule['id']}", headers=world["h"]).json()
    assert got["run_count"] == 1 and got["last_status"] == "ok"
    assert got["description"] == (
        "When status changes to a done status, then add tag shipped, set priority to low."
    )


def test_processing_twice_runs_once_and_history_is_not_replayed(client, db, world):
    item = _item(db, world)
    items.update_item(db, world["actor"], item.id, {"status": "Done"})
    automations.process(db)  # cursor passes the change before the rule exists
    _rule(
        client,
        world,
        trigger={"type": "status_changed"},
        actions=[{"type": "add_tag", "tag": "x"}],
    )
    assert automations.process(db) == 0
    items.update_item(db, world["actor"], item.id, {"status": "In progress"})
    assert automations.process(db) == 1
    assert automations.process(db) == 0


def test_automation_changes_never_trigger_automations(client, db, world):
    _rule(
        client,
        world,
        name="tag on done",
        trigger={"type": "status_changed", "to_kind": "done"},
        actions=[{"type": "add_tag", "tag": "a"}],
    )
    _rule(
        client,
        world,
        name="done on tag",
        trigger={"type": "tag_added"},
        actions=[{"type": "set_status", "status": "To do"}],
    )
    item = _item(db, world)
    items.update_item(db, world["actor"], item.id, {"status": "Done"})
    assert automations.process(db) == 1
    assert automations.process(db) == 0
    v = _view(db, item.id)
    assert v["tags"] == ["a"] and v["status"]["name"] == "Done"


def test_conditions_and_scope(client, db, world):
    _rule(
        client,
        world,
        trigger={"type": "task_created"},
        conditions=[{"field": "priority", "op": "is", "value": "urgent"}],
        actions=[{"type": "add_tag", "tag": "hot"}],
    )
    calm = _item(db, world, title="calm")
    hot = _item(db, world, title="hot", priority="urgent")
    elsewhere = items.create_item(
        db, world["actor"], list_id=world["other"].id, title="x", priority="urgent"
    )
    assert automations.process(db) == 1
    assert _view(db, hot.id)["tags"] == ["hot"]
    assert _view(db, calm.id)["tags"] == []
    assert _view(db, elsewhere.id)["tags"] == []


def test_section_and_everywhere_scopes(client, db, world):
    _rule(
        client,
        world,
        list_id=None,
        section_id=world["todo"]["section_id"],
        trigger={"type": "task_created"},
        actions=[{"type": "add_tag", "tag": "section"}],
    )
    _rule(
        client,
        world,
        list_id=None,
        trigger={"type": "task_created"},
        actions=[{"type": "set_priority", "priority": "high"}],
    )
    a = items.create_item(db, world["actor"], list_id=world["other"].id, title="a")
    assert automations.process(db) == 2
    v = _view(db, a.id)
    assert v["tags"] == ["section"] and v["priority"] == "high"


def test_assign_and_comment_with_placeholders(client, db, world, make_account):
    make_account("agent")
    _rule(
        client,
        world,
        trigger={"type": "tag_added", "tag": "agent"},
        actions=[
            {"type": "assign", "account": "agent"},
            {"type": "comment", "body": "@agent please take {title} (from {actor})"},
        ],
    )
    item = _item(db, world)
    items.update_item(db, world["actor"], item.id, {"tags": ["agent"]})
    automations.process(db)
    v = _view(db, item.id)
    assert [a["handle"] for a in v["assignees"]] == ["agent"]
    [c] = items.comments_of(db, item.id)
    assert c.body_md == "@agent please take Ship it (from @alex)"
    assert comments.mentions_in(db, c.body_md) == ["agent", "alex"]


def test_failing_action_is_logged_and_the_rest_still_run(client, db, world):
    rule = _rule(
        client,
        world,
        trigger={"type": "task_created"},
        actions=[{"type": "set_status", "status": "Nope"}, {"type": "add_tag", "tag": "ok"}],
    )
    item = _item(db, world)
    automations.process(db)
    assert _view(db, item.id)["tags"] == ["ok"]
    [run] = automations.list_runs(db, automation_id=rule["id"])
    assert run["status"] == "partial"
    assert not run["results"][0]["ok"] and "Nope" in run["results"][0]["detail"]


def test_move_list_and_moved_in(client, db, world):
    _rule(
        client,
        world,
        trigger={"type": "status_changed", "to_kind": "done"},
        actions=[{"type": "move_list", "list_id": world["other"].id}],
    )
    _rule(
        client,
        world,
        list_id=world["other"].id,
        trigger={"type": "moved_in"},
        actions=[{"type": "add_tag", "tag": "arrived"}],
    )
    item = _item(db, world)
    items.update_item(db, world["actor"], item.id, {"status": "Done"})
    automations.process(db)
    v = _view(db, item.id)
    assert v["list_id"] == world["other"].id
    # The move was an automation's own change, so moved_in did not fire on it.
    assert v["tags"] == []
    items.bulk_update(db, world["actor"], [item.id], {"list_id": world["todo"]["id"]})
    items.bulk_update(db, world["actor"], [item.id], {"list_id": world["other"].id})
    automations.process(db)
    assert _view(db, item.id)["tags"] == ["arrived"]


def test_due_passes_and_set_due(client, db, world):
    _rule(
        client,
        world,
        trigger={"type": "due_passes"},
        actions=[{"type": "set_priority", "priority": "urgent"}, {"type": "set_due", "in_days": 1}],
    )
    now = datetime.now(UTC)
    item = _item(db, world, due_at=(now - timedelta(hours=3)).isoformat())
    due.emit_due_events(db, now=now)
    automations.process(db)
    v = _view(db, item.id)
    assert v["priority"] == "urgent"
    assert v["due_at"] > now


def test_comment_mention_trigger(client, db, world, make_account):
    make_account("agent")
    _rule(
        client,
        world,
        trigger={"type": "comment_added", "mentions": "agent"},
        actions=[{"type": "set_status", "status": "In progress"}],
    )
    item = _item(db, world)
    comments.add_comment(db, world["actor"], item.id, body_md="just a note")
    automations.process(db)
    assert _view(db, item.id)["status"]["name"] == "To do"
    comments.add_comment(db, world["actor"], item.id, body_md="@agent over to you")
    automations.process(db)
    assert _view(db, item.id)["status"]["name"] == "In progress"


def test_webhook_action_posts_outside_the_change(client, db, world):
    rule = _rule(
        client,
        world,
        trigger={"type": "task_created"},
        actions=[{"type": "webhook", "url": "https://hooks.example.com/x"}],
    )
    sent = []

    def send(url, payload, secret):
        sent.append((url, payload))
        assert secret == rule["webhook_secret"]
        return True, "HTTP 200"

    _item(db, world)
    automations.process(db, send=send)
    assert rule["signed"] and rule["webhook_secret"].startswith("whsec_")
    again = client.get(f"{API}/automations/{rule['id']}", headers=world["h"]).json()
    assert again["webhook_secret"] is None and again["signed"] is True
    [(url, payload)] = sent
    assert url == "https://hooks.example.com/x"
    assert payload["automation"]["id"] == rule["id"]
    assert payload["event"]["type"] == "item.created"
    assert payload["item"]["title"] == "Ship it"
    [run] = automations.list_runs(db, automation_id=rule["id"])
    assert run["results"] == [{"action": "webhook", "ok": True, "detail": "HTTP 200"}]


def test_disabled_rules_do_not_run(client, db, world):
    rule = _rule(
        client,
        world,
        trigger={"type": "task_created"},
        actions=[{"type": "add_tag", "tag": "x"}],
        enabled=False,
    )
    _item(db, world)
    assert automations.process(db) == 0
    r = client.patch(f"{API}/automations/{rule['id']}", json={"enabled": True}, headers=world["h"])
    assert r.json()["enabled"] is True
    _item(db, world)
    assert automations.process(db) == 1


def test_run_now_and_all_runs(client, db, world):
    rule = _rule(
        client,
        world,
        trigger={"type": "due_arrives"},
        actions=[{"type": "archive"}],
    )
    item = _item(db, world)
    r = client.post(
        f"{API}/automations/{rule['id']}/run", json={"item_id": item.id}, headers=world["h"]
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    db.expire_all()
    assert db.get(Item, item.id).archived_at is not None
    log = client.get(f"{API}/automation-runs", headers=world["h"]).json()
    assert [x["automation_name"] for x in log] == ["Rule"]


def test_validation_is_plain_english(client, world):
    def bad(**body):
        body.setdefault("name", "x")
        body.setdefault("trigger", {"type": "task_created"})
        body.setdefault("actions", [{"type": "add_tag", "tag": "a"}])
        r = client.post(f"{API}/automations", json=body, headers=world["h"])
        assert r.status_code in (404, 422), r.text
        return r.json()["detail"]

    assert "unknown trigger" in bad(trigger={"type": "whenever"})
    assert "unknown action" in bad(actions=[{"type": "explode"}])
    assert "needs tag" in bad(actions=[{"type": "add_tag"}])
    assert "not both" in bad(list_id=world["todo"]["id"], section_id=world["todo"]["section_id"])
    assert "to_kind must be one of" in bad(
        trigger={"type": "status_changed", "to_kind": "finished"}
    )
    assert "no account" in bad(actions=[{"type": "assign", "account": "ghost"}])
    assert "non-empty" in bad(actions=[])
    assert "does not take" in bad(actions=[{"type": "archive", "why": "x"}])


def test_catalog_crud_and_delete(client, world):
    cat = client.get(f"{API}/automations/catalog", headers=world["h"]).json()
    assert {t["type"] for t in cat["triggers"]} >= {"status_changed", "due_passes", "moved_in"}
    assert {a["type"] for a in cat["actions"]} >= {"webhook", "comment", "move_list"}
    rule = _rule(client, world, trigger={"type": "task_created"}, actions=[{"type": "archive"}])
    listed = client.get(
        f"{API}/automations", params={"list_id": world["todo"]["id"]}, headers=world["h"]
    ).json()
    assert [r["id"] for r in listed] == [rule["id"]]
    r = client.patch(
        f"{API}/automations/{rule['id']}",
        json={"name": "Archive new", "conditions": [{"field": "tag", "value": "Junk"}]},
        headers=world["h"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["conditions"] == [{"field": "tag", "op": "is", "value": "junk"}]
    assert client.delete(f"{API}/automations/{rule['id']}", headers=world["h"]).status_code == 204
    assert client.get(f"{API}/automations/{rule['id']}", headers=world["h"]).status_code == 404


def test_items_in_archived_lists_are_left_alone(client, db, world):
    _rule(
        client,
        world,
        list_id=world["other"].id,
        trigger={"type": "task_created"},
        actions=[{"type": "add_tag", "tag": "x"}],
    )
    item = items.create_item(db, world["actor"], list_id=world["other"].id, title="x")
    lists.update_list(db, world["actor"], world["other"].id, archived=True)
    assert automations.process(db) == 0
    assert _view(db, item.id)["tags"] == []


def test_engine_step_and_webhook_sender(client, db, world, monkeypatch):
    import httpx

    from slate import automator
    from slate.outbound import sign

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["X-Slate-Automation"], request.read()))
        assert request.headers["X-Slate-Signature"] == sign(secret[0], request.read())
        return httpx.Response(500 if b"fail" in request.read() else 204)

    secret: list[str] = []
    monkeypatch.setattr(automator, "transport", httpx.MockTransport(handler))
    rule = _rule(
        client,
        world,
        trigger={"type": "task_created"},
        actions=[{"type": "webhook", "url": "https://hooks.example.com/x"}],
    )
    secret.append(rule["webhook_secret"])
    _item(db, world, title="fine")
    _item(db, world, title="fail")
    assert automator.step() == 2
    assert [s[0] for s in seen] == [rule["id"], rule["id"]]
    runs = automations.list_runs(db, automation_id=rule["id"])
    assert sorted(r["status"] for r in runs) == ["failed", "ok"]
    assert {r["results"][0]["detail"] for r in runs} == {"HTTP 204", "HTTP 500"}


def test_signing_secret_is_never_in_events_and_rotates(client, db, world):
    rule = _rule(
        client,
        world,
        trigger={"type": "task_created"},
        actions=[{"type": "add_tag", "tag": "x"}],
    )
    assert rule["signed"] is False and rule["webhook_secret"] is None
    r = client.patch(
        f"{API}/automations/{rule['id']}",
        json={"actions": [{"type": "webhook", "url": "https://hooks.example.com/x"}]},
        headers=world["h"],
    )
    first = r.json()["webhook_secret"]
    assert first and r.json()["signed"]
    r = client.post(f"{API}/automations/{rule['id']}/secret", headers=world["h"])
    second = r.json()["secret"]
    assert second.startswith("whsec_") and second != first
    stored = db.get(Automation, rule["id"])
    db.refresh(stored)
    assert automations.signing_secret(stored) == second
    blob = str([(e.before_json, e.after_json) for e in db.scalars(select(Event))])
    assert first not in blob and second not in blob and "secret_enc" not in blob


def test_agent_reported_trigger_moves_the_card_when_the_agent_changes_state(
    client, db, world, make_account
):
    from slate.service import reports

    bot, _ = make_account("agent")
    bot_actor = Actor(account_id=bot["id"], source=Source.api)
    rule = _rule(
        client,
        world,
        trigger={"type": "agent_reported", "state": "working"},
        actions=[{"type": "set_status", "status": "In progress"}],
    )
    assert rule["description"].startswith("When an agent reports working")
    item = _item(db, world)

    reports.report(db, bot_actor, item.id, state="working", note="Started", comment=False)
    assert automations.process(db) == 1
    assert _view(db, item.id)["status"]["name"] == "In progress"

    # More working reports (a new note) are not a new start: the rule does not run again.
    items.update_item(db, world["actor"], item.id, {"status": "To do"})
    reports.report(db, bot_actor, item.id, state="working", note="Still at it", comment=False)
    assert automations.process(db) == 0
    assert _view(db, item.id)["status"]["name"] == "To do"

    # Any state when the trigger names none; review after working is a change, so it fires.
    _rule(
        client,
        world,
        name="Any",
        trigger={"type": "agent_reported"},
        actions=[{"type": "add_tag", "tag": "touched"}],
    )
    reports.report(db, bot_actor, item.id, state="review", note="Fixed", comment=False)
    assert automations.process(db) == 1
    assert "touched" in _view(db, item.id)["tags"]


def test_agent_reported_trigger_validates_the_state(client, world):
    r = client.post(
        f"{API}/automations",
        json={
            "name": "x",
            "list_id": world["todo"]["id"],
            "trigger": {"type": "agent_reported", "state": "dancing"},
            "actions": [{"type": "archive"}],
        },
        headers=world["h"],
    )
    assert r.status_code == 422 and "state must be one of" in r.json()["detail"]
