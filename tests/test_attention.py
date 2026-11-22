"""Notification levels, durable holds and escalation."""

from datetime import timedelta

import pytest

from conftest import API
from slate.models import utcnow
from slate.service import push
from test_push import Browser


@pytest.mark.parametrize(
    ("reason", "levels"),
    [
        ("waiting", (3, 3)),
        ("mention", (3, 2)),
        ("chat", (3, 1)),
        ("reply", (2, 1)),
        ("comment", (2, 1)),
        ("assigned", (2, 1)),
        ("reminder", (3, 3)),
        ("due", (2, 2)),
        ("failed", (2, 2)),
        ("stalled", (2, 2)),
        ("unblocked", (2, 2)),
        ("nudge", (2, 2)),
        ("access", (2, 2)),
        ("health", (3, 3)),
        ("digest", (1, 1)),
        ("watching", (0, 0)),
        ("status", (0, 0)),
        ("update", (0, 0)),
        ("other", (0, 0)),
    ],
)
def test_base_levels(reason, levels):
    from slate.service.attention import loudness

    for kind, expected in zip(("person", "agent"), levels, strict=True):
        assert loudness(reason, kind) == expected
        assert loudness(reason, kind, urgent=True) == min(4, expected + 1)


@pytest.mark.parametrize("reason", ["no_next_step", "decide"])
def test_planning_attention_never_pushes(reason):
    from slate.service.attention import loudness

    assert loudness(reason, "person") == loudness(reason, "agent", urgent=True) == 0


def test_inbox_has_a_level_and_chat_climbs_once(client, alex, db, make_account):
    from slate.service.attention import escalate

    _, agent = make_account("my-agent", kind="agent")
    client.patch(f"{API}/me/prefs", json={"escalate_after_minutes": 1}, headers=agent)
    client.post(f"{API}/channels/general/messages", json={"body_md": "Please review"}, headers=alex)
    entries = client.get(f"{API}/inbox", headers=agent, params={"reason": "chat"}).json()
    original = next(e for e in entries if e["reason"] == "chat")
    assert original["loudness"] == 3
    now = utcnow() + timedelta(minutes=2)
    assert escalate(db, now) == 1
    assert escalate(db, now) == 0
    entries = client.get(f"{API}/inbox", headers=agent).json()
    climbed = next(e for e in entries if e["event"]["type"] == "inbox.escalated")
    assert climbed["loudness"] == 4 and climbed["reason"] == "chat"
    assert climbed["event"]["after"]["event_id"] == original["event"]["id"]


def test_off_switch_applies_even_to_an_urgent_mention(client, alex, db, todo, make_account):
    _, agent = make_account("my-agent", kind="agent")
    client.post(f"{API}/push/subscriptions", json=Browser().body(), headers=alex)
    client.patch(f"{API}/me/prefs", json={"push_reasons": ["watching"]}, headers=alex)
    notes = []

    def send(*args):
        notes.append(args[-1])
        return 201, ""

    push.process(db, send)
    item = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Review", "priority": "urgent"},
        headers=alex,
    ).json()
    client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": "@alex Review"}, headers=agent
    )
    assert push.process(db, send) == 0


def _delivery(client, db, headers):
    assert (
        client.post(f"{API}/push/subscriptions", json=Browser().body(), headers=headers).status_code
        == 201
    )
    notes = []

    def send(*args):
        notes.append(args[-1])
        return 201, ""

    push.process(db, send)
    return notes, send


def test_agent_turn_is_one_popup_and_ask_is_immediate(client, db, alex, todo, make_account):
    _, agent = make_account("my-agent", kind="agent")
    notes, send = _delivery(client, db, alex)
    card = client.post(
        f"{API}/items",
        json={
            "list_id": todo["id"],
            "title": "Review",
            "priority": "urgent",
            "assignees": ["my-agent"],
        },
        headers=alex,
    ).json()
    iid = card["id"]
    response = client.post(f"{API}/work/next", json={"item_id": iid}, headers=agent)
    assert response.status_code == 200, response.text
    run = response.json()["lease"]["run_id"]
    for txt in ("First update", "@alex Latest update", "Third update"):
        assert (
            client.post(
                f"{API}/items/{iid}/comments", json={"body_md": txt}, headers=agent
            ).status_code
            == 201
        )
    assert push.process(db, send) == 0, notes
    route = f"{API}/inbox/alerts"
    assert client.get(route, headers=alex, params={"after": "0"}).json() == []
    response = client.post(
        f"{API}/items/{iid}/report",
        headers=agent,
        json={"run_id": run, "state": "blocked", "waiting_on": "alex", "question": "Which one?"},
    )
    assert response.status_code == 200, response.text
    assert push.process(db, send) == 1
    assert notes[0]["title"] == "Which one?" and notes[0]["loudness"] == 4
    [question] = client.get(route, headers=alex, params={"after": "0"}).json()
    assert question["title"] == "Which one?"
    response = client.post(
        f"{API}/work/{iid}/finish",
        headers=agent,
        json={"run_id": run, "outcome": "review", "status": "Check the labels", "comment": False},
    )
    assert response.status_code == 200, response.text
    assert push.process(db, send) == 1
    assert notes[1]["loudness"] == 3 and "Latest update" in notes[1]["body"]
    [alert] = client.get(route, headers=alex, params={"after": question["cursor"]}).json()
    assert (alert["title"], alert["body"]) == (notes[1]["title"], notes[1]["body"])


