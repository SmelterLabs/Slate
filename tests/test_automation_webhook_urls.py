"""A rule's webhook address is its author's and the admins': other members see only its host."""

from __future__ import annotations

from sqlalchemy import select

from slate.models import Automation, Event
from slate.service import access
from slate.service.core import event_payload
from test_permissions import world  # noqa: F401

API = "/api/v1"
SECRET_URL = "https://hooks.example.com/t/abc123secret?key=s3cr3t"
HOST_ONLY = "https://hooks.example.com"


def _rule(client, h, **body):
    body.setdefault("name", "Ping")
    body.setdefault("trigger", {"type": "task_created"})
    body.setdefault("actions", [{"type": "webhook", "url": SECRET_URL}])
    r = client.post(f"{API}/automations", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _urls(rule):
    return [a.get("url") for a in rule["actions"] if a["type"] == "webhook"]


def _people(world):  # noqa: F811
    author, ah = world["account"]("author", "member")
    other, oh = world["account"]("other", "member")
    return author, ah, other, oh


def test_only_the_author_and_admins_read_a_rules_webhook_address(client, alex, world):  # noqa: F811
    _, ah, _, oh = _people(world)
    rule = _rule(client, ah)
    assert _urls(rule) == [SECRET_URL]
    for h, want in ((ah, SECRET_URL), (alex, SECRET_URL), (oh, HOST_ONLY)):
        one = client.get(f"{API}/automations/{rule['id']}", headers=h).json()
        assert _urls(one) == [want]
        [listed] = [r for r in client.get(f"{API}/automations", headers=h).json()]
        assert _urls(listed) == [want]
        assert SECRET_URL not in str(listed) or want == SECRET_URL


def test_a_member_who_edits_the_rule_keeps_the_hidden_address(client, alex, db, world):  # noqa: F811
    _, ah, _, oh = _people(world)
    rule = _rule(client, ah)
    seen = client.get(f"{API}/automations/{rule['id']}", headers=oh).json()
    actions = [*seen["actions"], {"type": "add_tag", "tag": "pinged"}]
    r = client.patch(f"{API}/automations/{rule['id']}", json={"actions": actions}, headers=oh)
    assert r.status_code == 200, r.text
    assert _urls(r.json()) == [HOST_ONLY]
    stored = db.get(Automation, rule["id"])
    db.refresh(stored)
    assert [a.get("url") for a in stored.actions_json if a["type"] == "webhook"] == [SECRET_URL]


def test_rule_history_shows_other_members_only_the_host(client, alex, db, world):  # noqa: F811
    author, ah, other, oh = _people(world)
    rule = _rule(client, ah, actions=[{"type": "add_tag", "tag": "x"}])
    patched = [{"type": "webhook", "url": SECRET_URL}]
    r = client.patch(f"{API}/automations/{rule['id']}", json={"actions": patched}, headers=ah)
    assert r.status_code == 200, r.text

    def history(h):
        rows = client.get(f"{API}/events", params={"limit": 500}, headers=h).json()["events"]
        return [e for e in rows if e["type"].startswith("automation.")]

    assert SECRET_URL in str(history(ah)) and SECRET_URL in str(history(alex))
    mine = history(oh)
    assert mine and SECRET_URL not in str(mine) and HOST_ONLY in str(mine)

    # The live stream applies the same rule to the payload it already holds.
    published = [
        event_payload(e)
        for e in db.scalars(select(Event).where(Event.type.startswith("automation.")))
    ]
    assert SECRET_URL in str(published)
    for account, want in ((other, False), (author, True)):
        gate = access.EventGate(account["id"])
        gate.load(db)
        live = [gate.project(db, p) for p in published]
        assert all(p is not None for p in live)
        assert (SECRET_URL in str(live)) is want
        assert HOST_ONLY in str(live)


def test_deleting_the_rule_keeps_its_address_out_of_other_members_history(
    client,
    alex,
    world,  # noqa: F811
):
    _, ah, _, oh = _people(world)
    rule = _rule(client, ah)
    assert client.delete(f"{API}/automations/{rule['id']}", headers=ah).status_code == 204
    rows = client.get(f"{API}/events", params={"limit": 500}, headers=oh).json()["events"]
    gone = [e for e in rows if e["type"] == "automation.deleted"]
    assert gone and SECRET_URL not in str(gone) and HOST_ONLY in str(gone)
    rows = client.get(f"{API}/events", params={"limit": 500}, headers=ah).json()["events"]
    assert SECRET_URL in str([e for e in rows if e["type"] == "automation.deleted"])
