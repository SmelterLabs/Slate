"""The agent work loop: /work/next takes the best task with a lease, heartbeat keeps it, finish
reports back (and can take the next one) in one call; expired leases give the task back."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import API
from run_proofs import remember, run_fields
from slate import db as slate_db
from slate.models import Item, Source, WorkLease, utcnow
from slate.service import work
from slate.service.core import Actor, apply_change


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _next(client, h, **body):
    r = client.post(f"{API}/work/next", json=body, headers=h)
    assert r.status_code == 200, r.text
    return remember(client, h, r.json())


def _finish(client, h, item_id, **body):
    body = run_fields(client, h, item_id, **body)
    r = client.post(f"{API}/work/{item_id}/finish", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _link(client, h, blocker, blocked):
    r = client.post(
        f"{API}/items/{blocker}/links", json={"kind": "blocks", "item_id": blocked}, headers=h
    )
    assert r.status_code in (200, 201), r.text


def test_next_takes_the_most_urgent_assigned_task_and_holds_it(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    _item(client, alex, todo["id"], "low", assignees=["agent"], priority="low")
    urgent = _item(client, alex, todo["id"], "urgent", assignees=["agent"], priority="urgent")
    _item(client, alex, todo["id"], "not mine", priority="urgent")

    got = _next(client, ch)
    assert got["item"]["id"] == urgent["id"]
    assert got["why"] == "assigned" and got["resumed"] is False
    assert got["item"]["agent"]["state"] == "working"
    assert got["lease"]["account_id"] == agent["id"]
    assert got["remaining"] == 1
    assert isinstance(got["context"], list)

    # Resuming requires the proof from this exact take.
    again = _next(client, ch, item_id=urgent["id"], run_id=got["lease"]["run_id"])
    assert again["item"]["id"] == urgent["id"] and again["resumed"] is True
    assert again["lease"]["until"] >= got["lease"]["until"]

    # resume=false takes another one.
    other = _next(client, ch, resume=False)
    assert other["item"]["title"] == "low"


def test_next_returns_null_when_nothing_is_free(client, alex, todo, make_account):
    _, ch = make_account("agent")
    got = _next(client, ch)
    assert got["item"] is None and got["lease"] is None and got["remaining"] == 0


def test_next_skips_blocked_review_waiting_and_future_tasks(client, alex, todo, make_account):
    _, ch = make_account("agent")
    make_account("agent2")
    upstream = _item(client, alex, todo["id"], "upstream")
    blocked = _item(client, alex, todo["id"], "blocked", assignees=["agent"], priority="urgent")
    _link(client, alex, upstream["id"], blocked["id"])
    _item(
        client,
        alex,
        todo["id"],
        "later",
        assignees=["agent"],
        priority="urgent",
        start_at=(utcnow() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _item(
        client,
        alex,
        todo["id"],
        "waits on agent2",
        assignees=["agent"],
        priority="urgent",
        waiting_on_account_id="agent2",
    )
    review = _item(client, alex, todo["id"], "in review", assignees=["agent"], priority="high")
    client.post(
        f"{API}/items/{review['id']}/report",
        json=run_fields(client, ch, review["id"], **{"state": "review"}),
        headers=ch,
    )
    plain = _item(client, alex, todo["id"], "plain", assignees=["agent"], priority="low")

    assert _next(client, ch)["item"]["id"] == plain["id"]
    _finish(client, ch, plain["id"], outcome="done")
    assert _next(client, ch)["item"] is None

    # Finishing the upstream task frees the blocked one.
    client.patch(f"{API}/items/{upstream['id']}", json={"status": "Done"}, headers=alex)
    assert _next(client, ch)["item"]["id"] == blocked["id"]


def test_two_agents_never_get_the_same_pool_task(client, alex, todo, make_account):
    _, ch = make_account("agent")
    agent2, dh = make_account("agent2")
    a = _item(client, alex, todo["id"], "a", priority="high")
    b = _item(client, alex, todo["id"], "b")

    assert _next(client, ch)["item"] is None  # unassigned work needs pool
    first = _next(client, ch, pool=True)
    second = _next(client, dh, pool=True)
    assert first["item"]["id"] == a["id"] and first["why"] == "pool"
    assert second["item"]["id"] == b["id"]
    assert [x["handle"] for x in first["item"]["assignees"]] == ["agent"]
    assert set(first["lease"]) == {"run_id", "item_id", "account_id", "claimed_at", "until"}

    # A second agent cannot take the first agent's held task by claim either.
    r = client.post(
        f"{API}/items/{a['id']}/report",
        json=run_fields(client, dh, a["id"], **{"state": "working", "claim": True}),
        headers=dh,
    )
    assert r.status_code == 409

    # Releasing a pool task un-assigns and frees it.
    out = _finish(client, ch, a["id"], outcome="release", note="Not for me")
    assert out["item"]["assignees"] == [] and out["item"]["agent"] is None
    assert _next(client, dh, pool=True, resume=False)["item"]["id"] == a["id"]


def test_heartbeat_extends_and_reports_progress_and_409_when_lost(client, alex, todo, make_account):
    _, ch = make_account("agent")
    _, dh = make_account("agent2")
    task = _item(client, alex, todo["id"], "t", assignees=["agent", "agent2"])
    got = _next(client, ch)
    r = client.post(
        f"{API}/work/{task['id']}/heartbeat",
        json=run_fields(
            client, ch, task["id"], **{"lease_seconds": 3600, "progress": 40, "note": "halfway"}
        ),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert r.json()["until"] > got["lease"]["until"]
    item = client.get(f"{API}/items/{task['id']}", headers=alex).json()
    assert item["agent"]["progress"] == 40 and item["agent"]["note"] == "halfway"

    # A bare heartbeat writes no event.
    before = len(client.get(f"{API}/items/{task['id']}/events", headers=alex).json())
    assert (
        client.post(
            f"{API}/work/{task['id']}/heartbeat",
            json=run_fields(client, ch, task["id"]),
            headers=ch,
        ).status_code
        == 200
    )
    after = len(client.get(f"{API}/items/{task['id']}/events", headers=alex).json())
    assert after == before

    # A heartbeat repeating the note and progress the badge already shows writes no event
    # either (a chatty runner cannot bury the history); a new note writes one.
    same = run_fields(client, ch, task["id"], progress=40, note="halfway")
    assert client.post(f"{API}/work/{task['id']}/heartbeat", json=same, headers=ch).is_success
    assert len(client.get(f"{API}/items/{task['id']}/events", headers=alex).json()) == before
    newer = run_fields(client, ch, task["id"], progress=40, note="three quarters")
    assert client.post(f"{API}/work/{task['id']}/heartbeat", json=newer, headers=ch).is_success
    assert len(client.get(f"{API}/items/{task['id']}/events", headers=alex).json()) == before + 1

    # The second agent is assigned too, but cannot take it while the lease runs.
    assert _next(client, dh)["item"] is None
    assert (
        client.post(
            f"{API}/work/{task['id']}/heartbeat",
            json=run_fields(client, dh, task["id"], **{}),
            headers=dh,
        ).status_code
        == 409
    )

    # Expiry is unknown, not permission for the second agent to execute over the first's work.
    with slate_db.SessionLocal() as s:
        s.get(WorkLease, task["id"]).until = utcnow() - timedelta(seconds=1)
        s.commit()
    assert _next(client, dh)["item"] is None
    assert (
        client.post(
            f"{API}/work/{task['id']}/heartbeat",
            json=run_fields(client, ch, task["id"]),
            headers=ch,
        ).status_code
        == 409
    )


def test_a_mid_turn_review_of_its_own_does_not_hide_the_run_still_going(
    client, alex, todo, make_account
):
    """The agent reports review with its own tool while its runner's turn goes on: the next
    heartbeat takes the card back (badge Working), and the finish has the last word."""
    _, ch = make_account("agent")
    task = _item(client, alex, todo["id"], "t", assignees=["agent"])
    _next(client, ch)
    r = client.post(
        f"{API}/items/{task['id']}/report",
        json=run_fields(client, ch, task["id"], **{"state": "review"}),
        headers=ch,
    )
    assert r.status_code == 200 and r.json()["agent"]["state"] == "review"

    r = client.post(
        f"{API}/work/{task['id']}/heartbeat",
        json=run_fields(client, ch, task["id"], **{"note": "The runtime is using terminal"}),
        headers=ch,
    )
    assert r.status_code == 200, r.text
    item = client.get(f"{API}/items/{task['id']}", headers=alex).json()
    assert (
        item["agent"]["state"] == "working"
        and item["agent"]["note"] == "The runtime is using terminal"
    )

    out = _finish(client, ch, task["id"], outcome="review", note="Ready")
    assert out["item"]["agent"]["state"] == "review"

    # A person's dismissal is not the agent's badge: a late heartbeat stays refused.
    client.post(
        f"{API}/items/{task['id']}/report",
        json=run_fields(client, alex, task["id"], **{"state": "idle", "comment": False}),
        headers=alex,
    )
    assert (
        client.post(
            f"{API}/work/{task['id']}/heartbeat",
            json=run_fields(client, ch, task["id"]),
            headers=ch,
        ).status_code
        == 409
    )


def test_finish_does_everything_in_one_call_and_takes_the_next(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    one = _item(client, alex, todo["id"], "one", assignees=["agent"], priority="high")
    two = _item(client, alex, todo["id"], "two", assignees=["agent"])
    _next(client, ch)

    out = _finish(client, ch, one["id"], outcome="done", note="Shipped")
    assert out["item"]["status"]["kind"] == "done" and out["item"]["agent"] is None
    assert out["next"] is None
    assert _next(client, ch)["item"]["id"] == two["id"]

    comments = client.get(f"{API}/items/{one['id']}/comments", headers=alex).json()
    assert comments[-1]["body_md"].endswith("Shipped")
    assert client.get(f"{API}/work", headers=ch).json()[0]["item_id"] == two["id"]


def test_finish_hand_to_passes_the_task_to_another_agent(client, alex, todo, make_account):
    _, ch = make_account("agent")
    _, dh = make_account("agent2")
    task = _item(client, alex, todo["id"], "write then review", assignees=["agent"])
    _next(client, ch)
    out = _finish(client, ch, task["id"], outcome="release", hand_to="agent2", note="Your turn")
    assert [a["handle"] for a in out["item"]["assignees"]] == ["agent2"]
    comments = client.get(f"{API}/items/{task['id']}/comments", headers=alex).json()
    assert comments[-1]["body_md"] == "**Handed to @agent2** · Your turn"
    got = _next(client, dh)
    assert got["item"]["id"] == task["id"] and got["why"] == "assigned"


def test_finish_review_and_blocked(client, alex, todo, make_account):
    _, ch = make_account("agent")
    a = _item(client, alex, todo["id"], "a", assignees=["agent"], priority="high")
    b = _item(client, alex, todo["id"], "b", assignees=["agent"])
    _next(client, ch)
    out = _finish(client, ch, a["id"], outcome="review", note="PR is up")
    assert out["item"]["agent"]["state"] == "review"
    _next(client, ch)
    out = _finish(client, ch, b["id"], outcome="blocked", waiting_on="alex", note="Which one?")
    assert out["item"]["agent"]["state"] == "blocked"
    alex_id = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    assert out["item"]["waiting_on_account_id"] == alex_id
    assert client.get(f"{API}/work", headers=ch).json() == []
    assert _next(client, ch)["item"] is None


def test_expired_lease_is_quarantined_by_the_scheduler(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    task = _item(client, alex, todo["id"], "pool task")
    _next(client, ch, pool=True)
    with slate_db.SessionLocal() as s:
        assert work.expire_leases(s, utcnow()) == 0
        assert work.expire_leases(s, utcnow() + timedelta(hours=1)) == 1
    item = client.get(f"{API}/items/{task['id']}", headers=alex).json()
    assert item["agent"]["state"] == "blocked" and item["assignees"][0]["id"] == agent["id"]
    events = client.get(f"{API}/items/{task['id']}/events", headers=alex).json()
    released = [e for e in events if e["type"] == "item.reported"][-1]
    assert released["after"]["reason"] == "lease_expired"
    assert released["after"]["by"] == agent["id"]
    assert client.get(f"{API}/work", headers=ch).json()[0]["item_id"] == task["id"]
    assert released["after"]["reconciliation_required"] is True
    assert _next(client, ch, pool=True)["item"] is None


@pytest.fixture
def file_db(tmp_path):
    """A file database for a test with two threads writing at once. The in-memory test database
    is one shared connection (StaticPool), so the long-poll's rollback could undo the other
    thread's half-written insert (a FOREIGN KEY failure, now and then, under full-suite load).
    Production has a connection per thread, as here."""
    from slate.db import Base, SessionLocal, engine, make_engine

    file_engine = make_engine(f"sqlite+pysqlite:///{(tmp_path / 'work.db').as_posix()}")
    Base.metadata.create_all(file_engine)
    SessionLocal.configure(bind=file_engine)
    try:
        yield file_engine
    finally:
        SessionLocal.configure(bind=engine)
        file_engine.dispose()


def test_next_waits_for_work_to_appear(file_db, client, alex, todo, make_account):
    import threading
    import time

    _, ch = make_account("agent")

    def later():
        time.sleep(0.3)
        _item(client, alex, todo["id"], "fresh", assignees=["agent"])

    t = threading.Thread(target=later)
    t.start()
    start = time.monotonic()
    got = _next(client, ch, wait=5)
    t.join()
    assert got["item"]["title"] == "fresh"
    assert time.monotonic() - start < 4


def test_pool_respects_token_list_limits(client, alex, todo, make_account):
    agent, _ = make_account("agent")
    other = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "Other"}, headers=alex
    ).json()
    _item(client, alex, other["id"], "elsewhere", priority="urgent")
    here = _item(client, alex, todo["id"], "here")
    tok = client.post(
        f"{API}/accounts/{agent['id']}/tokens",
        json={"scopes": ["write"], "limit": {"list_ids": [todo["id"]]}},
        headers=alex,
    )
    assert tok.status_code == 201, tok.text
    lh = {"Authorization": f"Bearer {tok.json()['token']}"}
    assert _next(client, lh, pool=True)["item"]["id"] == here["id"]
    assert _next(client, lh, pool=True, resume=False)["item"] is None


def test_a_task_i_reported_blocked_waits_an_hour(client, alex, todo, make_account):
    _, ch = make_account("agent")
    task = _item(client, alex, todo["id"], "flaky", assignees=["agent"])
    _next(client, ch)
    _finish(client, ch, task["id"], outcome="blocked", note="Failed: timeout")
    assert _next(client, ch)["item"] is None
    with slate_db.SessionLocal() as s:  # an hour later (a test-only event covers the edit)
        item = s.get(Item, task["id"])
        with apply_change(s, Actor(item.created_by, Source.api)) as cx:
            item.agent_state_at = utcnow() - timedelta(hours=2)
            cx.record("test.touched", item_id=item.id, covers=[item])
    assert _next(client, ch)["item"]["id"] == task["id"]


def test_a_long_poll_check_in_is_never_answered_304(client, alex, todo, make_account):
    _, ch = make_account("agent")
    first = client.get(f"{API}/checkin", headers=ch)
    since = first.json()["next_since"]
    tagged = client.get(f"{API}/checkin", params={"since": since}, headers=ch)
    etag = tagged.headers["etag"]
    assert (
        client.get(
            f"{API}/checkin", params={"since": since}, headers={**ch, "If-None-Match": etag}
        ).status_code
        == 304
    )
    waited = client.get(
        f"{API}/checkin", params={"since": since, "wait": 1}, headers={**ch, "If-None-Match": etag}
    )
    assert waited.status_code == 200 and "etag" not in waited.headers


def test_an_expired_pool_take_keeps_the_agent_assigned_until_it_is_settled(
    client, alex, todo, make_account, db
):
    """Expiry proves nothing about the remote run, so it changes no assignee: the card waits,
    quarantined, with the agent still on it, until the run is reconciled or a person clears it."""
    _, h = make_account("agent")
    item = _item(client, alex, todo["id"], "Pool")
    got = _next(client, h, pool=True)
    assert got["item"]["id"] == item["id"]
    assert work.expire_leases(db, utcnow() + timedelta(hours=2)) == 1
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert [a["handle"] for a in view["assignees"]] == ["agent"]
    assert view["agent"]["state"] == "blocked"
    held = client.get(f"{API}/work", headers=h).json()
    assert [x["item_id"] for x in held] == [item["id"]]
