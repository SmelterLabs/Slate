"""Slate's own handle and "is this a workspace agent" each have one definition."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

from slate import models
from slate.models import Account
from slate.service import access, chat, due, guests

SRC = Path(__file__).resolve().parents[1] / "src" / "slate"


def _sources():
    return {p: p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}


def test_slates_own_handle_is_written_once():
    assert due.SYSTEM_HANDLE == models.SYSTEM_HANDLE == "slate"
    defined = [p.name for p, text in _sources().items() if "SYSTEM_HANDLE =" in text]
    assert defined == ["models.py"]
    # No module compares a handle with the bare word instead of the constant.
    literal = re.compile(r"""handle\s*[!=]=\s*["']slate["']""")
    assert [p.name for p, text in _sources().items() if literal.search(text)] == []


def test_one_workspace_agent_predicate():
    assert not hasattr(chat, "workspace_agent")
    assert not hasattr(access, "_workspace_agents")
    # "An agent that is not a guest" is spelled out in guests.py only.
    rule = re.compile(
        r"""(kind\s*==\s*(AccountKind\.agent|["']agent["'])[^\n]*\n?[^\n]*"""
        r"""role\s*!=\s*(Role\.guest|["']guest["']))"""
    )
    spelled = sorted(p.name for p, text in _sources().items() if rule.search(text))
    assert spelled == ["guests.py"]


def test_the_sql_and_python_forms_agree(client, alex, db):
    def account(handle, kind, role):
        body = {"handle": handle, "display_name": handle, "kind": kind, "role": role}
        r = client.post("/api/v1/accounts", json=body, headers=alex)
        assert r.status_code == 201, r.text
        return r.json()

    account("my-agent", "agent", "member")
    account("other-agent", "agent", "guest")
    due.system_actor(db)
    db.commit()
    db.expire_all()
    every = db.scalars(select(Account)).all()
    by_python = {a.id for a in every if guests.workspace_agent(a)}
    by_sql = set(db.scalars(guests.workspace_agents()))
    assert by_python == by_sql == guests.workspace_agent_ids(db)
    # Not alex (a person), not other-agent (a project's guest agent), not Slate's own account.
    assert {a.handle for a in every if a.id in by_sql} == {"my-agent"}
