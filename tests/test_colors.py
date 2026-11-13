"""Status and tag colours: a hex colour or a colour name, nothing else (they end up in CSS)."""

import pytest

from conftest import API

GOOD = ["#c00", "#c00a", "#1d2127", "#1d2127cc", "red", "SlateGray"]
BAD = ["red;x:y", "0;position:fixed", "#12", "#1234567", "url(x)", "var(--x)", "#c00 ", "a b"]


@pytest.mark.parametrize("color", GOOD)
def test_status_and_tag_colours_accept_hex_and_names(client, alex, todo, color):
    r = client.put(
        f"{API}/lists/{todo['id']}/statuses",
        json={"statuses": [{"name": "todo", "color": color}, {"name": "done", "kind": "done"}]},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert r.json()["statuses"][0]["color"] == color
    r = client.post(f"{API}/tags", json={"name": f"t-{color[1:4]}", "color": color}, headers=alex)
    assert r.status_code == 201, r.text
    assert r.json()["color"] == color


@pytest.mark.parametrize("color", BAD)
def test_status_and_tag_colours_refuse_anything_else(client, alex, todo, color):
    r = client.put(
        f"{API}/lists/{todo['id']}/statuses",
        json={"statuses": [{"name": "todo", "color": color}, {"name": "done", "kind": "done"}]},
        headers=alex,
    )
    assert r.status_code in (400, 422), r.text
    r = client.post(f"{API}/tags", json={"name": "home", "color": color}, headers=alex)
    assert r.status_code in (400, 422), r.text
    tag = client.post(f"{API}/tags", json={"name": "work"}, headers=alex).json()
    r = client.patch(f"{API}/tags/{tag['id']}", json={"color": color}, headers=alex)
    assert r.status_code in (400, 422), r.text
    client.delete(f"{API}/tags/{tag['id']}", headers=alex)


def test_a_new_list_refuses_a_bad_status_colour_and_a_tag_colour_still_clears(client, alex, todo):
    r = client.post(
        f"{API}/lists",
        json={
            "section_id": todo["section_id"],
            "name": "Other",
            "statuses": [{"name": "todo", "color": "red;x:y"}, {"name": "done", "kind": "done"}],
        },
        headers=alex,
    )
    assert r.status_code in (400, 422), r.text
    tag = client.post(f"{API}/tags", json={"name": "home", "color": "#c00"}, headers=alex).json()
    r = client.patch(f"{API}/tags/{tag['id']}", json={"color": ""}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["color"] is None
