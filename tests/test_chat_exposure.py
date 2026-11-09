"""Exact exposure is not a channel-wide acknowledgment."""

from sqlalchemy import select

from slate.models import ChannelRead, Event
from slate.service import chat
from test_chat import API, channels, post


def _row(db, account_id, channel="general"):
    db.expire_all()
    return db.get(ChannelRead, (account_id, channel))


def test_exposures_compact_into_the_cursor_and_the_sparse_list_is_capped(
    client, make_account, db, monkeypatch
):
    """The cursor follows the exposures whenever that loses nothing (every live message by
    someone else below it is exposed), and the exact list keeps at most EXPOSED_KEEP ids: a
    message skipped while that many later ones were read counts as read."""
    monkeypatch.setattr(chat, "EXPOSED_KEEP", 3)
    reader, headers = make_account("reader")
    _, sender = make_account("sender")
    skipped = post(client, sender, "general", "Never looked at")
    msgs = [post(client, sender, "general", f"m{i}") for i in range(5)]
    url = f"{API}/channels/general/read"
    # Exposed above an unread message: exact ids, no cursor move.
    r = client.post(url, json={"message_ids": [m["id"] for m in msgs[:3]]}, headers=headers)
    assert r.status_code == 200 and r.json() == {
        "channel": "general",
        "through": None,
        "changed": True,
    }
    row = _row(db, reader["id"])
    assert row.read_through == "" and row.exposed_ids == [m["id"] for m in msgs[:3]]
    assert channels(client, headers)["general"]["first_unread_id"] == skipped["id"]
    # One more than the cap: the oldest exposure becomes the cursor and covers the skipped one.
    r = client.post(url, json={"message_ids": [msgs[3]["id"]]}, headers=headers)
    assert r.json()["through"] == msgs[0]["id"]
    row = _row(db, reader["id"])
    assert row.read_through == msgs[0]["id"] and row.exposed_ids == [m["id"] for m in msgs[1:4]]
    state = channels(client, headers)["general"]
    assert state["unread"] == 1 and state["first_unread_id"] == msgs[4]["id"]
    # Nothing unread below the newest exposure: the cursor takes it all, the list empties.
    client.post(url, json={"message_ids": [msgs[4]["id"]]}, headers=headers)
    row = _row(db, reader["id"])
    assert row.read_through == msgs[4]["id"] and row.exposed_ids == []
    assert channels(client, headers)["general"]["unread"] == 0
    # Reading out of order keeps the gap exact until it closes.
    m5 = post(client, sender, "general", "m5")
    m6 = post(client, sender, "general", "m6")
    client.post(url, json={"message_ids": [m6["id"]]}, headers=headers)
    row = _row(db, reader["id"])
    assert row.read_through == msgs[4]["id"] and row.exposed_ids == [m6["id"]]
    assert channels(client, headers)["general"]["first_unread_id"] == m5["id"]
    client.post(url, json={"message_ids": [m5["id"]]}, headers=headers)
    row = _row(db, reader["id"])
    assert row.read_through == m6["id"] and row.exposed_ids == []
    events = db.scalars(select(Event).where(Event.type == "channel.read")).all()
    assert [e.after_json.get("through") for e in events] == [
        None,
        msgs[0]["id"],
        msgs[4]["id"],
        None,
        m6["id"],
    ]


def test_exposure_preserves_older_and_unopened_thread(client, make_account, db):
    reader, headers = make_account("reader")
    _, sender = make_account("sender")
    old = post(client, sender, "general", "Earlier unseen")
    root = post(client, sender, "general", "Visible root")
    reply = post(client, sender, "general", "Unopened reply", parent_id=root["id"])
    latest = post(client, sender, "general", "Visible latest")
    url = f"{API}/channels/general/read"
    result = client.post(url, json={"message_ids": [root["id"], latest["id"]]}, headers=headers)
    assert result.status_code == 200, result.text
    state = channels(client, headers)["general"]
    assert state["unread"] == 2
    assert state["first_unread_id"] == old["id"]
    assert state["unread_threads"] == {root["id"]: 1}
    # Another device's older batch is a union, never a rewind or replacement.
    client.post(url, json={"message_ids": [old["id"]]}, headers=headers)
    state = channels(client, headers)["general"]
    assert state["unread"] == 1
    assert state["first_unread_id"] == reply["id"]
    assert state["first_unread_thread_id"] == root["id"]
    again = client.post(url, json={"message_ids": [root["id"]]}, headers=headers)
    assert again.json()["changed"] is False
    events = db.scalars(select(Event).where(Event.type == "channel.read")).all()
    assert len(events) == 2
    assert all(e.actor_id == reader["id"] and e.subject_id == reader["id"] for e in events)
    client.post(url, json={"message_ids": [reply["id"]]}, headers=headers)
    assert channels(client, headers)["general"]["unread"] == 0


def test_empty_exposure_is_not_read_all_and_wrong_channel_is_rejected(client, make_account, todo):
    _, headers = make_account("reader")
    _, sender = make_account("sender")
    post(client, sender, "general", "Keep unread")
    other = post(client, sender, f"list:{todo['id']}", "Elsewhere")
    url = f"{API}/channels/general/read"
    r = client.post(url, json={"message_ids": []}, headers=headers)
    assert r.status_code == 200 and r.json()["changed"] is False
    assert channels(client, headers)["general"]["unread"] == 1
    assert client.post(url, json={"message_ids": [other["id"]]}, headers=headers).status_code == 422
    client.post(f"{API}/channels/read-all", headers=headers)
    assert channels(client, headers)["general"]["unread"] == 0
