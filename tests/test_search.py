"""One search (`GET /search`, MCP `slate_search`): typed hits over tasks, docs and chat, tasks
first, access-filtered, with `%` and `_` taken literally."""

from __future__ import annotations

from conftest import API
from test_mcp import call, http_mcp, ok
from test_permissions import share


def _item(client, h, list_id, title, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _say(client, h, key, body, **extra):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _search(client, h, q, **params):
    r = client.get(f"{API}/search", params={"q": q, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["hits"]


def test_hits_are_typed_tasks_first_then_docs_then_chat(client, alex, todo):
    task = _item(client, alex, todo["id"], "Renew the passport", body_md="Bring two photos")
    _item(client, alex, todo["id"], "Nothing here")
    page = _doc(client, alex, title="Travel plan", body_md="The passport office opens at 9.")
    msg = _say(client, alex, f"list:{todo['id']}", "Did anyone find the passport?")
    reply = _say(
        client, alex, f"list:{todo['id']}", "passport is in the drawer", parent_id=msg["id"]
    )

    hits = _search(client, alex, "passport")
    assert [h["kind"] for h in hits] == ["task", "doc", "message", "message"]
    t, d, m2, m1 = hits
    assert t["id"] == task["id"] and t["title"] == "Renew the passport"
    assert t["where"] == "Todo" and t["list_id"] == todo["id"]
    assert d["id"] == page["id"] and d["title"] == "Travel plan" and d["where"] == "Workspace"
    assert "passport office" in d["snippet"]
    # Newest message first; a reply carries its thread so the board can open it there.
    assert m2["id"] == reply["id"] and m2["thread_id"] == msg["id"]
    assert m1["id"] == msg["id"] and m1["thread_id"] is None
    assert (
        m1["title"] == "Todo" and m1["where"] == "#Todo" and m1["channel"] == f"list:{todo['id']}"
    )
    assert "find the passport" in m1["snippet"]
    assert all(h["at"] for h in hits)

    # A body-only task match gives the text around the match, not the title.
    (hit,) = _search(client, alex, "two photos")
    assert hit["kind"] == "task" and "two photos" in hit["snippet"]


def test_limit_is_shared_across_the_groups(client, alex, todo):
    for n in range(6):
        _item(client, alex, todo["id"], f"Apple task {n}")
    _doc(client, alex, title="Apple page")
    for n in range(6):
        _say(client, alex, f"list:{todo['id']}", f"apple message {n}")

    hits = _search(client, alex, "apple", limit=6)
    kinds = [h["kind"] for h in hits]
    # Two each, then leftovers in order: tasks fill what the docs group could not use.
    assert len(hits) == 6
    assert kinds == ["task", "task", "task", "doc", "message", "message"]
    assert len(_search(client, alex, "apple")) == 13


def test_percent_and_underscore_are_literal(client, alex, todo):
    _say(client, alex, f"list:{todo['id']}", "Battery at 100% now")
    _say(client, alex, f"list:{todo['id']}", "Battery at 100 percent now")
    _say(client, alex, f"list:{todo['id']}", "Use snake_case here")
    _say(client, alex, f"list:{todo['id']}", "Use snakeXcase here")

    assert [h["snippet"] for h in _search(client, alex, "100%")] == ["Battery at 100% now"]
    assert [h["snippet"] for h in _search(client, alex, "snake_case")] == ["Use snake_case here"]
    # A bare `%` finds the one message that contains one, not everything.
    assert [h["snippet"] for h in _search(client, alex, "%")] == ["Battery at 100% now"]


def test_a_guest_only_sees_hits_in_reach(client, alex, todo, make_account):
    r = client.post(f"{API}/sections", json={"name": "Work", "audience": "work"}, headers=alex)
    other = client.post(
        f"{API}/lists", json={"section_id": r.json()["id"], "name": "Clients"}, headers=alex
    ).json()
    _item(client, alex, todo["id"], "Shared secret handshake")
    _item(client, alex, other["id"], "Private secret handshake")
    _doc(client, alex, title="Shared secret notes", list_id=todo["id"])
    _doc(client, alex, title="Private secret notes", list_id=other["id"])
    _say(client, alex, f"list:{todo['id']}", "the secret is shared")
    _say(client, alex, f"list:{other['id']}", "the secret is private")

    guest = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    tok = client.post(
        f"{API}/accounts/{guest['id']}/tokens", json={"scopes": ["read"]}, headers=alex
    ).json()
    gh = {"Authorization": f"Bearer {tok['token']}"}
    assert _search(client, gh, "secret") == []

    share(client, alex, guest["id"], "list", todo["id"], "view")
    hits = _search(client, gh, "secret")
    assert sorted((h["kind"], h["title"]) for h in hits) == [
        ("doc", "Shared secret notes"),
        ("message", "Todo"),
        ("task", "Shared secret handshake"),
    ]
    assert all("private" not in (h["snippet"] or "").lower() for h in hits)


def test_an_empty_query_is_refused(client, alex):
    assert client.get(f"{API}/search", params={"q": " "}, headers=alex).status_code == 422
    assert client.get(f"{API}/search", headers=alex).status_code == 422


def test_archived_and_deleted_things_do_not_hit(client, alex, todo):
    item = _item(client, alex, todo["id"], "Old lantern")
    msg = _say(client, alex, f"list:{todo['id']}", "lantern in chat")
    assert len(_search(client, alex, "lantern")) == 2
    assert client.post(f"{API}/items/{item['id']}/archive", headers=alex).status_code == 200
    assert client.delete(f"{API}/messages/{msg['id']}", headers=alex).status_code in (200, 204)
    assert _search(client, alex, "lantern") == []


async def test_search_through_mcp(client, alex, todo):
    _item(client, alex, todo["id"], "Fix the gutter")
    async with http_mcp(alex) as mcp:
        out = ok(await call(mcp, "slate_search", "search", q="gutter"))
    assert out["q"] == "gutter"
    assert [(h["kind"], h["title"]) for h in out["hits"]] == [("task", "Fix the gutter")]


# --- case folding and wildcards are the same in every group -------------------------------


def test_case_folds_beyond_ascii_in_every_search(client, alex, todo):
    task = _item(client, alex, todo["id"], "Österreich trip", body_md="Visit ÉCOLE friends")
    page = _doc(client, alex, title="Ärzte list", body_md="Call the ÜBER office")
    msg = _say(client, alex, f"list:{todo['id']}", "Packed the ÇANTA bag")
    for q in ("Österreich", "österreich", "ÖSTERREICH", "école"):
        assert [h["id"] for h in _search(client, alex, q)] == [task["id"]], q
        got = client.get(f"{API}/items", params={"q": q}, headers=alex).json()
        assert [i["id"] for i in got] == [task["id"]], q
    for q in ("ärzte", "ÄRZTE", "über"):
        assert [h["id"] for h in _search(client, alex, q)] == [page["id"]], q
        got = client.get(f"{API}/docs", params={"q": q}, headers=alex).json()
        rows = got["docs"] if isinstance(got, dict) else got
        assert [d["id"] for d in rows] == [page["id"]], q
    assert [h["id"] for h in _search(client, alex, "çanta")] == [msg["id"]]


def test_percent_and_underscore_are_literal_for_tasks_and_docs_too(client, alex, todo):
    _item(client, alex, todo["id"], "plain task")
    hit = _item(client, alex, todo["id"], "50% off_sale")
    _doc(client, alex, title="plain page")
    doc = _doc(client, alex, title="Q3_plan at 10%")
    for q in ("_", "%"):
        got = {h["id"] for h in _search(client, alex, q)}
        assert got == {hit["id"], doc["id"]}, q
        items = client.get(f"{API}/items", params={"q": q}, headers=alex).json()
        assert [i["id"] for i in items] == [hit["id"]], q
