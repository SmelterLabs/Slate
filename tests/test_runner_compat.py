"""A runner older than the API is refused with a clear 'update' message, not a silent 422."""

import sys
from pathlib import Path

from slate import runner_compat

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))
import slate_runner  # noqa: E402

API = "/api/v1"


def test_the_shipped_runner_speaks_what_the_server_needs():
    # Bump MIN_RUNNER_PROTOCOL and slate_runner.RUNNER_PROTOCOL together when an API change
    # breaks older runners; this fails if the server's floor is raised past the client.
    assert slate_runner.RUNNER_PROTOCOL >= runner_compat.MIN_RUNNER_PROTOCOL
    assert slate_runner.__version__.startswith(f"0.{slate_runner.RUNNER_PROTOCOL}.")


def test_an_old_runner_is_refused_with_how_to_update(client, alex):
    old = runner_compat.MIN_RUNNER_PROTOCOL - 1
    r = client.post(
        f"{API}/work/next", json={}, headers={**alex, runner_compat.HEADER: f"slate-runner/{old}"}
    )
    assert r.status_code == 426
    detail = r.json()["detail"]
    assert "update" in detail.lower() and str(runner_compat.MIN_RUNNER_PROTOCOL) in detail


def test_a_current_runner_and_a_plain_client_pass(client, alex):
    cur = f"slate-runner/{runner_compat.MIN_RUNNER_PROTOCOL}"
    assert client.get(f"{API}/me", headers={**alex, runner_compat.HEADER: cur}).status_code == 200
    assert client.get(f"{API}/me", headers=alex).status_code == 200  # no header: not a runner
    garbled = {**alex, runner_compat.HEADER: "something-else"}
    assert client.get(f"{API}/me", headers=garbled).status_code == 200


def test_healthz_says_which_runner_it_needs(client):
    assert client.get("/healthz").json()["min_runner_protocol"] == (
        runner_compat.MIN_RUNNER_PROTOCOL
    )


def test_the_runner_sends_its_protocol():
    r = slate_runner.Runner("http://x", "t", "echo")
    assert r.headers[runner_compat.HEADER] == f"slate-runner/{slate_runner.RUNNER_PROTOCOL}"