def test_quiet_pushes_summarize_and_urgent_passes(client, db, alex, todo, make_account):
    from slate.dates import LOCAL
    from slate.service import email

    _, person = make_account("alex-reviewer", kind="person")
    now = utcnow()
    local = now.astimezone(LOCAL)
    begin = (local - timedelta(minutes=5)).strftime("%H:%M")
    end = (local + timedelta(minutes=5)).strftime("%H:%M")
    assert (
        client.patch(
            f"{API}/me/prefs",
            headers=alex,
            json={"quiet_hours": {"from": begin, "to": end}, "email_to": "alex@example.com"},
        ).status_code
        == 200
    )
    notes, send = _delivery(client, db, alex)
    for priority in ("normal", "normal", "urgent"):
        item = client.post(
            f"{API}/items",
            headers=alex,
            json={
                "list_id": todo["id"],
                "title": priority,
                "priority": priority,
            },
        ).json()
        assert (
            client.post(
                f"{API}/items/{item['id']}/comments",
                headers=person,
                json={"body_md": "@alex Please review"},
            ).status_code
            == 201
        )
    assert push.process(db, send) == 1
    assert notes[0]["loudness"] == 4 and not notes[0]["silent"]
    route = f"{API}/inbox/alerts"
    [urgent] = client.get(route, headers=alex, params={"after": "0"}).json()
    assert urgent["loudness"] == 4
    mail = []
    assert email.process(db, lambda *args: mail.append(args), now=now + timedelta(minutes=4)) == 0
    assert push.process(db, send, now=now + timedelta(minutes=6)) == 1
    assert notes[1]["title"] == "2 things while you were away"
    assert notes[1]["loudness"] == 3
    [summary] = client.get(route, headers=alex, params={"after": urgent["cursor"]}).json()
    assert summary["entry"] is None and summary["item_id"] is None
    assert (summary["title"], summary["body"]) == (notes[1]["title"], notes[1]["body"])
    assert email.process(db, lambda *args: mail.append(args), now=now + timedelta(minutes=6)) == 1
    assert summary["body"] in mail[0][2]
    assert push.process(db, send, now=now + timedelta(minutes=7)) == 0


@pytest.mark.parametrize("answered", [True, False])
def test_waiting_climbs_only_when_question_is_open(client, db, alex, todo, make_account, answered):
    from slate.service.attention import escalate

    _, agent = make_account("my-agent", kind="agent")
    client.patch(f"{API}/me/prefs", json={"escalate_after_minutes": 1}, headers=alex)
    card = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Decide", "assignees": ["my-agent"]},
        headers=alex,
    ).json()
    iid = card["id"]
    response = client.post(
        f"{API}/items/{iid}/report",
        headers=agent,
        json={"state": "blocked", "waiting_on": "alex", "question": "Which one?"},
    )
    assert response.status_code == 200, response.text
    if answered:
        client.post(f"{API}/items/{iid}/comments", json={"body_md": "The first one"}, headers=alex)
    now = utcnow() + timedelta(minutes=2)
    assert escalate(db, now) == int(not answered)
    assert escalate(db, now) == 0


def test_due_falls_back_to_creator_and_owner(client, db, alex, todo, make_account):
    from slate.service.due import emit_due_events

    _, agent = make_account("my-agent", kind="agent")
    _, person = make_account("alex-reviewer", kind="person")
    when = utcnow() - timedelta(minutes=1)
    for creator in (person, agent):
        response = client.post(
            f"{API}/items",
            headers=creator,
            json={
                "list_id": todo["id"],
                "title": "Pay invoice",
                "due_at": when.isoformat(),
                "assignees": ["my-agent"],
            },
        )
        assert response.status_code == 201, response.text
    emit_due_events(db, now=utcnow())
    for recipient in (alex, person):
        entries = client.get(f"{API}/inbox", headers=recipient, params={"reason": "due"}).json()
        assert len(entries) == 1 and entries[0]["loudness"] == 2


def test_email_floor(client, db, alex, todo, make_account):
    from slate.service import email

    _, agent = make_account("my-agent", kind="agent")
    _, person = make_account("alex-reviewer", kind="person")
    assert (
        client.patch(
            f"{API}/me/prefs",
            headers=alex,
            json={"email_to": "alex@example.com", "email_reasons": ["mention"]},
        ).status_code
        == 200
    )
    sent = []
    email.process(db, lambda *args: sent.append(args))
    card = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": todo["id"],
            "title": "Review",
        },
    ).json()
    for headers, body in ((agent, "@alex from agent"), (person, "@alex from person")):
        assert (
            client.post(
                f"{API}/items/{card['id']}/comments", headers=headers, json={"body_md": body}
            ).status_code
            == 201
        )
    assert email.process(db, lambda *args: sent.append(args), utcnow() + timedelta(minutes=6)) == 1
    assert "from person" in sent[0][2] and "from agent" not in sent[0][2]


