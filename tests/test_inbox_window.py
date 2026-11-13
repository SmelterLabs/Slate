"""The inbox is derived from the account's own candidate events, so other people's activity
can never push an entry out of it, and the query reads only those candidates."""

from __future__ import annotations

import pytest
from sqlalchemy import event as sa_event

from slate.models import Event
from slate.service import inbox as inbox_svc

API = "/api/v1"


def post(client, headers, key, body):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def small_window(monkeypatch):
    # A small entry cap stands in for a busy workspace: before the fix, the candidate window was
    # a multiple of MAX_ENTRIES across the whole workspace.
    monkeypatch.setattr(inbox_svc, "MAX_ENTRIES", 5)


@pytest.fixture
def loaded_events():
    seen: list[str] = []

    def on_load(target, _ctx):
        seen.append(target.id)

    sa_event.listen(Event, "load", on_load)
    yield seen
    sa_event.remove(Event, "load", on_load)


def test_mention_survives_any_amount_of_unrelated_activity(
    client, alex, todo, make_account, small_window, loaded_events
):
    _, agent = make_account("my-agent")
    other, other_h = make_account("other-agent")
    key = f"list:{todo['id']}"
    mention = post(client, agent, key, "@alex can you look at this")
    assert client.get(f"{API}/inbox/count", headers=alex).json() == {"unread": 1}

    # Plenty of traffic that is not about alex: chat, comments, assignments, mentions of others.
    for i in range(30):
        post(client, other_h, key, f"status line {i}")
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Not mine"}, headers=other_h
    ).json()
    for i in range(10):
        client.post(
            f"{API}/items/{item['id']}/comments",
            json={"body_md": f"@my-agent note {i}"},
            headers=other_h,
        )
    client.patch(f"{API}/items/{item['id']}", json={"assignees": ["my-agent"]}, headers=other_h)

    loaded_events.clear()
    entries = client.get(f"{API}/inbox", headers=alex).json()
    assert [e["event"]["subject_id"] for e in entries] == [mention["id"]]
    assert client.get(f"{API}/inbox/count", headers=alex).json() == {"unread": 1}
    # Only the account's own candidates reach Python, never the workspace's traffic.
    assert len(loaded_events) <= 4, len(loaded_events)
    assert other["id"]  # the other agent exists and posted


def test_more_own_entries_than_one_page_are_all_listed(
    client, alex, todo, make_account, small_window
):
    _, agent = make_account("my-agent")
    key = f"list:{todo['id']}"
    for i in range(12):
        post(client, agent, key, f"@alex ping {i}")
    # limit is capped by MAX_ENTRIES (5 here); the count is capped the same way.
    got = client.get(f"{API}/inbox", headers=alex).json()
    assert len(got) == 5
    assert all(e["reason"] == "mention" for e in got)
    assert client.get(f"{API}/inbox/count", headers=alex).json() == {"unread": 5}


def test_rejected_candidates_do_not_hide_older_entries(
    client, alex, todo, make_account, monkeypatch
):
    """Candidates the Python judge refuses (position changes on a followed item) are paged
    past, so an older real entry is still found."""
    monkeypatch.setattr(inbox_svc, "PAGE", 3)
    _, agent = make_account("my-agent")
    key = f"list:{todo['id']}"
    mention = post(client, agent, key, "@alex old news")
    item = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Mine", "assignees": ["alex"]},
        headers=agent,
    ).json()
    for i in range(8):
        client.patch(f"{API}/items/{item['id']}", json={"position": 1000 + i}, headers=agent)
    ids = [e["event"]["subject_id"] for e in client.get(f"{API}/inbox", headers=alex).json()]
    assert mention["id"] in ids
