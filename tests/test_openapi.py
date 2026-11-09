"""The OpenAPI document lists the core REST routes and every item filter."""

from __future__ import annotations

CORE_ROUTES = {
    ("get", "/api/v1/sections"),
    ("post", "/api/v1/sections"),
    ("patch", "/api/v1/sections/{section_id}"),
    ("get", "/api/v1/lists"),
    ("post", "/api/v1/lists"),
    ("patch", "/api/v1/lists/{list_id}"),
    ("put", "/api/v1/lists/{list_id}/statuses"),
    ("get", "/api/v1/items"),
    ("get", "/api/v1/items/{item_id}"),
    ("post", "/api/v1/items"),
    ("patch", "/api/v1/items/{item_id}"),
    ("post", "/api/v1/items/{item_id}/archive"),
    ("post", "/api/v1/items/{item_id}/unarchive"),
    ("post", "/api/v1/items/{item_id}/split"),
    ("get", "/api/v1/items/{item_id}/events"),
    ("get", "/api/v1/items/{item_id}/versions"),
    ("post", "/api/v1/items/{item_id}/comments"),
    ("patch", "/api/v1/comments/{comment_id}"),
    ("get", "/api/v1/tags"),
    ("post", "/api/v1/tags"),
    ("get", "/api/v1/today"),
}

ITEM_FILTERS = {
    "list_id",
    "section_id",
    "status",
    "assignee",
    "tag",
    "external_ref",
    "due_before",
    "due_after",
    "q",
    "ids",
    "open_comment_for",
    "stale",
    "include_archived",
    "archived",
    "trashed",
    "closed",
    "order",
    "kind",
    "blocked",
    "changed_since",
    "fields",
    "limit",
    "offset",
}


def test_openapi_has_every_core_route(client):
    paths = client.get("/openapi.json").json()["paths"]
    present = {(verb, path) for path, ops in paths.items() for verb in ops}
    assert present >= CORE_ROUTES, sorted(CORE_ROUTES - present)


def test_item_query_documents_every_filter(client):
    op = client.get("/openapi.json").json()["paths"]["/api/v1/items"]["get"]
    assert {p["name"] for p in op["parameters"] if p["in"] == "query"} == ITEM_FILTERS
