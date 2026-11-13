"""Request bodies have one size cap for the whole app, counted while the body is read (a chunked
body carries no length), and the long text fields have their own limits. The cap is sized so the
documented 25 MB upload still fits as base64 JSON."""

from __future__ import annotations

import base64
import json
import math

import pytest

from conftest import API
from slate.api import schemas, uploads
from slate.service import attachments as attachments_svc


def _chunks(total: int, size: int = 64 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        yield b" " * n
        sent += n


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_REQUEST_BYTES", 64 * 1024)
    return 64 * 1024


@pytest.fixture
def item(client, alex, todo) -> dict:
    r = client.post(f"{API}/items", json={"list_id": todo["id"], "title": "Spec"}, headers=alex)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_declared_body_over_the_cap_is_refused_before_it_is_read(client, alex, todo, small_cap):
    body = json.dumps({"list_id": todo["id"], "title": "x", "body_md": "a" * small_cap})
    r = client.post(
        f"{API}/items", content=body, headers={**alex, "Content-Type": "application/json"}
    )
    assert r.status_code == 413, r.text
    assert "larger than" in r.json()["detail"]
    assert client.get(f"{API}/items", headers=alex).json() == []


def test_a_chunked_body_over_the_cap_is_refused_while_it_streams(client, alex, small_cap):
    r = client.post(
        f"{API}/items",
        content=_chunks(small_cap * 4),
        headers={**alex, "Content-Type": "application/json"},
    )
    assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413, r.text


def test_a_chunked_body_under_the_cap_is_read_as_usual(client, alex, todo, small_cap):
    body = b'{"list_id": "%s", "title": "chunked"}' % todo["id"].encode()
    r = client.post(
        f"{API}/items",
        content=(body[i : i + 7] for i in range(0, len(body), 7)),
        headers={**alex, "Content-Type": "application/json"},
    )
    assert r.status_code == 201, r.text


def test_the_cap_leaves_room_for_a_25_mb_file_as_base64_json():
    encoded = math.ceil(attachments_svc.MAX_BYTES / 3) * 4
    assert encoded + 64 * 1024 < uploads.MAX_REQUEST_BYTES
    assert encoded == uploads.BASE64_MAX_CHARS


def test_a_25_mb_base64_upload_still_works(client, alex, item):
    data = b"\x00" * attachments_svc.MAX_BYTES
    r = client.post(
        f"{API}/items/{item['id']}/attachments/base64",
        json={"filename": "big.bin", "content_base64": base64.b64encode(data).decode()},
        headers=alex,
    )
    assert r.status_code == 201, r.text


def test_a_25_mb_raw_upload_still_works(client, alex, item):
    r = client.post(
        f"{API}/items/{item['id']}/attachments",
        params={"filename": "big.bin"},
        content=b"\x00" * attachments_svc.MAX_BYTES,
        headers={**alex, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 201, r.text


def test_base64_over_the_decoded_cap_is_refused_by_length(client, alex, item):
    r = client.post(
        f"{API}/items/{item['id']}/attachments/base64",
        json={"filename": "big.bin", "content_base64": "A" * (uploads.BASE64_MAX_CHARS + 4)},
        headers=alex,
    )
    assert r.status_code == 422, r.text


def test_a_task_body_over_its_limit_is_refused(client, alex, todo, item):
    long = "a" * (schemas.ITEM_BODY_MAX + 1)
    r = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "t", "body_md": long}, headers=alex
    )
    assert r.status_code == 422
    r = client.patch(f"{API}/items/{item['id']}", json={"acceptance_md": long}, headers=alex)
    assert r.status_code == 422
    ok = "a" * schemas.ITEM_BODY_MAX
    r = client.patch(f"{API}/items/{item['id']}", json={"body_md": ok}, headers=alex)
    assert r.status_code == 200, r.text


def test_a_comment_over_its_limit_is_refused(client, alex, item):
    url = f"{API}/items/{item['id']}/comments"
    long = "a" * (schemas.COMMENT_MAX + 1)
    assert client.post(url, json={"body_md": long}, headers=alex).status_code == 422
    r = client.post(url, json={"body_md": "a" * schemas.COMMENT_MAX}, headers=alex)
    assert r.status_code == 201, r.text
    edit = client.patch(f"{API}/comments/{r.json()['id']}", json={"body_md": long}, headers=alex)
    assert edit.status_code == 422


def test_a_split_into_too_many_children_is_refused(client, alex, item):
    url = f"{API}/items/{item['id']}/split"
    many = [{"title": f"c{i}"} for i in range(schemas.SPLIT_MAX + 1)]
    assert client.post(url, json={"children": many}, headers=alex).status_code == 422
    r = client.post(url, json={"children": many[: schemas.SPLIT_MAX]}, headers=alex)
    assert r.status_code == 201, r.text


def test_a_page_body_over_its_limit_is_refused(client, alex):
    long = "a" * (schemas.PAGE_BODY_MAX + 1)
    r = client.post(f"{API}/docs", json={"title": "N", "body_md": long}, headers=alex)
    assert r.status_code == 422
    page = client.post(f"{API}/docs", json={"title": "N"}, headers=alex).json()
    r = client.patch(f"{API}/docs/{page['id']}", json={"append_md": long}, headers=alex)
    assert r.status_code == 422
