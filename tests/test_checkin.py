"""The check-in: an account's open work and the others' events that concern it, from a cursor."""

from __future__ import annotations

from conftest import API


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comment(client, h, item_id, body):
    r = client.post(f"{API}/items/{item_id}/comments", json={"body_md": body}, headers=h)
    assert r.status_code == 201, r.text


def _checkin(client, h, **q):
    r = client.get(f"{API}/checkin", params=q, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_queue_is_open_work_assigned_or_waiting_most_urgent_first(client, alex, todo, make_account):
    _, ch = make_account("agent")
    low = _item(client, alex, todo["id"], "low", assignees=["agent"], priority="low")
    urgent = _item(client, alex, todo["id"], "urgent", assignees=["agent"], priority="urgent")
    waiting = _item(client, alex, todo["id"], "waiting", waiting_on_account_id="agent")
    _item(client, alex, todo["id"], "not mine")
    done = _item(client, alex, todo["id"], "done", assignees=["agent"])
    client.patch(f"{API}/items/{done['id']}", json={"status": "Done"}, headers=alex)

    queue = [i["id"] for i in _checkin(client, ch)["queue"]]
    assert queue[0] == urgent["id"]
    assert set(queue) == {low["id"], urgent["id"], waiting["id"]}


def test_news_says_why_and_leaves_out_my_own_changes(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    start = _checkin(client, ch)["next_since"]

    mine = _item(client, alex, todo["id"], "Draft", assignees=["agent"])
    _comment(client, alex, mine["id"], "any news?")
    other = _item(client, alex, todo["id"], "Other")
    _comment(client, alex, other["id"], "@agent look")
    _comment(client, alex, other["id"], "unrelated")
    _comment(client, ch, mine["id"], "on it")

    out = _checkin(client, ch, since=start)
    whys = [(n["why"], n["type"], n["item_title"]) for n in out["news"]]
    assert whys == [
        ("assigned", "item.assigned", "Draft"),
        ("on_your_item", "item.commented", "Draft"),
        ("mentioned", "item.commented", "Other"),
    ]
    assert all(n["actor_id"] != agent["id"] for n in out["news"])
    assert out["has_more"] is False

    client.patch(f"{API}/items/{mine['id']}", json={"assignees": []}, headers=alex)
    gone = _checkin(client, ch, since=out["next_since"])
    assert [n["why"] for n in gone["news"]] == ["unassigned"]
    out = gone

    # The cursor moved past everything; nothing new, nothing again.
    again = _checkin(client, ch, since=out["next_since"])
    assert again["news"] == [] and again["next_since"] == out["next_since"]


def test_limit_pages_without_gaps_or_repeats(client, alex, todo, make_account):
    _, ch = make_account("agent")
    start = _checkin(client, ch)["next_since"]
    item = _item(client, alex, todo["id"], "Busy", assignees=["agent"])
    for n in range(5):
        _comment(client, alex, item["id"], f"c{n}")
    seen, since = [], start
    while True:
        out = _checkin(client, ch, since=since, limit=2)
        seen += [n["id"] for n in out["news"]]
        since = out["next_since"]
        if not out["has_more"]:
            break
    assert len(seen) == len(set(seen)) == 6  # assigned + 5 comments


def test_without_since_the_latest_news(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Fresh", assignees=["agent"])
    out = _checkin(client, ch, limit=1)
    assert [n["why"] for n in out["news"]] == ["assigned"]
    assert out["news"][0]["item_id"] == item["id"]


def test_watching_an_item_brings_its_changes(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Watched")
    client.post(f"{API}/items/{item['id']}/watch", headers=ch)
    start = _checkin(client, ch)["next_since"]
    client.patch(f"{API}/items/{item['id']}", json={"title": "Watched!"}, headers=alex)
    assert [n["why"] for n in _checkin(client, ch, since=start)["news"]] == ["watching"]


def test_cursor_moves_past_events_that_do_not_concern_me(client, alex, todo, make_account):
    _, ch = make_account("agent")
    start = _checkin(client, ch)["next_since"]
    for n in range(5):
        _item(client, alex, todo["id"], f"Not mine {n}", tags=["x"])
    newest = client.get(f"{API}/events", params={"since": start}, headers=alex).json()
    out = _checkin(client, ch, since=start)
    assert out["news"] == [] and out["has_more"] is False
    assert out["next_since"] == newest["next_since"]
    # A later match after a run of unrelated events is still found from that cursor.
    mine = _item(client, alex, todo["id"], "Mine", assignees=["agent"])
    later = _checkin(client, ch, since=out["next_since"])
    assert [(n["why"], n["item_id"]) for n in later["news"]] == [("assigned", mine["id"])]


def test_queue_is_capped_and_trimmed_with_a_total(client, alex, todo, make_account):
    _, ch = make_account("agent")
    for i in range(5):
        client.post(
            "/api/v1/items",
            json={"list_id": todo["id"], "title": f"t{i}", "assignees": ["agent"]},
            headers=alex,
        )
    r = client.get("/api/v1/checkin?queue_limit=2&fields=title,due_at", headers=ch).json()
    assert r["queue_total"] == 5 and len(r["queue"]) == 2
    assert set(r["queue"][0]) == {"id", "title", "due_at"}
    news_only = client.get("/api/v1/checkin?queue_limit=0", headers=ch).json()
    assert news_only["queue"] == [] and news_only["queue_total"] == 5
    assert client.get("/api/v1/checkin?fields=nope", headers=ch).status_code == 422


def test_an_edited_comment_does_not_break_the_checkin(client, alex, todo, make_account):
    # comment.edited carries the new body (a string) as `after`, not a dict with mentions.
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "x", assignees=["agent"])
    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=alex)
    client.patch(f"{API}/comments/{r.json()['id']}", json={"body_md": "hello"}, headers=alex)
    news = _checkin(client, ch)["news"]
    assert "comment.edited" in [n["type"] for n in news]
