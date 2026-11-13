"""The application shell: the lifespan, which engines it starts, the API docs page and the
public health summary."""

from __future__ import annotations

import inspect
import re

from fastapi.testclient import TestClient

from slate import app as app_module
from slate import emailer, mailer
from slate.app import create_app
from slate.settings import settings


def test_lifespan_is_the_context_manager_and_warm_up_is_a_plain_function():
    assert not hasattr(app_module._warm_items, "__wrapped__")
    assert app_module._warm_items() is None  # a thread target that runs and returns
    assert hasattr(app_module.lifespan, "__wrapped__")
    assert inspect.isasyncgenfunction(app_module.lifespan.__wrapped__)


def test_email_runs_on_smtp_settings_alone(monkeypatch):
    """Push off must not switch email off, and Health must name the real reason."""
    from slate import monitor

    started: list[str] = []
    monkeypatch.setattr(settings, "push_enabled", False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "slate@example.com")
    monkeypatch.setattr(emailer, "start", lambda: started.append("email") or None)
    assert mailer.enabled()
    with TestClient(create_app()):
        pass
    assert started == ["email"]

    monkeypatch.setattr(settings, "smtp_host", "")
    with TestClient(create_app()):
        reason = monitor.task_states()["email"]["detail"]
    assert "SLATE_SMTP_HOST" in reason and "PUSH" not in reason


def test_api_docs_page_pins_its_script_and_sets_a_policy(client):
    r = client.get("/docs")
    assert r.status_code == 200
    html = r.text
    scripts = re.findall(r"<script[^>]*src=\"([^\"]+)\"[^>]*>", html)
    assert scripts and all("@5." in s and s.count(".") >= 3 for s in scripts), scripts
    for tag in re.findall(r"<(?:script|link)[^>]*(?:src|href)=\"https://[^>]*>", html):
        if "favicon" in tag:
            continue
        assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag, tag
    csp = r.headers["content-security-policy"]
    assert "script-src" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_public_health_summary_is_cached_briefly(client, monkeypatch):
    from slate.service import health

    calls = {"n": 0}
    real = health.summary

    def counted(session):
        calls["n"] += 1
        return real(session)

    monkeypatch.setattr(health, "summary", counted)
    for _ in range(5):
        assert client.get("/health").status_code in (200, 503)
    assert calls["n"] == 1
