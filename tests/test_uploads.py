"""Raw upload bodies are counted as they arrive, not trusted from `Content-Length`: a chunked
request (no length) must not skip the pre-check and be buffered whole before the service's
own 25 MB check runs. One helper (`api/uploads.read_body`) serves attachments and both doc
uploads."""

from __future__ import annotations

import pytest

from conftest import API
from slate.service import attachments as attachments_svc
from slate.service import docs as docs_svc


def _chunks(total: int, size: int = 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        yield b"x" * n
        sent += n


def _file_doc(client, h):
    r = client.post(f"{API}/docs", json={"kind": "file", "title": "Spec"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _page(client, h):
    r = client.post(f"{API}/docs", json={"title": "Notes"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(attachments_svc, "MAX_BYTES", 4096)
    monkeypatch.setattr(docs_svc, "MAX_REVISION_BYTES", 4096)
    monkeypatch.setattr(docs_svc, "MAX_FILE_BYTES", 4096)


def _routes(client, alex, todo):
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Scan"}, headers=alex
    ).json()
    return [
        (f"{API}/items/{item['id']}/attachments", {"filename": "scan.bin"}, "attachment"),
        (f"{API}/docs/{_page(client, alex)}/files", {"filename": "pic.bin"}, "file"),
        (
            f"{API}/docs/{_file_doc(client, alex)}/revisions",
            {"filename": "spec.bin", "note": "first"},
            "file",
        ),
    ]


def test_chunked_upload_over_the_cap_is_refused_without_a_content_length(
    client, alex, todo, small_caps
):
    for url, params, noun in _routes(client, alex, todo):
        r = client.post(
            url,
            params=params,
            content=_chunks(4097),
            headers={**alex, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 422, (url, r.text)
        assert r.json()["detail"] == f"{noun} is larger than 0 MB"
        exact = client.post(
            url,
            params=params,
            content=_chunks(4096),
            headers={**alex, "Content-Type": "application/octet-stream"},
        )
        assert exact.status_code == 201, (url, exact.text)
        assert exact.json()["size"] == 4096


def test_a_declared_length_over_the_cap_is_refused_before_the_body_is_read(
    client, alex, todo, small_caps
):
    url, params, _ = _routes(client, alex, todo)[0]
    headers = {**alex, "Content-Type": "application/octet-stream", "Content-Length": "999999"}
    r = client.post(url, params=params, content=b"tiny", headers=headers)
    assert r.status_code == 422 and "larger than" in r.json()["detail"]
    bad = {**alex, "Content-Type": "application/octet-stream", "Content-Length": "lots"}
    r = client.post(url, params=params, content=b"tiny", headers=bad)
    assert r.status_code == 422 and "not a number" in r.json()["detail"]
