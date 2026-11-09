def test_healthz_reports_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert body["version"]


def test_healthz_reports_deploy_fields(client):
    from slate.settings import settings

    body = client.get("/healthz").json()
    # Scheduler is disabled in tests, so there is no tick age to report.
    assert body["scheduler_last_tick_age_s"] is None
    assert body["webhooks_enabled"] is bool(getattr(settings, "secret_key", None))
    assert "db_path" not in body  # the file's location is not for an unauthenticated caller
    assert body["version"]


def test_healthz_tick_age_when_scheduler_enabled(client, monkeypatch):
    from datetime import timedelta

    from slate import scheduler
    from slate.models import utcnow
    from slate.settings import settings

    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(scheduler, "last_tick_at", None)
    assert client.get("/healthz").json()["scheduler_last_tick_age_s"] is None  # not ticked yet

    monkeypatch.setattr(scheduler, "last_tick_at", utcnow() - timedelta(seconds=42))
    age = client.get("/healthz").json()["scheduler_last_tick_age_s"]
    assert 41 <= age < 60


def test_healthz_is_unauthenticated_and_fast(client):
    import time

    start = time.perf_counter()
    r = client.get("/healthz")
    assert r.status_code == 200
    assert time.perf_counter() - start < 0.5


def test_healthz_names_the_board_bundle(client, monkeypatch, tmp_path):
    # An open board compares this with the bundle it is running and reloads after a deploy.
    from slate.settings import settings

    monkeypatch.setattr(settings, "static_dir", None)
    assert client.get("/healthz").json()["board"] is None  # no board served

    (tmp_path / "index.html").write_text(
        '<script type="module" crossorigin src="/assets/index-AbC_12.js"></script>'
    )
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    assert client.get("/healthz").json()["board"] == "/assets/index-AbC_12.js"

    # A new deploy is seen without a restart (the file is read each time, and it is small).
    (tmp_path / "index.html").write_text('<script src="/assets/index-Next9.js"></script>')
    assert client.get("/healthz").json()["board"] == "/assets/index-Next9.js"
