"""Comment dots (service/item_reads.py): a card's dot says a comment is new to you since you
last opened it. `mention` beats `human` beats `agent`; your own comments never count; opening
the card (`POST /items/{id}/read`) clears it and writes nothing when nothing was new."""

from __future__ import annotations

from conftest import API


def _item(client, h, list_id, title="Plan the offsite"):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comment(client, h, item_id, body, **kw):
    r = client.post(f"{API}/items/{item_id}/comments", json={"body_md": body, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _dots(client, h):
    r = client.get(f"{API}/me/comment-dots", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _read(client, h, item_id):
    r = client.post(f"{API}/items/{item_id}/read", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_agent_comment_is_a_gray_dot_and_a_person_beats_it(client, alex, todo, make_account):
    _, agent = make_account("agent")
    _, member = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    assert _dots(client, alex) == {}
    _comment(client, agent, item["id"], "Working on it.")
    assert _dots(client, alex) == {item["id"]: "agent"}
    _comment(client, member, item["id"], "Any update?")
    assert _dots(client, alex) == {item["id"]: "human"}
    _comment(client, agent, item["id"], "Still going.")  # a later agent note does not demote
    assert _dots(client, alex) == {item["id"]: "human"}


def test_a_mention_or_an_ask_by_anyone_is_the_strongest(client, alex, todo, make_account):
    _, agent = make_account("agent")
    _, member = make_account("member", kind="person")
    a = _item(client, alex, todo["id"], "A")
    b = _item(client, alex, todo["id"], "B")
    _comment(client, member, a["id"], "Looks good")
    _comment(client, agent, a["id"], "@alex which venue?")
    _comment(client, agent, b["id"], "Need a decision", assignee="alex")
    assert _dots(client, alex) == {a["id"]: "mention", b["id"]: "mention"}
    # Mentioning someone else is no mention of me.
    c = _item(client, alex, todo["id"], "C")
    _comment(client, agent, c["id"], "@member can you check?")
    assert _dots(client, alex)[c["id"]] == "agent"


def test_own_comments_never_count_and_opening_clears(client, alex, todo, make_account):
    _, member = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    _comment(client, alex, item["id"], "Note to self")
    assert _dots(client, alex) == {}
    assert _read(client, alex, item["id"])["read_at"] is None  # nothing new: nothing written
    _comment(client, member, item["id"], "Reply")
    assert _dots(client, alex) == {item["id"]: "human"}
    assert _read(client, alex, item["id"])["read_at"] is not None
    assert _dots(client, alex) == {}
    # The member's own view is separate: Alex's note is new to them until they open the card.
    assert _dots(client, member) == {item["id"]: "human"}
    _comment(client, member, item["id"], "Another")
    assert _dots(client, alex) == {item["id"]: "human"}


def test_read_is_one_private_event_with_no_item_id(client, alex, todo, make_account):
    _, member = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    _comment(client, member, item["id"], "Hi")
    _read(client, alex, item["id"])
    _read(client, alex, item["id"])  # second open: nothing new, no second event
    evs = client.get(f"{API}/events?type=item.read&since=0", headers=alex).json()["events"]
    assert len(evs) == 1
    (ev,) = evs
    assert ev["item_id"] is None and ev["after"]["item_id"] == item["id"]
    alex_id = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    assert ev["actor_id"] == alex_id and ev["subject_id"] == alex_id
    # Opening a card is not item history.
    history = client.get(f"{API}/items/{item['id']}/events", headers=alex).json()
    assert not [e for e in history if e["type"] == "item.read"]


def test_reading_in_the_inbox_does_not_clear_the_dot(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"])
    _comment(client, agent, item["id"], "@alex ping")
    r = client.post(f"{API}/inbox/read", json={"all": True}, headers=alex)
    assert r.status_code == 200, r.text
    assert _dots(client, alex) == {item["id"]: "mention"}


def test_trashed_items_have_no_dot(client, alex, todo, make_account):
    _, member = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    _comment(client, member, item["id"], "Hi")
    assert client.post(f"{API}/items/{item['id']}/trash", headers=alex).status_code == 200
    assert _dots(client, alex) == {}


# --- guests ----------------------------------------------------------------------------------

from test_guest_scope import BROWSER, fresh_attempts, world  # noqa: E402, F401, F811


def test_a_guest_sees_only_its_projects_dots_and_never_a_workspace_agent(client, alex, world):  # noqa: F811
    guest, task = world["guest"], world["task"]
    ltd_h = world["ltd_h"]  # a workspace agent shared into the project
    _comment(client, ltd_h, task["id"], "Agent note")
    _comment(client, alex, world["secret_item"]["id"], "Private")
    assert _dots(guest, BROWSER) == {}
    _comment(client, alex, task["id"], "@guest can you help Friday?")
    assert _dots(guest, BROWSER) == {task["id"]: "mention"}
    r = guest.post(f"{API}/items/{task['id']}/read", headers=BROWSER)
    assert r.status_code == 200 and r.json()["read_at"], r.text
    assert _dots(guest, BROWSER) == {}
    # The guest's read is its own: Alex never sees it as a dot change, and a guest's comment
    # is a person's comment to Alex.
    assert (
        guest.post(
            f"{API}/items/{task['id']}/comments", json={"body_md": "Yes"}, headers=BROWSER
        ).status_code
        == 201
    )
    assert _dots(client, alex)[task["id"]] == "human"


def test_a_limited_token_can_resolve_an_ask(client, alex, world):  # noqa: F811
    """Resolving with a restricted token must not flush the half-made change while checking
    access (that fails with WriteWithoutEvent, a 500)."""
    task, ltd_h = world["task"], world["ltd_h"]
    ask = _comment(client, alex, task["id"], "Done yet?", assignee="agent")
    r = client.post(f"{API}/comments/{ask['id']}/resolve", headers=ltd_h)
    assert r.status_code == 200, r.text
    assert r.json()["resolved_at"]
