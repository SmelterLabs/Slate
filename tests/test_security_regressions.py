"""Security regressions: owner protection, automation and webhook permissions, cache
boundaries, recurrence limits, private webhook targets and push endpoints."""

from __future__ import annotations

import pytest

API = "/api/v1"


def _post(client, path, body, h, expect=(200, 201)):
    r = client.post(f"{API}{path}", json=body, headers=h)
    assert r.status_code in expect, r.text
    return r.json()


@pytest.fixture
def world(client, alex):
    personal = _post(client, "/sections", {"name": "Personal", "audience": "personal"}, alex)
    private = _post(client, "/sections", {"name": "Secret", "audience": "work"}, alex)
    todo = _post(client, "/lists", {"section_id": personal["id"], "name": "Todo"}, alex)
    vault = _post(client, "/lists", {"section_id": private["id"], "name": "Vault"}, alex)
    r = client.patch(f"{API}/lists/{vault['id']}", json={"private": True}, headers=alex)
    assert r.status_code == 200, r.text
    milk = _post(client, "/items", {"list_id": todo["id"], "title": "Buy milk"}, alex)
    secret = _post(client, "/items", {"list_id": vault["id"], "title": "Secret plan"}, alex)

    def account(handle, role, scopes=("write",), limit=None):
        a = _post(
            client,
            "/accounts",
            {"handle": handle, "display_name": handle.title(), "kind": "person", "role": role},
            alex,
        )
        body = {"scopes": list(scopes)}
        if limit:
            body["limit"] = limit
        tok = _post(client, f"/accounts/{a['id']}/tokens", body, alex)
        return a, {"Authorization": f"Bearer {tok['token']}"}

    def share(account_id, kind, scope_id, level):
        r = client.put(
            f"{API}/shares",
            json={
                "account_id": account_id,
                "scope_kind": kind,
                "scope_id": scope_id,
                "level": level,
            },
            headers=alex,
        )
        assert r.status_code == 200, r.text

    return {
        "todo": todo,
        "vault": vault,
        "milk": milk,
        "secret": secret,
        "account": account,
        "share": share,
    }


# --- An admin cannot act as the owner ------------------------------------------------------


def test_admin_cannot_mint_a_token_on_the_owner_account(client, alex, world):
    _, admin = world["account"]("al", "admin", scopes=("admin",))
    owner_id = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    r = client.post(f"{API}/accounts/{owner_id}/tokens", json={"scopes": ["admin"]}, headers=admin)
    assert r.status_code == 403, r.text
    # The owner still can, and an admin can still mint for a member.
    assert (
        client.post(
            f"{API}/accounts/{owner_id}/tokens", json={"scopes": ["read"]}, headers=alex
        ).status_code
        == 201
    )
    member, _ = world["account"]("member", "member")
    assert (
        client.post(
            f"{API}/accounts/{member['id']}/tokens", json={"scopes": ["write"]}, headers=admin
        ).status_code
        == 201
    )


def test_admin_cannot_disable_or_lock_out_the_owner(client, alex, world):
    _, admin = world["account"]("al", "admin", scopes=("admin",))
    me = client.get(f"{API}/me", headers=alex).json()
    owner_id, owner_token = me["account"]["id"], me["token_id"]
    r = client.patch(f"{API}/accounts/{owner_id}", json={"disabled": True}, headers=admin)
    assert r.status_code == 403, r.text
    r = client.delete(f"{API}/tokens/{owner_token}", headers=admin)
    assert r.status_code == 403, r.text
    assert client.get(f"{API}/me", headers=alex).status_code == 200
    # Renaming the owner is harmless and stays allowed; the owner may still revoke own tokens.
    assert (
        client.patch(
            f"{API}/accounts/{owner_id}", json={"display_name": "C"}, headers=admin
        ).status_code
        == 200
    )


# --- Automations follow the permission model -----------------------------------------------


def _rule(client, h, **extra):
    body = {
        "name": "leak",
        "trigger": {"type": "task_created"},
        "actions": [{"type": "add_tag", "tag": "seen"}],
        **extra,
    }
    return client.post(f"{API}/automations", json=body, headers=h)


