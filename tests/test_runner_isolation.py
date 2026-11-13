"""The runner keeps agents apart and keeps going.

- `--config`: each agent's command gets only that agent's token and environment: the runner's
  basic environment (PATH, home, temp, locale), the agent's own `env_file` and `env` table. The
  shared top-level `env_file` and the rest of the runner's environment only fill `${VAR}` and
  `token_env`; no other agent's token reaches a command, whatever file it is in.
- Card text never reaches the command's environment (it is on stdin), so an `exec` line cannot
  expand a title into a shell command.
- An undelivered result is retried on its own with backoff, and a fresh grant for a card whose
  earlier result could not be delivered (the server settled that run) runs the card instead of
  handing it back, so the agent's other work is never starved.
"""

from __future__ import annotations

import io
import json
from datetime import timedelta

import httpx
import pytest

from conftest import API
from slate.models import utcnow
from slate.service import work
from test_clients import _assign, _runner
from test_runner_lifecycle import Child

TOKEN_A = "slate_aaaaaaaaaaaa_" + "A" * 40
TOKEN_B = "slate_bbbbbbbbbbbb_" + "B" * 40


def _config(tmp_path, body: str):
    from slate_runner import load_config

    (tmp_path / "runner.toml").write_text(body)
    return {a["name"]: a for a in load_config(tmp_path / "runner.toml")}


def _child_env(agent: dict) -> dict[str, str]:
    from slate_runner import Runner

    r = Runner(agent["url"], agent["token"], agent["command"], env=agent["env"], state_path=None)
    return r._env({"item": {"id": "i1", "title": "t", "list_id": "l1"}})


# --- each agent's own environment -----------------------------------------------------------


