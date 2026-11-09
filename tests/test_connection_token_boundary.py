"""A narrowed credential cannot disconnect a different device through generic token APIs."""

# ruff: noqa: F811
from test_agent_connections import API, exchange, issue, setup_connection  # noqa: F401


def test_connection_scope_applies_to_all_token_management_routes(client, alex, setup_connection):
    agent, _, body = setup_connection
    first = exchange(client, issue(client, alex, body)["code"]).json()
    section = client.post(
        "/api/v1/sections", headers=alex, json={"name": "Other scope", "audience": "work"}
    ).json()
    other = client.post(
        "/api/v1/lists", headers=alex, json={"name": "Other", "section_id": section["id"]}
    ).json()
    second = exchange(
        client, issue(client, alex, {**body, "list_ids": [other["id"]]})["code"]
    ).json()
    own = {"Authorization": "Bearer " + first["token"]}
    sibling = {"Authorization": "Bearer " + second["token"]}
    own_id, other_id = first["token"].split("_")[1], second["token"].split("_")[1]
    assert client.delete(API + "/" + second["connection"]["id"], headers=own).status_code == 403
    assert client.delete("/api/v1/tokens/" + other_id, headers=own).status_code == 403
    assert client.get("/api/v1/me", headers=sibling).status_code == 200
    for endpoint in (
        f"/api/v1/accounts/{agent['id']}/tokens",
        f"/api/v1/accounts/{agent['id']}/usage",
    ):
        response = client.get(endpoint, headers=own)
        assert response.status_code == 200, response.text
        data = response.json()
        rows = data if isinstance(data, list) else data["tokens"]
        assert [r["id"] for r in rows] == [own_id]
    assert client.delete("/api/v1/tokens/" + own_id, headers=own).status_code == 204
    assert client.get("/api/v1/me", headers=own).status_code == 401
    assert client.get("/api/v1/me", headers=sibling).status_code == 200
    assert client.delete("/api/v1/tokens/" + other_id, headers=alex).status_code == 204
