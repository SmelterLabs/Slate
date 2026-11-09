"""Moving a task to another list takes its subtasks with it, and a subtask cannot move alone."""

API = "/api/v1"


def _later(client, alex, todo):
    section_id = client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["section_id"]
    return client.post(
        f"{API}/lists", json={"section_id": section_id, "name": "Later"}, headers=alex
    ).json()


def test_moving_a_parent_takes_its_subtasks(client, alex, todo):
    later = _later(client, alex, todo)
    parent = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Bank follow-up"}, headers=alex
    ).json()
    child = client.post(
        f"{API}/items", json={"parent_id": parent["id"], "title": "Call the bank"}, headers=alex
    ).json()

    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [parent["id"]], "changes": {"list_id": later["id"]}},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    got = client.get(f"{API}/items/{child['id']}", headers=alex).json()
    assert got["list_id"] == later["id"]
    assert got["status"]["id"] in {s["id"] for s in later["statuses"]}


def test_a_subtask_cannot_move_without_its_parent(client, alex, todo):
    later = _later(client, alex, todo)
    parent = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Parent"}, headers=alex
    ).json()
    child = client.post(
        f"{API}/items", json={"parent_id": parent["id"], "title": "Child"}, headers=alex
    ).json()
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [child["id"]], "changes": {"list_id": later["id"]}},
        headers=alex,
    )
    assert r.status_code == 422 and "move the parent" in r.json()["detail"]

    both = client.post(
        f"{API}/items/bulk",
        json={"ids": [parent["id"], child["id"]], "changes": {"list_id": later["id"]}},
        headers=alex,
    )
    assert both.status_code == 200, both.text
    assert {i["list_id"] for i in both.json()} == {later["id"]}


def test_moving_a_parent_takes_every_level(client, alex, todo):
    """Subtasks nest four deep; a move to another list carries the whole tree."""
    later = _later(client, alex, todo)
    ids = []
    parent_id = None
    for n in range(4):
        body = {
            "title": f"L{n}",
            **({"parent_id": parent_id} if parent_id else {"list_id": todo["id"]}),
        }
        parent_id = client.post(f"{API}/items", json=body, headers=alex).json()["id"]
        ids.append(parent_id)
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [ids[0]], "changes": {"list_id": later["id"]}},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    for i in ids:
        assert client.get(f"{API}/items/{i}", headers=alex).json()["list_id"] == later["id"]
