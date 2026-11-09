"""Every device must run the deployed build: the page and service worker are revalidated on each
load, only the hashed bundles are cached for good."""

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    from slate import app as app_module
    from slate.settings import settings

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<script src="/assets/index-abc123.js"></script>')
    (tmp_path / "sw.js").write_text("// sw")
    (tmp_path / "assets" / "index-abc123.js").write_text("// js")
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    return TestClient(app_module.create_app())


def test_page_and_service_worker_are_revalidated(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    for path in ("/", "/index.html", "/sw.js"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache", path


def test_revalidation_still_answers_304(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    etag = c.get("/").headers["etag"]
    r = c.get("/", headers={"if-none-match": etag})
    assert r.status_code == 304
    assert r.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_immutable(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]