def test_restricted_caller_cannot_create_a_workspace_wide_automation(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    world["share"](guest["id"], "list", world["todo"]["id"], "edit")
    # Whole workspace: would fire on the private list too.
    assert _rule(client, gh).status_code == 403
    # A list the guest cannot see: absent, not forbidden.
    assert _rule(client, gh, list_id=world["vault"]["id"]).status_code == 404
    # Her own list: fine.
    r = _rule(client, gh, list_id=world["todo"]["id"])
    assert r.status_code == 201, r.text
    # And the guest cannot widen it afterwards.
    r = client.patch(f"{API}/automations/{r.json()['id']}", json={"list_id": None}, headers=gh)
    assert r.status_code == 403, r.text


def test_comment_level_caller_cannot_run_an_automation_on_the_item(client, alex, world):
    # Scoped to the guest's list: a workspace-wide rule would be absent (test_guest_scope.py).
    rule = _rule(client, alex, actions=[{"type": "archive"}], list_id=world["todo"]["id"]).json()
    guest, gh = world["account"]("guest", "guest")
    world["share"](guest["id"], "list", world["todo"]["id"], "comment")
    r = client.post(
        f"{API}/automations/{rule['id']}/run", json={"item_id": world["milk"]["id"]}, headers=gh
    )
    assert r.status_code == 403, r.text
    assert (
        client.get(f"{API}/items/{world['milk']['id']}", headers=alex).json()["archived_at"] is None
    )


# --- The item cache never serves one caller's view to another ------------------------------


def test_cached_views_do_not_cross_access_boundaries(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    world["share"](guest["id"], "list", world["todo"]["id"], "view")
    r = client.post(
        f"{API}/items/{world['secret']['id']}/links",
        json={"kind": "blocks", "item_id": world["milk"]["id"]},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    # The owner reads first (filling the cache with the private blocker), then the guest.
    full = {i["id"]: i for i in client.get(f"{API}/items", headers=alex).json()}
    assert full[world["milk"]["id"]]["blocked_by"] == [world["secret"]["id"]]
    mine = {i["id"]: i for i in client.get(f"{API}/items", headers=gh).json()}
    assert list(mine) == [world["milk"]["id"]]
    assert mine[world["milk"]["id"]]["blocked_by"] == []
    # And the guest's narrower view never replaces the owner's.
    full = {i["id"]: i for i in client.get(f"{API}/items", headers=alex).json()}
    assert full[world["milk"]["id"]]["blocked_by"] == [world["secret"]["id"]]


# --- Webhooks and crypto -------------------------------------------------------------------


def test_limited_token_cannot_subscribe_a_webhook(client, alex, world):
    member, _ = world["account"]("member", "member")
    tok = _post(
        client,
        f"/accounts/{member['id']}/tokens",
        {"scopes": ["write"], "limit": {"list_ids": [world["todo"]["id"]]}},
        alex,
    )
    narrow = {"Authorization": f"Bearer {tok['token']}"}
    body = {"url": "https://rx.example.com/x", "events": ["*"]}
    assert client.post(f"{API}/webhooks", json=body, headers=narrow).status_code == 403
    _, wide = world["account"]("mia", "member")
    assert client.post(f"{API}/webhooks", json=body, headers=wide).status_code == 201


def test_disabled_account_webhook_hears_nothing(client, alex, world, db):
    from sqlalchemy import select

    from slate import notify
    from slate.models import Delivery

    member, mh = world["account"]("member", "member")
    hook = _post(client, "/webhooks", {"url": "https://rx.example.com/x", "events": ["*"]}, mh)
    d = notify.Dispatcher()
    d.start()
    _post(client, "/items", {"list_id": world["todo"]["id"], "title": "Before"}, alex)
    assert d.fan_out_once() >= 1
    r = client.patch(f"{API}/accounts/{member['id']}", json={"disabled": True}, headers=alex)
    assert r.status_code == 200, r.text
    _post(client, "/items", {"list_id": world["todo"]["id"], "title": "After"}, alex)
    d.fan_out_once()
    bodies = [
        row.body for row in db.scalars(select(Delivery).where(Delivery.webhook_id == hook["id"]))
    ]
    assert any("Before" in b for b in bodies) and not any("After" in b for b in bodies)


def test_decrypt_rejects_garbage_with_a_clear_error():
    from slate import crypto

    for blob in ("v1:not-base64!!", "v1:QUJD", "v1:"):
        with pytest.raises(crypto.SecretKeyError):
            crypto.decrypt(blob, aad="x")


def _delivery_world(db):
    from slate.models import Source
    from slate.service import accounts, lists, sections, webhooks
    from slate.service.core import Actor

    alex, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=alex.id, source=Source.api)
    section = sections.create_section(db, actor, name="P", audience="personal")
    todo = lists.create_list(db, actor, section_id=section.id, name="Todo")
    hook, _ = webhooks.create_webhook(db, actor, url="https://rx.example.com/x", events=["*"])
    return actor, todo, hook


def _one_attempt(db, monkeypatch, rx):
    import httpx

    from slate import notify
    from slate.models import utcnow
    from slate.service import items

    monkeypatch.setattr(notify, "transport", httpx.MockTransport(rx))
    d = notify.Dispatcher()
    d.start()
    actor, todo, _ = _delivery_world(db)
    items.create_item(db, actor, list_id=todo.id, title="x")
    assert d.fan_out_once() >= 1
    ids = d.claim(1)
    assert ids
    return notify.attempt(ids[0], clock=utcnow)


def test_webhook_attempt_reads_a_bounded_amount_of_the_response(monkeypatch, db):
    import httpx

    from slate import notify

    pulled = {"chunks": 0}

    def endless():
        while True:
            pulled["chunks"] += 1
            yield b"x" * 4096

    row = _one_attempt(db, monkeypatch, lambda r: httpx.Response(500, content=endless()))
    assert row.status_code == 500 and row.error.startswith("HTTP 500")
    assert len(row.error) < 400
    assert pulled["chunks"] <= notify.RESPONSE_CAP_BYTES // 4096 + 1


def test_webhook_attempt_records_errors_that_are_not_httpx_errors(monkeypatch, db):
    def boom(request):
        raise UnicodeError("label too long")  # what idna raises for a bad hostname

    row = _one_attempt(db, monkeypatch, boom)
    assert row.state != "sending", row.state
    assert "UnicodeError" in (row.error or "")


# --- Recurrence rules that hang or flood, and a rule moved into a list ---------------------


def _rule_post(client, h, todo, **fields):
    return client.post(f"{API}/recurrences", json={"list_id": todo["id"], **fields}, headers=h)


@pytest.mark.parametrize(
    "rrule",
    [
        "FREQ=DAILY;INTERVAL=0",  # dateutil yields the same instant for ever
        "FREQ=MONTHLY;INTERVAL=0",
        "FREQ=DAILY;BYHOUR=0,1,2,3;BYMINUTE=0,15,30,45",  # sub-daily through the back door
        "FREQ=DAILY;BYSECOND=0,30",
        "FREQ=DAILY;BYHOUR=9,17",
    ],
)
def test_rrules_that_loop_or_flood_are_refused(client, alex, world, rrule):
    r = _rule_post(client, alex, world["todo"], title_template="x", rrule=rrule)
    assert r.status_code == 422, r.text
    r = client.get(
        f"{API}/recurrences/preview",
        params={"rrule": rrule, "trigger": "on_done"},
        headers=alex,
    )
    assert r.status_code == 422, r.text


def test_one_byhour_is_still_fine(client, alex, world):
    r = _rule_post(client, alex, world["todo"], title_template="x", rrule="FREQ=DAILY;BYHOUR=9")
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("dtstart", ["1900-01-01", "0001-01-02", "9999-12-31T23:00:00"])
def test_dtstart_out_of_range_is_a_422_not_a_hang_or_a_500(client, alex, world, dtstart):
    r = _rule_post(
        client, alex, world["todo"], title_template="x", rrule="FREQ=DAILY", dtstart=dtstart
    )
    assert r.status_code == 422, r.text
    r = client.get(
        f"{API}/recurrences/preview",
        params={"rrule": "FREQ=DAILY", "dtstart": dtstart},
        headers=alex,
    )
    assert r.status_code == 422, r.text


def test_occurrences_stop_when_a_rule_stops_advancing():
    from datetime import UTC, datetime
    from itertools import islice

    from slate.models import Recurrence
    from slate.service.recurrences import occurrences

    rule = Recurrence(
        rrule="FREQ=DAILY;INTERVAL=0",  # past validation (an old row, say)
        dtstart=datetime(2026, 1, 1, tzinfo=UTC),
        timezone="America/Chicago",
        skip_weekends=True,
    )
    got = list(islice(occurrences(rule, datetime(2026, 1, 1, tzinfo=UTC)), 10))
    assert len(got) <= 1


def test_a_tick_spawns_at_most_a_bounded_number_per_rule(client, alex, world):
    from slate.service import recurrences

    rule = _rule_post(
        client, alex, world["todo"], title_template="{iso_date}", rrule="FREQ=DAILY", lead_days=366
    ).json()
    first = client.post(f"{API}/recurrences/{rule['id']}/run-now", headers=alex).json()
    assert len(first["spawned"]) == recurrences.MAX_SPAWN_PER_TICK
    second = client.post(f"{API}/recurrences/{rule['id']}/run-now", headers=alex).json()
    assert len(second["spawned"]) == recurrences.MAX_SPAWN_PER_TICK
    dates = sorted(i["occurrence_at"] for i in first["spawned"] + second["spawned"])
    assert len(set(dates)) == 2 * recurrences.MAX_SPAWN_PER_TICK  # no gap, no repeat


def test_a_rule_cannot_be_moved_into_a_list_the_caller_only_views(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    world["share"](guest["id"], "list", world["todo"]["id"], "edit")
    world["share"](guest["id"], "list", world["vault"]["id"], "view")
    rule = _rule_post(client, gh, world["todo"], title_template="x", rrule="FREQ=DAILY").json()
    r = client.patch(
        f"{API}/recurrences/{rule['id']}", json={"list_id": world["vault"]["id"]}, headers=gh
    )
    assert r.status_code == 403, r.text


def test_attachment_content_type_must_be_a_media_type(client, alex, world):
    import base64

    r = client.post(
        f"{API}/items/{world['milk']['id']}/attachments/base64",
        json={
            "filename": "x.txt",
            "content_type": "text/html\r\nX-Injected: 1",
            "content_base64": base64.b64encode(b"hi").decode(),
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    assert r.json()["content_type"] == "text/plain"


# --- Private targets, delivery errors, push endpoints, the board's CSP, lead windows --------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9000/hook",
        "https://10.0.0.5/hook",
        "https://homeserver/hook",
        "https://x.local/h",
    ],
)
def test_private_webhook_targets_are_refused_unless_allowed(client, alex, world, url, monkeypatch):
    from slate.settings import settings

    body = {"url": url, "events": ["*"]}
    r = client.post(f"{API}/webhooks", json=body, headers=alex)
    assert r.status_code == 422 and "private address" in r.json()["detail"], r.text
    rule = {
        "name": "x",
        "trigger": {"type": "task_created"},
        "actions": [{"type": "webhook", "url": url}],
    }
    assert client.post(f"{API}/automations", json=rule, headers=alex).status_code == 422
    monkeypatch.setattr(settings, "webhook_private_targets", True)
    assert client.post(f"{API}/webhooks", json=body, headers=alex).status_code == 201


def test_a_public_name_that_resolves_to_a_private_address_is_refused_at_send_time(monkeypatch, db):
    import socket

    from slate import automator

    def private(host, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", private)
    row = _one_attempt(db, monkeypatch, lambda r: (_ for _ in ()).throw(AssertionError("sent")))
    assert row.state == "failed" and "private address" in row.error  # final: no retry
    ok, detail = automator.send("https://rx.example.com/x", {"automation": {"id": "a"}}, "s")
    assert ok is False and "private address" in detail


def test_delivery_errors_never_echo_the_receivers_answer(monkeypatch, db):
    import httpx

    row = _one_attempt(db, monkeypatch, lambda r: httpx.Response(500, text="internal secret 42"))
    assert row.error == "HTTP 500"


def test_db_path_is_only_for_authenticated_callers(client, alex):
    assert "db_path" not in client.get("/healthz").json()
    assert client.get(f"{API}/stats", headers=alex).json()["db_path"] == ":memory:"


def test_push_endpoints_must_be_a_known_push_service(client, alex, monkeypatch):
    from slate.service import push
    from slate.settings import Settings, settings

    monkeypatch.setattr(
        settings, "push_endpoint_hosts", Settings.model_fields["push_endpoint_hosts"].default
    )
    assert push.endpoint_allowed("fcm.googleapis.com")
    assert push.endpoint_allowed("web.push.apple.com")
    assert push.endpoint_allowed("updates.push.services.mozilla.com")
    assert not push.endpoint_allowed("push.example.com")
    assert not push.endpoint_allowed("evil.googleapis.com")
    body = {
        "endpoint": "https://push.example.com/send/abc",
        "keys": {"p256dh": "BA" + "A" * 85, "auth": "A" * 22},
    }
    r = client.post(f"{API}/push/subscriptions", json=body, headers=alex)
    assert r.status_code == 422 and "SLATE_PUSH_ENDPOINT_HOSTS" in r.json()["detail"]
    monkeypatch.setattr(settings, "push_endpoint_hosts", ["*"])
    assert push.endpoint_allowed("anything.example")


def test_board_shell_carries_a_content_security_policy(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from slate.app import create_app
    from slate.settings import settings

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/index-abc.js"></script>'
    )
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    c = TestClient(create_app())
    shell = c.get("/")
    csp = shell.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "frame-src blob:" in csp  # the PDF viewers frame a fetched blob; nothing external
    assert shell.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in c.get("/assets/index-abc.js").headers


def test_lead_window_counts_local_days_across_a_clock_change(client, alex, world, db):
    from datetime import UTC, datetime

    from slate.models import Source
    from slate.service import recurrences
    from slate.service.core import Actor

    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    actor = Actor(account_id=me, source=Source.api)
    # 00:51 Chicago on the Saturday before clocks fall back; the window must reach Monday.
    now = datetime(2026, 10, 31, 5, 51, tzinfo=UTC)
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=world["todo"]["id"],
        title_template="{iso_date}",
        rrule="FREQ=DAILY",
        dtstart="2026-10-31",
        lead_days=2,
        now=now,
    )
    spawned = recurrences.run_now(db, actor, rule.id, now=now)
    assert sorted(i.title for i in spawned) == ["2026-10-31", "2026-11-01", "2026-11-02"]


# --- `slate check-targets`: what a deploy must know before private targets go off -------------


def test_check_targets_lists_private_receivers(client, alex, world, monkeypatch, capsys):
    from slate import cli
    from slate.settings import settings

    assert cli.main(["check-targets"]) == 0
    assert "No webhook" in capsys.readouterr().out
    monkeypatch.setattr(settings, "webhook_private_targets", True)
    hook = _post(client, "/webhooks", {"url": "http://127.0.0.1:9000/h", "events": ["*"]}, alex)
    _post(client, "/webhooks", {"url": "https://public.example.com/h", "events": ["*"]}, alex)
    rule = _post(
        client,
        "/automations",
        {
            "name": "Tell the runner",
            "trigger": {"type": "task_created"},
            "actions": [{"type": "webhook", "url": "http://homeserver:8642/hook"}],
        },
        alex,
    )
    monkeypatch.setattr(settings, "webhook_private_targets", False)
    assert cli.main(["check-targets"]) == 1
    out = capsys.readouterr().out
    assert "2 target(s)" in out and "REFUSED" in out
    assert f"webhook    {hook['id']}  alex: http://127.0.0.1:9000/h" in out
    assert f"automation {rule['id']}  Tell the runner: http://homeserver:8642/hook" in out
    assert "public.example.com" not in out
    assert "SLATE_WEBHOOK_PRIVATE_TARGETS=true" in out
