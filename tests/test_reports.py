"""`url` on agent reports, heartbeats and finish: where the agent is working, shown on the card
as Open, kept while the agent keeps reporting, cleared with the badge."""

from __future__ import annotations

import pytest

from conftest import API


def _item(client, h, list_id, title="Write the report", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _report(client, h, item_id, **body):
    return client.post(f"{API}/items/{item_id}/report", json=body, headers=h)


def _view(client, h, item_id):
    return client.get(f"{API}/items/{item_id}", headers=h).json()


CHAT = "https://chat.example.com/c/123"


def test_report_with_url_puts_it_on_the_badge_and_done_clears_it(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])

    r = _report(client, ch, item["id"], state="working", note="Drafting", url=CHAT)
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["url"] == CHAT

    # A later report without url keeps the link; the event carries it.
    r = _report(client, ch, item["id"], state="working", progress=50)
    assert r.json()["agent"]["url"] == CHAT
    events = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    reported = [e for e in events if e["type"] == "item.reported"]
    assert reported[-1]["after"]["url"] == CHAT

    r = _report(client, ch, item["id"], state="done")
    assert r.json()["agent"] is None
    r = _report(client, ch, item["id"], state="working")
    assert r.json()["agent"]["url"] is None


def test_report_url_must_be_http(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "not a url", "https://"):
        r = _report(client, ch, item["id"], state="working", url=bad)
        assert r.status_code == 422, (bad, r.text)


def test_link_schemes_setting_admits_a_desktop_app_scheme(
    client, alex, todo, make_account, monkeypatch
):
    # A desktop chat app's own scheme (SLATE_LINK_SCHEMES) is accepted for url and thread.url,
    # still needs a host part, and is told to the board on /me so it can trust the link.
    from slate.settings import settings

    monkeypatch.setattr(settings, "link_schemes", ["http", "https", "chatapp"])
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    link = "chatapp://chat/api_0000000001?profile=agent2"
    r = _report(
        client, ch, item["id"], state="working", thread={"ref": "api_0000000001", "url": link}
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["url"] == link
    assert r.json()["agent_threads"] == [
        {"account_id": r.json()["agent"]["by"], "ref": "api_0000000001", "url": link}
    ]
    for bad in ("chatapp:chat", "chatapp:", "javascript:alert(1)"):
        assert _report(client, ch, item["id"], state="working", url=bad).status_code == 422, bad
    assert client.get(f"{API}/me", headers=alex).json()["link_schemes"] == [
        "http",
        "https",
        "chatapp",
    ]

    monkeypatch.setattr(settings, "link_schemes", ["http", "https"])
    assert _report(client, ch, item["id"], state="working", url=link).status_code == 422
    assert client.get(f"{API}/me", headers=alex).json()["link_schemes"] == ["http", "https"]


def test_heartbeat_url_updates_the_badge_live_and_finish_can_set_it(
    client, alex, todo, make_account
):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    got = client.post(f"{API}/work/next", json={"note": "Starting"}, headers=ch).json()
    assert got["item"]["id"] == item["id"]

    r = client.post(
        f"{API}/work/{item['id']}/heartbeat",
        json={"url": CHAT, "run_id": got["lease"]["run_id"]},
        headers=ch,
    )
    assert r.status_code == 200, r.text
    view = _view(client, alex, item["id"])
    assert view["agent"]["url"] == CHAT
    assert view["agent"]["note"] == "Starting"  # a url-only heartbeat keeps the note

    # The same url again is bookkeeping only: no new event.
    before = len(client.get(f"{API}/items/{item['id']}/events", headers=alex).json())
    client.post(
        f"{API}/work/{item['id']}/heartbeat",
        json={"url": CHAT, "run_id": got["lease"]["run_id"]},
        headers=ch,
    )
    after = len(client.get(f"{API}/items/{item['id']}/events", headers=alex).json())
    assert after == before

    pr = "https://review.example.com/x/y/1"
    r = client.post(
        f"{API}/work/{item['id']}/finish",
        json={"outcome": "review", "url": pr, "run_id": got["lease"]["run_id"]},
        headers=ch,
    )
    assert r.status_code == 200, r.text
    assert r.json()["item"]["agent"]["url"] == pr


def test_heartbeat_rejects_a_bad_url(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    got = client.post(f"{API}/work/next", json={}, headers=ch).json()
    r = client.post(
        f"{API}/work/{item['id']}/heartbeat",
        json={"url": "ftp://x/y", "run_id": got["lease"]["run_id"]},
        headers=ch,
    )
    assert r.status_code == 422


def test_agents_roster_carries_the_url(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    _report(client, ch, item["id"], state="working", url=CHAT)
    roster = client.get(f"{API}/agents", headers=alex).json()
    agent = next(a for a in roster if a["account"]["handle"] == "agent")
    assert agent["working"][0]["url"] == CHAT


@pytest.mark.parametrize("scheme", ["javascript", "JavaScript", "data", "vbscript", "blob", "file"])
def test_a_dangerous_link_scheme_is_never_accepted(
    client, alex, todo, make_account, monkeypatch, scheme
):
    from pydantic import ValidationError

    from slate.settings import Settings, settings

    with pytest.raises(ValidationError, match="link_schemes"):
        Settings(link_schemes=["http", "https", scheme])
    # Even if one reaches the live settings some other way, no link with it passes, and the
    # board is never told to trust it.
    monkeypatch.setattr(settings, "link_schemes", ["http", "https", scheme])
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], assignees=["agent"])
    bad = f"{scheme}://%0aalert(1)"
    assert _report(client, ch, item["id"], state="working", url=bad).status_code == 422
    assert client.get(f"{API}/me", headers=alex).json()["link_schemes"] == ["http", "https"]
