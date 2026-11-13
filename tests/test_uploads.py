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


def test_an_upload_waiting_for_the_write_lock_does_not_stop_other_requests(client, alex, todo):
    """The route reads the body on the event loop, then writes in the threadpool: a write
    waiting for the lock (another change holds it) must not hold up `/healthz`."""
    import threading
    import time

    from slate.service import core

    item = client.post(f"{API}/items", json={"list_id": todo["id"], "title": "x"}, headers=alex)
    released = threading.Event()
    held = threading.Event()

    def hold() -> None:
        with core._write_lock:  # stands in for a slow change (a big batch, an import)
            held.set()
            released.wait(5)

    with client:  # one event loop for every request, as under uvicorn
        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(2)
        got: list[int] = []
        upload = threading.Thread(
            target=lambda: got.append(
                client.post(
                    f"{API}/items/{item.json()['id']}/attachments?filename=a.txt",
                    content=b"hello",
                    headers={**alex, "Content-Type": "text/plain"},
                ).status_code
            )
        )
        upload.start()
        time.sleep(0.3)  # the upload is now waiting for the lock
        started = time.perf_counter()
        assert client.get("/healthz").status_code == 200
        elapsed = time.perf_counter() - started
        released.set()
        holder.join()
        upload.join()
    assert elapsed < 1.0, f"/healthz waited {elapsed:.1f} s behind an upload"
    assert got == [201]
