"""Pasted media: an image an agent pastes into a comment or a body as a base64 `data:` URI is
saved as a real attachment on the item, and the text keeps a short note in its place."""

from __future__ import annotations

import base64

from conftest import API

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
B64 = base64.b64encode(PNG).decode()


def _item(client, h, list_id, **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": "Bake-off", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _files(client, h, item_id):
    return client.get(f"{API}/items/{item_id}/attachments", headers=h).json()


def test_a_pasted_image_in_a_comment_becomes_an_attachment(client, alex, todo):
    item = _item(client, alex, todo["id"])
    body = f"Review sheet:\n![Chart preview](data:image/png;base64,{B64})\nYour call."
    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": body}, headers=alex)
    assert r.status_code == 201, r.text
    text = r.json()["body_md"]
    assert "base64" not in text and "data:" not in text
    assert "Review sheet:" in text and "Your call." in text
    files = _files(client, alex, item["id"])
    assert [(f["filename"], f["content_type"], f["size"]) for f in files] == [
        ("Chart-preview.png", "image/png", len(PNG))
    ]
    assert "Chart-preview.png" in text
    got = client.get(f"{API}/attachments/{files[0]['id']}", headers=alex)
    assert got.content == PNG


def test_a_bare_data_uri_and_an_unnamed_image_get_numbered_names(client, alex, todo):
    item = _item(client, alex, todo["id"])
    body = f"![](data:image/png;base64,{B64})\nand data:image/jpeg;base64,{B64}"
    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": body}, headers=alex)
    assert r.status_code == 201, r.text
    names = [f["filename"] for f in _files(client, alex, item["id"])]
    assert names == ["image-1.png", "image-2.jpg"]
    assert "base64" not in r.json()["body_md"]


def test_a_pasted_image_in_a_body_on_create_and_update(client, alex, todo):
    item = _item(client, alex, todo["id"], body_md=f"![first](data:image/png;base64,{B64})")
    assert "base64" not in item["body_md"]
    r = client.patch(
        f"{API}/items/{item['id']}",
        json={"body_md": f"{item['body_md']}\n![second](data:image/png;base64,{B64})"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert "base64" not in r.json()["body_md"]
    names = [f["filename"] for f in _files(client, alex, item["id"])]
    assert names == ["first.png", "second.png"]


def test_editing_a_comment_converts_too_and_invalid_data_is_left_alone(client, alex, todo):
    item = _item(client, alex, todo["id"])
    c = client.post(
        f"{API}/items/{item['id']}/comments", json={"body_md": "draft"}, headers=alex
    ).json()
    r = client.patch(
        f"{API}/comments/{c['id']}",
        json={"body_md": f"![shot](data:image/png;base64,{B64})"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert [f["filename"] for f in _files(client, alex, item["id"])] == ["shot.png"]
    # Not base64 at all: nothing to save, the text stays as written.
    odd = "see data:image/png;base64,not-base64!!"
    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": odd}, headers=alex)
    assert r.json()["body_md"] == odd
