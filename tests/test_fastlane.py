"""Fast lane: Server-Timing, /stats, gzip, ETag/304 and the X-Slate-Cursor header."""

from __future__ import annotations

from conftest import API


def _item(client, alex, todo, title="one"):
    r = client.post(f"{API}/items", json={"list_id": todo["id"], "title": title}, headers=alex)
    assert r.status_code == 201, r.text
    return r.json()


def test_every_api_response_carries_server_timing(client, alex):
    r = client.get(f"{API}/me", headers=alex)
    assert r.headers["server-timing"].startswith("app;dur=")


def test_stats_reports_latency_per_route_template(client, alex, todo):
    item = _item(client, alex, todo)
    for _ in range(3):
        client.get(f"{API}/items/{item['id']}", headers=alex)
    body = client.get(f"{API}/stats", headers=alex).json()
    routes = {(r["method"], r["route"]): r for r in body["routes"]}
    detail = routes[("GET", "/api/v1/items/{item_id}")]
    assert detail["count"] >= 3
    assert 0 <= detail["p50_ms"] <= detail["p95_ms"] <= detail["max_ms"]
    assert body["items"] == 1 and body["open_items"] == 1
    assert body["events"] > 0 and body["cursor"] != "0"


def test_stats_needs_a_token(client):
    assert client.get(f"{API}/stats").status_code == 401


def test_large_json_is_gzipped(client, alex, todo):
    for i in range(20):
        _item(client, alex, todo, f"item number {i}")
    r = client.get(f"{API}/items", headers={**alex, "Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert len(r.json()) == 20


def test_unchanged_read_is_a_304_and_any_change_breaks_the_tag(client, alex, todo):
    item = _item(client, alex, todo)
    first = client.get(f"{API}/items?list_id={todo['id']}", headers=alex)
    etag = first.headers["etag"]
    again = client.get(f"{API}/items?list_id={todo['id']}", headers={**alex, "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == etag

    client.patch(f"{API}/items/{item['id']}", json={"title": "two"}, headers=alex)
    after = client.get(f"{API}/items?list_id={todo['id']}", headers={**alex, "If-None-Match": etag})
    assert after.status_code == 200 and after.json()[0]["title"] == "two"
    assert after.headers["etag"] != etag


def test_tag_is_per_url_and_per_token(client, alex, todo, make_account):
    _item(client, alex, todo)
    _, agent = make_account("agent", ("read",))
    a = client.get(f"{API}/items", headers=alex).headers["etag"]
    b = client.get(f"{API}/items?q=x", headers=alex).headers["etag"]
    c = client.get(f"{API}/items", headers=agent).headers["etag"]
    assert len({a, b, c}) == 3
    # Someone else's tag never short-circuits: a different token gets the full answer.
    r = client.get(f"{API}/items", headers={**agent, "If-None-Match": a})
    assert r.status_code == 200


def test_no_304_without_a_token(client, alex, todo):
    _item(client, alex, todo)
    etag = client.get(f"{API}/items", headers=alex).headers["etag"]
    r = client.get(f"{API}/items", headers={"If-None-Match": etag})
    assert r.status_code == 401


def test_time_dependent_reads_are_not_tagged(client, alex, todo):
    _item(client, alex, todo)
    assert "etag" not in client.get(f"{API}/today", headers=alex).headers
    assert "etag" not in client.get(f"{API}/items?stale=true", headers=alex).headers


def test_cursor_header_lets_a_reader_follow_on_without_a_gap(client, alex, todo):
    _item(client, alex, todo, "before")
    r = client.get(f"{API}/items", headers=alex)
    cursor = r.headers["x-slate-cursor"]
    assert [i["title"] for i in r.json()] == ["before"]
    _item(client, alex, todo, "after")
    page = client.get(f"{API}/events?since={cursor}", headers=alex).json()
    assert [e["after"]["title"] for e in page["events"] if e["type"] == "item.created"] == ["after"]


# --- GET /items for agents: fields, paging, changed_since ---------------------------------------


def test_fields_trims_each_item_and_always_keeps_id(client, alex, todo):
    _item(client, alex, todo, "one")
    r = client.get(f"{API}/items?fields=title,status", headers=alex)
    assert r.status_code == 200
    [row] = r.json()
    assert set(row) == {"id", "title", "status"}
    assert row["status"]["name"] == "To do"


def test_unknown_field_is_a_422_listing_the_real_ones(client, alex):
    r = client.get(f"{API}/items?fields=title,colour", headers=alex)
    assert r.status_code == 422 and "colour" in r.json()["detail"]
    assert "due_at" in r.json()["detail"]


def test_full_items_keep_the_documented_shape(client, alex, todo):
    _item(client, alex, todo, "one")
    [row] = client.get(f"{API}/items", headers=alex).json()
    assert row["created_at"].endswith("Z")
    from slate.api.schemas import ItemOut

    assert set(row) == set(ItemOut.model_fields)


def test_limit_and_offset_page_in_order(client, alex, todo):
    for i in range(5):
        _item(client, alex, todo, f"t{i}")
    page1 = client.get(f"{API}/items?limit=2&fields=title", headers=alex).json()
    page2 = client.get(f"{API}/items?limit=2&offset=2&fields=title", headers=alex).json()
    page3 = client.get(f"{API}/items?limit=2&offset=4&fields=title", headers=alex).json()
    assert [i["title"] for i in page1 + page2 + page3] == [f"t{i}" for i in range(5)]


def test_changed_since_returns_exactly_the_items_touched_after_the_cursor(client, alex, todo):
    a = _item(client, alex, todo, "a")
    b = _item(client, alex, todo, "b")
    _item(client, alex, todo, "c")
    cursor = client.get(f"{API}/items", headers=alex).headers["x-slate-cursor"]
    assert client.get(f"{API}/items?changed_since={cursor}", headers=alex).json() == []

    client.patch(f"{API}/items/{a['id']}", json={"title": "a2"}, headers=alex)
    client.post(f"{API}/items/{b['id']}/comments", json={"body_md": "hi"}, headers=alex)
    d = _item(client, alex, todo, "d")
    changed = client.get(f"{API}/items?changed_since={cursor}", headers=alex).json()
    assert {i["id"] for i in changed} == {a["id"], b["id"], d["id"]}

    cursor2 = client.get(f"{API}/items?changed_since={cursor}", headers=alex).headers[
        "x-slate-cursor"
    ]
    client.post(f"{API}/items/{d['id']}/archive", headers=alex)
    gone = client.get(
        f"{API}/items?changed_since={cursor2}&include_archived=true&fields=archived_at",
        headers=alex,
    ).json()
    assert [i["id"] for i in gone] == [d["id"]] and gone[0]["archived_at"]
