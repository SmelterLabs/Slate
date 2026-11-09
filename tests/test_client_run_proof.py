import sys
from pathlib import Path

import pytest

from conftest import API
from test_clients import _assign, _token

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))
from slate_client import Slate, SlateError  # noqa: E402


def test_python_loop_propagates_proof_and_completes(client, alex, todo, make_account):
    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    sdk = Slate("http://testserver", _token(h), client=client)
    sdk.work_forever(lambda task: "verified", once=True, wait=0)
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["completed_at"]


def test_legacy_python_loop_cannot_duplicate_same_account_work(client, alex, todo, make_account):
    _, h = make_account("agent")
    _assign(client, alex, todo)
    first = Slate("http://testserver", _token(h), client=client)
    second = Slate("http://testserver", _token(h), client=client)
    duplicates = []

    def handle(item):
        second.run_forever(lambda duplicate: duplicates.append(duplicate["id"]), once=True)
        return "finished"

    first.run_forever(handle, once=True)
    assert duplicates == []


def test_python_loop_permission_loss_does_not_finish(client, alex, todo, make_account, monkeypatch):
    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    sdk = Slate("http://testserver", _token(h), client=client)

    def lost(*a, **kw):
        raise SlateError(403, "permission lost")

    monkeypatch.setattr(sdk, "heartbeat", lost)
    with pytest.raises(SlateError, match="permission lost"):
        sdk.work_forever(lambda task: "must not deliver", once=True, wait=0)
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["completed_at"] is None
