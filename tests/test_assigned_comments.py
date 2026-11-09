"""Assigned comments ("Ask"): a comment addressed to one account, open until they reply on the
item or someone resolves it. REST, MCP, check-in news `asked`, Today `asked`, the agent work
loop (why `asked`), the agent stream hint and mail-in `@handle` replies."""

from __future__ import annotations

from conftest import API


def _item(client, h, list_id, title="Plan the offsite", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _comment(client, h, item_id, body, **kw):
    r = client.post(f"{API}/items/{item_id}/comments", json={"body_md": body, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _me(client, h):
    return client.get(f"{API}/me", headers=h).json()["account"]


def _events(client, h, item_id, type_):
    evs = client.get(f"{API}/items/{item_id}/events", headers=h).json()
    return [e for e in evs if e["type"] == type_]


# --- service and REST -----------------------------------------------------------------------


def test_ask_assigns_the_comment_and_a_reply_by_the_assignee_keeps_it_open(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"])
    ask = _comment(client, alex, item["id"], "Which venue did we pick?", assignee="agent")
    assert ask["assignee_id"] == agent["id"] and ask["resolved_at"] is None
    (assigned,) = _events(client, alex, item["id"], "comment.assigned")
    assert assigned["after"] == {
        "assignee": "agent",
        "assignee_id": agent["id"],
        "comment_id": ask["id"],
    }
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert view["open_asks"] == [agent["id"]]
    assert item["assignees"] == []  # independent of the item's assignees

    # Neither someone else's comment nor the assignee's reply settles the request.
    _comment(client, alex, item["id"], "Also: dates?")
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"]
    _comment(client, ch, item["id"], "The lake house.")
    comments = client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    first = next(c for c in comments if c["id"] == ask["id"])
    assert first["resolved_at"] is None and first["resolved_by_id"] is None
    assert _events(client, alex, item["id"], "comment.resolved") == []
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"] == [
        agent["id"]
    ]


def test_keep_open_reply_and_resolve_by_hand(client, alex, todo, make_account):
    _, ch = make_account("agent")
    _, dh = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    ask = _comment(client, alex, item["id"], "Book it?", assignee="agent")
    _comment(client, ch, item["id"], "Looking into it", keep_open=True)
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["open_asks"]

    # The member is neither assignee nor author (nor admin): refused.
    assert client.post(f"{API}/comments/{ask['id']}/resolve", headers=dh).status_code == 403
    r = client.post(f"{API}/comments/{ask['id']}/resolve", headers=ch)
    assert r.status_code == 200 and r.json()["resolved_at"]
    # Idempotent.
    assert client.post(f"{API}/comments/{ask['id']}/resolve", headers=ch).status_code == 200
    assert len(_events(client, alex, item["id"], "comment.resolved")) == 1


def test_patch_assigns_reassigns_and_takes_off(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    member, _ = make_account("member", kind="person")
    item = _item(client, alex, todo["id"])
    c = _comment(client, alex, item["id"], "Thoughts?")

    r = client.patch(f"{API}/comments/{c['id']}", json={"assignee": "agent"}, headers=alex)
    assert r.status_code == 200 and r.json()["assignee_id"] == agent["id"]
    # Not the author: refused.
    r = client.patch(f"{API}/comments/{c['id']}", json={"assignee": "member"}, headers=ch)
    assert r.status_code == 403
    r = client.patch(f"{API}/comments/{c['id']}", json={"assignee": "member"}, headers=alex)
    assert r.json()["assignee_id"] == member["id"]
    r = client.patch(f"{API}/comments/{c['id']}", json={"assignee": None}, headers=alex)
    assert r.json()["assignee_id"] is None
    # Editing text still works through the same PATCH.
    r = client.patch(
        f"{API}/comments/{c['id']}", json={"body_md": "Thoughts, anyone?"}, headers=alex
    )
    assert r.json()["body_md"] == "Thoughts, anyone?"
    # Resolving a comment addressed to nobody is invalid.
    assert client.post(f"{API}/comments/{c['id']}/resolve", headers=alex).status_code == 422


def test_items_open_comment_for_filter(client, alex, todo, make_account):
    make_account("agent")
    asked = _item(client, alex, todo["id"], "Asked")
    _item(client, alex, todo["id"], "Not asked")
    _comment(client, alex, asked["id"], "?", assignee="agent")
    r = client.get(f"{API}/items", params={"open_comment_for": "agent"}, headers=alex)
    assert [i["id"] for i in r.json()] == [asked["id"]]


# --- queues: check-in, Today ----------------------------------------------------------------


def test_checkin_news_asked_and_today_asked(client, alex, todo, make_account):
    _, ch = make_account("agent")
    since = client.get(f"{API}/checkin", headers=ch).json()["next_since"]
    item = _item(client, alex, todo["id"])
    _comment(client, alex, item["id"], "Can you check the invoice?", assignee="agent")

    news = client.get(f"{API}/checkin", params={"since": since}, headers=ch).json()["news"]
    asked = [n for n in news if n["why"] == "asked"]
    assert len(asked) == 1 and asked[0]["item_title"] == "Plan the offsite"

    today = client.get(f"{API}/today", headers=ch).json()
    assert [i["id"] for i in today["asked"]] == [item["id"]]
    assert client.get(f"{API}/today", headers=alex).json()["asked"] == []


# --- the agent work loop -------------------------------------------------------------------


def test_an_agent_asked_on_a_task_takes_it_without_being_assigned_and_done_answers(
    client, alex, todo, make_account
):
    agent, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Budget")
    ask = _comment(client, alex, item["id"], "What is left in Q4?", assignee="agent")

    got = client.post(f"{API}/work/next", json={}, headers=ch).json()
    assert got["item"]["id"] == item["id"] and got["why"] == "asked"
    assert [a["id"] for a in got["asks"]] == [ask["id"]]
    assert got["item"]["assignees"] == []  # holding it did not assign the agent

    r = client.post(
        f"{API}/work/{item['id']}/finish",
        json={"outcome": "done", "note": "About $4k left.", "run_id": got["lease"]["run_id"]},
        headers=ch,
    )
    assert r.status_code == 200, r.text
    view = r.json()["item"]
    assert view["completed_at"] is None  # answering does not complete the task
    assert view["agent"] is None and view["open_asks"] == []
    assert view["assignees"] == []


def test_ask_opens_the_due_gate(client, alex, todo, make_account):
    _, ch = make_account("agent")
    item = _item(client, alex, todo["id"], "Later", assignees=["agent"], due_at="2099-01-01")
    assert client.post(f"{API}/work/next", json={}, headers=ch).json()["item"] is None
    _comment(client, alex, item["id"], "Quick question first?", assignee="agent")
    got = client.post(f"{API}/work/next", json={}, headers=ch).json()
    assert got["item"]["id"] == item["id"]


# --- MCP ------------------------------------------------------------------------------------


async def test_mcp_comment_add_assign_resolve(alex, make_account):
    from test_mcp import call, http_mcp, make_list, ok

    make_account("agent")
    async with http_mcp(alex) as mcp:
        todo = await make_list(mcp)
        item = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Ask me"))
        c = ok(
            await call(mcp, "slate_comment", "add", id=item["id"], body_md="?", assignee="agent")
        )
        assert c["assignee_id"]
        c = ok(await call(mcp, "slate_comment", "assign", id=c["id"], assignee=None))
        assert c.get("assignee_id") is None
        c = ok(await call(mcp, "slate_comment", "assign", id=c["id"], assignee="agent"))
        c = ok(await call(mcp, "slate_comment", "resolve", id=c["id"]))
        assert c["resolved_at"]
