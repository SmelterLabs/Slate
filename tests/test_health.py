"""The Health page: per-token calls, pollers and their alerts, /health and /api/v1/health."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from slate import monitor, scheduler
from slate.db import SessionLocal
from slate.models import Event, utcnow
from slate.settings import settings

API = "/api/v1"


@pytest.fixture(autouse=True)
def fresh_monitor():
    monitor.calls.reset()
    monitor.pollers.reset()
    yield
    monitor.calls.reset()
    monitor.pollers.reset()


def _health(client, headers) -> dict:
    r = client.get(f"{API}/health", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_owner_reads_the_report(client, alex):
    body = _health(client, alex)
    assert body["status"] in ("ok", "warn", "fail")
    sections = {"checks", "pollers", "engines", "agents", "latency", "automations", "queues", "db"}
    assert sections <= set(body)
    keys = [p["key"] for p in body["pollers"]]
    assert keys[:2] == ["reminders", "digest"]
    # Tests run with the scheduler off: every poller says so instead of failing.
    assert all(p["status"] == "off" for p in body["pollers"])
    assert body["db"]["events"] > 0


def test_members_guests_and_limited_tokens_are_refused(client, alex, make_account):
    _, member = make_account("member", ("read",), kind="person")
    r = client.get(f"{API}/health", headers=member)
    assert r.status_code == 403
    assert "owner or admin" in r.json()["detail"]


def test_calls_are_counted_per_token_with_errors_and_slow_calls(
    client, alex, make_account, monkeypatch
):
    acct, agent = make_account("agent", ("write",))
    client.get(f"{API}/items", headers=agent)
    client.get(f"{API}/items/Zz0Zz0Zz0Zz0", headers=agent)  # 404
    client.post(f"{API}/items", json={"title": "no list"}, headers=agent)  # 422
    monkeypatch.setattr(settings, "health_slow_ms", 0.0)
    client.get(f"{API}/tags", headers=agent)  # slow, by the threshold

    body = _health(client, alex)
    row = next(t for t in body["agents"]["tokens"] if t["account_id"] == acct["id"])
    assert row["handle"] == "agent" and row["kind"] == "agent"
    assert row["calls"] == 4
    assert row["client_errors"] == 2 and row["server_errors"] == 0
    assert row["error_rate"] == 0.5
    assert row["slow"] >= 1
    assert row["last_error"]["status"] == 422
    errs = [e for e in body["agents"]["recent_errors"] if e["account_id"] == acct["id"]]
    assert {e["status"] for e in errs} == {404, 422}
    assert errs[0]["route"].startswith("POST /api/v1/items")
    assert any(s["handle"] == "agent" for s in body["agents"]["slow_calls"])
    assert body["latency"]["samples"] >= 4


def test_window_drops_calls_older_than_an_hour():
    now = 1_800_000_000.0
    monitor.calls.record("t1", "a1", "GET /x", 5, now=now - 3700)
    monitor.calls.record("t1", "a1", "GET /x", 5, status=500, now=now)
    (row,) = monitor.calls.snapshot(now=now)
    assert row["calls"] == 1 and row["server_errors"] == 1 and row["error_rate"] == 1.0


def test_outcome_reads_hook_results():
    assert monitor.outcome(None) == (False, None)
    assert monitor.outcome(3) == (True, None)
    assert monitor.outcome({"errors": []}) == (True, None)
    assert monitor.outcome({"errors": ["a/b: 401"]}) == (True, "a/b: 401")
    assert monitor.outcome({"read": 0, "error": "OSError: refused"}) == (True, "OSError: refused")


def _events(kind: str) -> list[Event]:
    with SessionLocal() as s:
        return list(s.scalars(select(Event).where(Event.type == kind)))


def test_a_failing_poller_alerts_once_then_recovers(client, alex, monkeypatch):
    calls = {"fail": True}

    def poll_tick(session, now):
        if calls["fail"]:
            return {"read": 0, "results": {}, "error": "OSError: connection refused"}
        return {"read": 2, "results": {}, "error": None}

    monkeypatch.setattr(scheduler, "hooks", [poll_tick])
    monkeypatch.setattr(scheduler, "POLLERS", {poll_tick: "reminders"})
    monkeypatch.setattr(settings, "health_alert_after", 3)
    now = utcnow()
    for i in range(2):
        scheduler.tick(now + timedelta(minutes=i))
    assert _events("health.alert") == []
    state = monitor.pollers.snapshot()["reminders"]
    assert state["consecutive_failures"] == 2 and state["last_ok_at"] is None

    for i in range(2, 5):  # the third failure alerts; later ones do not repeat it
        scheduler.tick(now + timedelta(minutes=i))
    (alert,) = _events("health.alert")
    assert alert.after_json["check"] == "poller:reminders"
    assert alert.after_json["label"] == "Reminders"
    assert alert.after_json["failures"] == 3
    assert "connection refused" in alert.after_json["error"]

    body = _health(client, alex)
    gh = next(p for p in body["pollers"] if p["key"] == "reminders")
    assert gh["status"] == "fail" and gh["consecutive_failures"] == 5
    assert "connection refused" in gh["detail"]
    assert body["status"] == "fail"

    inbox = client.get(f"{API}/inbox", headers=alex).json()
    entries = inbox["entries"] if isinstance(inbox, dict) else inbox
    assert [e["reason"] for e in entries if e["event"]["type"] == "health.alert"] == ["health"]

    calls["fail"] = False
    scheduler.tick(now + timedelta(minutes=6))
    (rec,) = _events("health.recovered")
    assert rec.after_json["check"] == "poller:reminders"
    state = monitor.pollers.snapshot()["reminders"]
    assert state["consecutive_failures"] == 0 and state["last_ok_at"] is not None


def test_a_hook_that_raises_counts_as_a_failure(monkeypatch):
    def sync_tick(session, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "hooks", [sync_tick])
    monkeypatch.setattr(scheduler, "POLLERS", {sync_tick: "reminders"})
    scheduler.tick()
    state = monitor.pollers.snapshot()["reminders"]
    assert state["failures"] == 1 and state["last_error"] == "RuntimeError: boom"
    assert state["running_since"] is None


def test_members_do_not_get_health_alerts(client, alex, make_account, monkeypatch):
    _, member = make_account("member", ("write",), kind="person")
    monkeypatch.setattr(settings, "health_alert_after", 1)

    def poll_tick(session, now):
        raise OSError("down")

    monkeypatch.setattr(scheduler, "hooks", [poll_tick])
    monkeypatch.setattr(scheduler, "POLLERS", {poll_tick: "reminders"})
    scheduler.tick()
    assert len(_events("health.alert")) == 1
    inbox = client.get(f"{API}/inbox", headers=member).json()
    entries = inbox["entries"] if isinstance(inbox, dict) else inbox
    assert not [e for e in entries if e["event"]["type"].startswith("health.")]


def test_public_health_is_the_verdict_without_details(client, alex, monkeypatch):
    from slate import app as app_module

    monkeypatch.setattr(app_module, "HEALTH_CACHE_SECONDS", 0)  # see each answer fresh
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "warn")
    assert body["checks"] and set(body["checks"][0]) == {"key", "label", "status"}

    monkeypatch.setattr(settings, "health_alert_after", 1)

    def poll_tick(session, now):
        return {"errors": ["the mail server answered 401 unauthorized-for-relay"]}

    monkeypatch.setattr(scheduler, "hooks", [poll_tick])
    monkeypatch.setattr(scheduler, "POLLERS", {poll_tick: "reminders"})
    scheduler.tick()
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "fail"
    gh = next(c for c in body["checks"] if c["key"] == "poller:reminders")
    assert gh["status"] == "fail"
    # Details stay behind the login. The error text is matched whole: a bare "401" can turn up
    # in a timestamp or a duration of the public body.
    assert "unauthorized-for-relay" not in json.dumps(body)
    assert all(set(c) == {"key", "label", "status"} for c in body["checks"])


def test_stuck_poller_shows_as_failing(client, alex):
    monitor.pollers.begin("recurring", now=utcnow() - timedelta(minutes=10))
    body = _health(client, alex)
    rec = next(p for p in body["pollers"] if p["key"] == "recurring")
    assert rec["status"] == "fail" and rec["detail"].startswith("Stuck")


def test_failed_automation_runs_and_queues(client, alex, todo):
    from slate.models import Automation, AutomationRun, Source
    from slate.service.core import Actor, apply_change

    with SessionLocal() as s:
        me = s.scalar(select(Event.actor_id).limit(1))
        rule = Automation(
            name="Ping receiver",
            trigger_json={"type": "created"},
            actions_json=[{"type": "webhook"}],
            created_by=me,
        )
        with apply_change(s, Actor(account_id=me, source=Source.api)) as cx:
            cx.create(rule, "automation.created")
        s.add(
            AutomationRun(
                automation_id=rule.id,
                status="failed",
                results_json=[{"action": "webhook", "ok": False, "detail": "HTTP 500"}],
            )
        )
        s.commit()
    body = _health(client, alex)
    assert body["automations"]["failed_24h"] == 1
    (run,) = body["automations"]["recent"]
    assert run["name"] == "Ping receiver" and run["detail"] == "webhook: HTTP 500"
    check = next(c for c in body["checks"] if c["key"] == "automations")
    assert check["status"] == "warn"
    assert body["queues"]["webhooks_pending"] == 0


async def test_slate_health_over_mcp_counts_the_call(alex):
    from contextlib import asynccontextmanager

    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    from slate.app import create_app

    @asynccontextmanager
    async def mcp(headers):
        app = create_app()
        async with app.state.mcp_manager.run():
            transport = httpx2.ASGITransport(app=app)
            async with (
                httpx2.AsyncClient(
                    transport=transport, base_url="http://slate", headers=headers
                ) as hc,
                Client(streamable_http_client("http://slate/mcp", http_client=hc)) as client,
            ):
                yield client

    async with mcp(alex) as client:
        result = await client.call_tool("slate_health", {})
        assert not result.is_error, result.content[0].text
        body = json.loads(result.content[0].text)
        assert body["status"] in ("ok", "warn", "fail")
        bad = await client.call_tool(
            "slate_item", {"action": "get", "params": {"id": "Zz0Zz0Zz0Zz0"}}
        )
        assert bad.is_error
    rows = monitor.calls.snapshot()
    (row,) = [r for r in rows if r["client_errors"]]
    assert row["last_error"]["door"] == "mcp"
    assert row["last_error"]["route"] == "slate_item get"
