"""Readiness needs a real probe, not recurring paid probes or silent stale UI state."""
# ruff: noqa: F811 -- imported pytest fixture names are injected into test functions.

from datetime import timedelta

from freezegun import freeze_time
from sqlalchemy import select

from slate.models import Event, utcnow
from test_agent_connections import API, exchange, issue, setup_connection  # noqa: F401


def paired(client, alex, setup_connection):
    agent, _, body = setup_connection
    data = exchange(
        client, issue(client, alex, body)["code"], capabilities=["task", "probe"]
    ).json()
    return (
        agent,
        API + "/" + data["connection"]["id"] + "/heartbeat",
        {"Authorization": "Bearer " + data["token"]},
    )


def test_ready_needs_probe_and_does_not_refresh_test_time(client, alex, setup_connection):
    agent, url, own = paired(client, alex, setup_connection)
    assert client.post(url, headers=own, json={"check": "ready"}).status_code == 422
    first = client.post(url, headers=own, json={"check": "probe_passed"})
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "ready"
    verified = first.json()["verified_at"]
    with freeze_time(utcnow() + timedelta(hours=2)):
        later = client.post(url, headers=own, json={"check": "ready"})
        assert later.status_code == 200, later.text
        assert later.json()["state"] == "ready"
        assert later.json()["verified_at"] == verified
        assert (
            client.get(API, headers=alex, params={"account_id": agent["id"]}).json()["connections"][
                0
            ]["state"]
            == "ready"
        )
        assert (
            client.post(url, headers=own, json={"check": "runtime_unavailable"}).json()[
                "verified_at"
            ]
            is None
        )
        assert client.post(url, headers=own, json={"check": "ready"}).status_code == 422


def test_healthy_heartbeats_emit_bounded_freshness_events(client, alex, setup_connection, db):
    _, url, own = paired(client, alex, setup_connection)
    first = client.post(url, headers=own, json={"check": "probe_passed"})
    assert first.status_code == 200, first.text

    def events():
        return list(db.scalars(select(Event).where(Event.type == "agent.connection_checked")))

    assert len(events()) == 1
    with freeze_time(utcnow() + timedelta(seconds=31)):
        assert client.post(url, headers=own, json={"check": "ready"}).status_code == 200
    with freeze_time(utcnow() + timedelta(seconds=65)):
        assert client.post(url, headers=own, json={"check": "ready"}).status_code == 200
    assert len(events()) == 2
    with freeze_time(utcnow() + timedelta(seconds=95)):
        assert client.post(url, headers=own, json={"check": "ready"}).status_code == 200
    assert len(events()) == 2
