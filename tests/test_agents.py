"""Agent reports on items, the agents roster, per-actor events, and rate limits."""

from __future__ import annotations

import pytest

from conftest import API
from slate.presence import presence
from slate.ratelimit import limiter
from slate.settings import settings


@pytest.fixture(autouse=True)
def _clean():
    presence.reset()
    limiter.reset()
    yield
    presence.reset()
    limiter.reset()


def _item(client, h, list_id, title="Draft the newsletter", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _report(client, h, item_id, **body):
    return client.post(f"{API}/items/{item_id}/report", json=body, headers=h)


def test_working_report_sets_the_badge_comments_and_records_one_event(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    r = _report(client, ch, item["id"], state="working", note="Outlining sections", progress=30)
    assert r.status_code == 200, r.text
    flag = r.json()["agent"]
    assert flag["state"] == "working" and flag["by"] == agent["id"]
    assert flag["note"] == "Outlining sections" and flag["progress"] == 30

    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert detail["comments"][-1]["body_md"] == "**Working (30%)** · Outlining sections"
    assert detail["comments"][-1]["author_id"] == agent["id"]
    events = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    reported = [e for e in events if e["type"] == "item.reported"]
    assert len(reported) == 1 and reported[0]["actor_id"] == agent["id"]
    assert reported[0]["before"] is None and reported[0]["after"]["state"] == "working"
    # A flag, not a column: the status did not move.
    assert r.json()["status"]["name"] == "To do"


def test_done_clears_the_badge_and_completes_unless_told_not_to(client, alex, todo, make_account):
    _, ch = make_account("agent")
    a = _item(client, alex, todo["id"], "a")
    b = _item(client, alex, todo["id"], "b")
    _report(client, ch, a["id"], state="working")
    done = _report(client, ch, a["id"], state="done", note="Sent").json()
    assert done["agent"] is None and done["status"]["kind"] == "done" and done["completed_at"]
    kept = _report(client, ch, b["id"], state="done", complete=False).json()
    assert kept["status"]["name"] == "To do"


def test_blocked_waiting_on_alex_lands_in_his_today(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    r = _report(client, ch, item["id"], state="blocked", note="Which list?", waiting_on="alex")
    assert r.json()["agent"]["state"] == "blocked"
    today = client.get(f"{API}/today", headers=alex).json()
    assert [i["id"] for i in today["waiting"]] == [item["id"]]


def test_assign_me_and_idle(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["alex"])
    r = _report(client, ch, item["id"], state="working", assign_me=True).json()
    assert {a["handle"] for a in r["assignees"]} == {"alex", "agent"}
    assert _report(client, ch, item["id"], state="idle").json()["agent"] is None


def test_report_validation(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    assert _report(client, ch, item["id"], state="napping").status_code == 422
    assert _report(client, ch, item["id"], state="working", progress=101).status_code == 422
    r = _report(client, ch, item["id"], state="working", waiting_on="alex")
    assert r.status_code == 422 and "blocked" in r.json()["detail"]
    _, reader = make_account("peek", ("read",))
    assert _report(client, reader, item["id"], state="working").status_code == 403


def test_roster_shows_presence_work_and_activity(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    make_account("agent2")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _report(client, ch, item["id"], state="working", note="On it", progress=10)
    roster = client.get(f"{API}/agents", headers=alex).json()
    assert [r["account"]["handle"] for r in roster] == ["agent", "agent2"]
    c = roster[0]
    assert c["online"] is True and c["last_seen_via"] == "api"
    assert c["working"][0]["item_id"] == item["id"] and c["working"][0]["note"] == "On it"
    assert c["open_assigned"] == 1 and c["events_24h"] >= 2
    assert roster[1]["online"] is False and roster[1]["working"] == []
    people = client.get(f"{API}/agents?kind=person", headers=alex).json()
    assert [r["account"]["handle"] for r in people] == ["alex"]


def test_events_filter_by_actor_is_an_audit_trail(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    client.patch(f"{API}/items/{item['id']}", json={"title": "x"}, headers=ch)
    page = client.get(f"{API}/events?since=0&actor=agent", headers=alex).json()
    assert [e["type"] for e in page["events"]] == ["item.updated"]
    nobody = client.get(f"{API}/events?since=0&actor=ghost", headers=alex).json()
    assert nobody["events"] == []


def test_rate_limit_answers_429_with_retry_after(client, alex, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_reads_per_minute", 3)
    for _ in range(3):
        assert client.get(f"{API}/me", headers=alex).status_code == 200
    r = client.get(f"{API}/me", headers=alex)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1 and "rate limit" in r.json()["detail"]
    me = client.get(f"{API}/me", headers=alex)
    assert me.status_code == 429
    assert limiter.throttled  # counted for the roster


def test_rate_limit_buckets_refill_and_split_reads_from_writes(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_writes_per_minute", 60)
    monkeypatch.setattr(settings, "rate_limit_reads_per_minute", 0)  # off
    for _ in range(60):
        assert limiter.take("tok", "acct", "write", now=100.0).allowed
    refused = limiter.take("tok", "acct", "write", now=100.0)
    assert not refused.allowed and refused.retry_after == 1
    assert limiter.take("tok", "acct", "write", now=101.0).allowed  # one per second back
    assert limiter.take("tok", "acct", "read", now=100.0).allowed
    assert limiter.take("other", "acct", "write", now=100.0).allowed  # per token


def test_events_tail_by_agents_is_the_live_feed_seed(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    for n in range(3):
        client.patch(f"{API}/items/{item['id']}", json={"title": f"t{n}"}, headers=ch)
    page = client.get(f"{API}/events?tail=true&limit=2&actor_kind=agent", headers=alex).json()
    assert [e["after"] for e in page["events"]] == ["t1", "t2"]  # newest two, oldest first
    assert page["next_since"] == page["events"][-1]["id"]
    people = client.get(f"{API}/events?tail=true&actor_kind=person", headers=alex).json()
    assert {e["actor_id"] for e in people["events"]} == {
        client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    }


# --- claim -------------------------------------------------------------------------------


def test_claim_takes_a_free_item_and_assigns_the_claimer(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    r = _report(client, ch, item["id"], state="working", claim=True, note="Mine")
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["by"] == agent["id"]
    assert [a["handle"] for a in r.json()["assignees"]] == ["agent"]
    # Claiming again (a refresh) is fine for the holder.
    assert _report(client, ch, item["id"], state="working", claim=True).status_code == 200


def test_a_second_claim_is_409_and_writes_nothing(client, alex, todo, make_account, db):
    _, ch = make_account("agent")
    _, hh = make_account("agent3")
    item = _item(client, alex, todo["id"])
    assert _report(client, ch, item["id"], state="working", claim=True).status_code == 200
    r = _report(client, hh, item["id"], state="working", claim=True, note="Me too")
    assert r.status_code == 409 and "agent" in r.json()["detail"]
    detail = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert [a["handle"] for a in detail["assignees"]] == ["agent"]
    assert not any("Me too" in c["body_md"] for c in detail.get("comments", []))


def test_a_stale_or_cleared_badge_can_be_claimed(client, alex, todo, make_account, db):
    from datetime import timedelta

    from slate.models import Item, utcnow
    from slate.service.reports import CLAIM_FRESH

    _, ch = make_account("agent")
    _, hh = make_account("agent3")
    item = _item(client, alex, todo["id"])
    _report(client, ch, item["id"], state="working", claim=True)
    # Age the badge past CLAIM_FRESH (a direct column write, outside the change path).
    from sqlalchemy import update

    with db.connection() as conn:
        conn.execute(
            update(Item)
            .where(Item.id == item["id"])
            .values(agent_state_at=utcnow() - CLAIM_FRESH - timedelta(minutes=1))
        )
        conn.commit()
    assert _report(client, hh, item["id"], state="working", claim=True).status_code == 200
    assert _report(client, hh, item["id"], state="idle").status_code == 200
    assert _report(client, ch, item["id"], state="working", claim=True).status_code == 200


def test_claim_only_goes_with_working(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    assert _report(client, ch, item["id"], state="review", claim=True).status_code == 422


@pytest.fixture
def counted_binds(monkeypatch):
    from slate.service import access

    binds = {"n": 0}
    real = access.bind

    def counted(*a, **k):  # noqa: ANN002, ANN003
        binds["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(access, "bind", counted)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_reads_per_minute", 2)
    return binds


def test_a_refused_call_costs_no_access_computation(client, make_account, counted_binds):
    """The limiter answers before the caller's access is worked out, so a runaway loop that
    is being refused stays cheap."""
    _, agent = make_account("my-agent")
    counted_binds["n"] = 0
    statuses = [client.get(f"{API}/me", headers=agent).status_code for _ in range(5)]
    assert statuses == [200, 200, 429, 429, 429]
    assert counted_binds["n"] == 2


async def test_a_refused_mcp_call_costs_no_access_computation(alex, counted_binds):
    from test_mcp import call, http_mcp

    async with http_mcp(alex) as mcp:
        answers = [await call(mcp, "slate_today") for _ in range(4)]
    refused = [a for a in answers if a.is_error and "rate limit" in a.content[0].text]
    assert len(refused) == 2
    assert counted_binds["n"] == 2