def test_a_shared_env_file_never_reaches_another_agents_command(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_HOST_SECRET", "from-the-runner-shell")
    monkeypatch.setenv("SLATE_TOKEN_FROM_SHELL", "slate_cccc_" + "C" * 40)
    (tmp_path / "secrets.env").write_text(
        f"SLATE_TOKEN_MY_AGENT={TOKEN_A}\nSLATE_TOKEN_OTHER_AGENT={TOKEN_B}\n"
        "API_SERVER_KEY=runtime-key-for-my-agent\n"
    )
    (tmp_path / "other.env").write_text("OTHER_ONLY=for-other-agent\n")
    agents = _config(
        tmp_path,
        'url = "https://slate.example.com"\n'
        'env_file = "secrets.env"\n'
        '[[agent]]\nname = "my-agent"\ntoken_env = "SLATE_TOKEN_MY_AGENT"\n'
        'exec = "python a.py"\n'
        'env = { RUNTIME_KEY = "${API_SERVER_KEY}" }\n'
        '[[agent]]\nname = "other-agent"\ntoken_env = "SLATE_TOKEN_OTHER_AGENT"\n'
        'env_file = "other.env"\nexec = "python b.py"\n',
    )
    mine, other = _child_env(agents["my-agent"]), _child_env(agents["other-agent"])

    assert mine["SLATE_TOKEN"] == TOKEN_A and other["SLATE_TOKEN"] == TOKEN_B
    for env, foreign in ((mine, TOKEN_B), (other, TOKEN_A)):
        assert foreign not in env.values()
        assert not [k for k in env if k.startswith("SLATE_TOKEN_")]
        # Neither the shared file nor the runner's own environment is passed through...
        assert "API_SERVER_KEY" not in env and "RUNNER_HOST_SECRET" not in env
        # ...but what a command needs to run at all is.
        assert env["PATH"]
    # What an agent names in its own env table or env_file is its own.
    assert mine["RUNTIME_KEY"] == "runtime-key-for-my-agent" and "OTHER_ONLY" not in mine
    assert other["OTHER_ONLY"] == "for-other-agent" and "RUNTIME_KEY" not in other


def test_another_agents_token_is_dropped_wherever_it_was_put(tmp_path):
    """A misconfigured env table or agent env_file that names another agent's token."""
    (tmp_path / "b.token").write_text(TOKEN_B + "\n")
    (tmp_path / "a.env").write_text(f"MY_TOKEN={TOKEN_A}\nSTRAY={TOKEN_B}\n")
    agents = _config(
        tmp_path,
        'url = "https://slate.example.com"\nexec = "python run.py"\n'
        '[[agent]]\nname = "my-agent"\nenv_file = "a.env"\ntoken_env = "MY_TOKEN"\n'
        'env = { BORROWED = "' + TOKEN_B + '" }\n'
        '[[agent]]\nname = "other-agent"\ntoken_file = "b.token"\n',
    )
    env = _child_env(agents["my-agent"])
    assert env["SLATE_TOKEN"] == TOKEN_A
    assert TOKEN_B not in env.values()
    assert "STRAY" not in env and "BORROWED" not in env and "MY_TOKEN" not in env


def test_a_single_agent_runner_passes_no_other_slate_token(monkeypatch):
    from slate_runner import Runner

    monkeypatch.setenv("SLATE_TOKEN_OTHER_AGENT", TOKEN_B)
    r = Runner("https://slate.example.com", TOKEN_A, "python run.py", state_path=None)
    env = r._env({"item": {"id": "i1", "title": "t", "list_id": "l1"}})
    assert env["SLATE_TOKEN"] == TOKEN_A and "SLATE_TOKEN_OTHER_AGENT" not in env


# --- card text stays out of the shell -------------------------------------------------------


def test_the_card_title_is_not_in_the_commands_environment():
    from slate_runner import Runner

    r = Runner("https://slate.example.com", TOKEN_A, "python run.py", state_path=None)
    env = r._env({"item": {"id": "i1", "title": "x & calc", "list_id": "l1"}})
    assert "SLATE_ITEM_TITLE" not in env
    assert "x & calc" not in env.values()


@pytest.mark.parametrize("ref", ["%SLATE_ITEM_TITLE%", "$SLATE_ITEM_TITLE", "${SLATE_ITEM_TITLE}"])
def test_an_exec_line_that_expands_the_title_is_refused(tmp_path, ref):
    import slate_runner

    (tmp_path / "t").write_text("tok")
    with pytest.raises(ValueError, match="stdin"):
        _config(
            tmp_path,
            f'url = "u"\nexec = "python run.py {ref}"\n[[agent]]\nname = "a"\ntoken_file = "t"\n',
        )
    assert slate_runner.main(["--url", "u", "--exec", f"python run.py {ref}"]) == 3


# --- an undelivered result ------------------------------------------------------------------


def _fail_delivery(runner, monkeypatch, task):
    import slate_runner

    child = Child()
    child.stdout = io.StringIO('{"outcome":"done","reply":"Finished"}\n')
    child.finish()
    monkeypatch.setattr(slate_runner.subprocess, "Popen", lambda *a, **k: child)
    original = runner.call

    def down(method, path, body=None):
        if path.endswith("/finish"):
            raise httpx.ConnectError("server restarting")
        return original(method, path, body)

    monkeypatch.setattr(runner, "call", down)
    assert runner.run(task) == "delivery_failed"
    monkeypatch.setattr(runner, "call", original)


def test_an_undelivered_result_is_retried_with_backoff(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    _fail_delivery(runner, monkeypatch, runner.take(item["id"]))
    assert runner._records[item["id"]]["state"] == "delivery_failed"

    original = runner.call
    attempts = []

    def flaky(method, path, body=None):
        if path.endswith("/finish"):
            attempts.append(path)
            if len(attempts) <= 3:
                raise httpx.ConnectError("still down")
        return original(method, path, body)

    monkeypatch.setattr(runner, "call", flaky)
    now = 1000.0
    assert runner.retry_due(now) == []  # the first retry waits a little
    assert runner.retry_due(now + 5) == [item["id"]] and len(attempts) == 3
    assert runner._records[item["id"]]["state"] == "delivery_failed"
    assert runner.retry_due(now + 6) == []  # backed off
    assert runner.retry_due(now + 5 + 10) == [item["id"]]
    assert runner._records[item["id"]]["state"] == "delivered"
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["completed_at"]
    assert runner.retry_due(now + 1000) == []


def test_a_fresh_grant_after_an_undelivered_result_runs_the_card(
    client, alex, todo, make_account, tmp_path, monkeypatch, db
):
    """The server settled the earlier run (its lease expired, then a person cleared the
    badge): the card's new grant is run, not handed back, and the agent's other work follows."""
    _, h = make_account("agent")
    urgent = _assign(client, alex, todo, title="A urgent")
    client.patch(f"{API}/items/{urgent['id']}", json={"priority": "urgent"}, headers=alex)
    low = _assign(client, alex, todo, title="B low")
    runner = _runner(client, h, tmp_path)
    first = runner.take(urgent["id"])
    _fail_delivery(runner, monkeypatch, first)
    work.expire_leases(db, utcnow() + timedelta(hours=1))
    r = client.post(
        f"{API}/items/{urgent['id']}/report", json={"state": "idle", "comment": False}, headers=alex
    )
    assert r.status_code == 200, r.text

    again = runner.take()
    assert again is not None and again["item"]["id"] == urgent["id"]
    assert again["lease"]["run_id"] != first["lease"]["run_id"]
    saved = json.loads(runner.state_path.read_text())["runs"][urgent["id"]]
    assert saved["state"] == "taken" and saved["run_id"] == again["lease"]["run_id"]
    runner._claimed.discard(urgent["id"])  # (not run here)
    nxt = runner.take()
    assert nxt is not None and nxt["item"]["id"] == low["id"]


def test_a_restarted_runner_does_not_fence_keys_it_never_ran(
    client, alex, todo, make_account, tmp_path
):
    """A record left `taken` or `running` by a runner that died is unknown remote state: the
    server keeps that run quarantined, so the next process treats it like any interrupted run."""
    import slate_runner

    _, h = make_account("agent")
    runner = _runner(client, h, tmp_path)
    runner._record("i-crashed", run_id="old", state="running", remote_state="unknown")
    runner._record("i-taken", run_id="old2", state="taken", remote_state="not_started")
    restarted = _runner(client, h, tmp_path)
    for key in ("i-crashed", "i-taken"):
        assert restarted._records[key]["state"] == "reconciliation_required"
        assert restarted._records[key]["remote_state"] == "unknown"
    assert slate_runner  # the module is the one under test


def test_retry_delivery_from_the_command_line(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    import slate_runner

    _, h = make_account("agent")
    item = _assign(client, alex, todo)
    runner = _runner(client, h, tmp_path)
    _fail_delivery(runner, monkeypatch, runner.take(item["id"]))
    token = h["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("SLATE_TOKEN", token)
    real = slate_runner.Runner

    def with_test_client(*a, **k):
        return real(*a, **{**k, "client": client})

    monkeypatch.setattr(slate_runner, "Runner", with_test_client)
    state = str(tmp_path / "runner.json")
    argv = ["--url", "http://testserver", "--state", state, "--retry-delivery", item["id"]]
    assert slate_runner.main(argv) == 0
    assert json.loads((tmp_path / "runner.json").read_text())["runs"][item["id"]]["state"] == (
        "delivered"
    )
    assert slate_runner.main(argv) == 2  # nothing left to deliver