@pytest.mark.parametrize("reference", [False, True])
def test_digest_and_health_list_open_questions(client, db, alex, todo, make_account, reference):
    from slate.models import Account
    from slate.service import health

    _, agent = make_account("my-agent", kind="agent")
    for question in ("Which file?", "Which format?"):
        card = client.post(
            f"{API}/items",
            headers=alex,
            json={"list_id": todo["id"], "title": "Review", "assignees": ["my-agent"]},
        ).json()
        response = client.post(
            f"{API}/items/{card['id']}/report",
            headers=agent,
            json={"state": "blocked", "waiting_on": "alex", "question": question},
        )
        assert response.status_code == 200, response.text
    client.patch(
        f"{API}/lists/{todo['id']}", headers=alex, json={"reference": reference}
    ).raise_for_status()
    digest = client.get(f"{API}/me/digest", headers=alex).json()
    assert [q["question"] for q in digest["questions"]] == (
        [] if reference else ["Which file?", "Which format?"]
    )
    assert all(q["age_minutes"] >= 0 for q in digest["questions"])
    viewer = db.get(Account, client.get(f"{API}/me", headers=alex).json()["account"]["id"])
    report = health.report(db, viewer=viewer, now=utcnow() + timedelta(days=2))
    checks = {c["key"]: c["detail"] for c in report["checks"]}
    assert checks["questions_open_24h"] == ("0" if reference else "2")
    assert checks["push_failed_24h"] == checks["push_removed_7d"] == "0"


def test_released_alert_cursor_skips_backlog_and_matches_push(client, alex, db, todo, make_account):
    _, person = make_account("alex-reviewer", kind="person")
    notes, send = _delivery(client, db, alex)
    route = f"{API}/inbox/alerts"
    first = client.get(route, headers=alex)
    assert first.status_code == 200, first.text
    assert first.json() == []
    assert client.get(route, headers=alex, params={"after": "invalid"}).status_code == 422
    cursor = first.headers["X-Slate-Cursor"]

    card = client.post(
        f"{API}/items",
        headers=alex,
        json={
            "list_id": todo["id"],
            "title": "Review",
        },
    ).json()
    for body in ("@alex One", "@alex Two"):
        client.post(f"{API}/items/{card['id']}/comments", headers=person, json={"body_md": body})
    assert push.process(db, send) == 2
    page = client.get(route, headers=alex, params={"after": cursor, "limit": 1})
    assert page.status_code == 200
    [first_alert] = page.json()
    more = client.get(route, headers=alex, params={"after": first_alert["cursor"]})
    [second_alert] = more.json()
    assert cursor < first_alert["cursor"] < second_alert["cursor"]
    assert [(a["title"], a["body"], a["loudness"]) for a in (first_alert, second_alert)] == [
        (n["title"], n["body"], n["loudness"]) for n in notes
    ]
    assert second_alert["entry"]["reason"] == "mention"
    fresh = client.get(route, headers=alex)
    assert fresh.json() == [] and fresh.headers["X-Slate-Cursor"] == second_alert["cursor"]


def test_a_stall_is_its_own_release_after_the_turns_alert(client, db, alex, todo, make_account):
    """The poked run that answered again: its comment is the turn's one pop-up, and the stall
    (recorded after the turn ends) is a release of its own, never folded into it."""
    from slate.service import due

    _, agent = make_account("my-agent", kind="agent")
    notes, send = _delivery(client, db, alex)
    card = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Labels", "assignees": ["my-agent"]},
        headers=alex,
    ).json()
    iid = card["id"]

    def turn(reply, *, comment=None):
        taken = client.post(f"{API}/work/next", json={"item_id": iid}, headers=agent).json()
        if comment:
            r = client.post(f"{API}/items/{iid}/comments", json={"body_md": comment}, headers=agent)
            assert r.status_code == 201, r.text
        r = client.post(
            f"{API}/work/{iid}/finish",
            headers=agent,
            json={"run_id": taken["lease"]["run_id"], "outcome": "answered", "reply": reply},
        )
        assert r.status_code == 200, r.text

    turn("Printed half.")
    push.process(db, send)
    db.expire_all()
    assert due.poke_stalled(db, now=utcnow() + timedelta(hours=4.1)) == 1
    route = f"{API}/inbox/alerts"
    head = client.get(route, headers=alex)
    cursor = head.headers["X-Slate-Cursor"]
    turn("Still half.", comment="@alex the printer jammed")
    push.process(db, send)
    alerts = client.get(route, headers=alex, params={"after": cursor}).json()
    reasons = [a["reason"] for a in alerts]
    assert "stalled" in reasons and len(alerts) == 2, alerts
    stalled = next(a for a in alerts if a["reason"] == "stalled")
    assert stalled["loudness"] == 2
    assert stalled["title"] == "My-Agent stopped without finishing: Labels"
