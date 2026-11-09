from __future__ import annotations

import os

# Tests always run against an in-memory database; set before slate.settings is imported.
os.environ.setdefault("SLATE_DATABASE_PATH", ":memory:")
os.environ.setdefault("SLATE_SCHEDULER_ENABLED", "false")
os.environ.setdefault("SLATE_AUTOMATIONS_ENABLED", "false")
os.environ.setdefault("SLATE_PUSH_ENABLED", "false")
# One fixed sample zone with daylight saving, so date rules are tested across a clock change.
os.environ.setdefault("SLATE_TIMEZONE", "America/Chicago")
# Tests hammer the API far faster than any agent; the rate-limit tests turn limits back on.
os.environ.setdefault("SLATE_RATE_LIMIT_ENABLED", "false")
# A fixed test-only key (32 bytes, hex) so webhook secrets can be encrypted in tests.
os.environ.setdefault("SLATE_SECRET_KEY", "00" * 16 + "11" * 16)
# Receivers in tests are on example.com names behind a mock transport; private targets stay
# off (the default) so tests/test_security_regressions.py can check the refusal.
os.environ.setdefault("SLATE_PUSH_ENDPOINT_HOSTS", '["push.example.com"]')

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from slate import models  # noqa: E402, F401
from slate.app import create_app  # noqa: E402
from slate.db import Base, SessionLocal, engine  # noqa: E402
from slate.service import item_cache  # noqa: E402

item_cache.VERIFY = True  # every cached GET /items is checked against a fresh build


@pytest.fixture(autouse=True)
def fresh_schema():
    Base.metadata.create_all(engine)
    item_cache.cache.clear()
    yield
    # lists.default_status_id <-> statuses.list_id is circular, so drop with FKs off.
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(conn)
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


API = "/api/v1"


@pytest.fixture
def alex(client) -> dict[str, str]:
    """Auth headers for `alex`, the bootstrapped admin."""
    r = client.post(f"{API}/bootstrap", json={"handle": "alex", "display_name": "Alex"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def make_account(client, alex):
    """make_account(handle, scopes) -> (account json, auth headers)."""

    def make(handle: str = "agent", scopes: tuple[str, ...] = ("write",), kind: str = "agent"):
        acct = client.post(
            f"{API}/accounts",
            json={"handle": handle, "display_name": handle.title(), "kind": kind},
            headers=alex,
        ).json()
        tok = client.post(
            f"{API}/accounts/{acct['id']}/tokens", json={"scopes": list(scopes)}, headers=alex
        ).json()
        return acct, {"Authorization": f"Bearer {tok['token']}"}

    return make


@pytest.fixture
def todo(client, alex) -> dict:
    """A `Personal` section with a `Todo` list (default statuses). Returns the list json."""
    section = client.post(
        f"{API}/sections", json={"name": "Personal", "audience": "personal"}, headers=alex
    ).json()
    r = client.post(
        f"{API}/lists", json={"section_id": section["id"], "name": "Todo"}, headers=alex
    )
    assert r.status_code == 201, r.text
    return r.json()
