"""Static checks on the GitHub workflows. Nothing here talks to GitHub.

The rules: every job starts with a read-only token that is not left in `.git/config`, every
action is pinned by commit, every job has a time limit, downloads are verified, and the
private-terms check never runs code from the pull request while it holds the denylist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: dict):
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            yield name, step


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "security.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_tokens_are_read_only(path):
    workflow = _load(path)
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        assert "permissions" not in job, f"{name} widens the token"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_by_commit(path):
    for job, step in _steps(_load(path)):
        if "uses" in step:
            assert PINNED.match(step["uses"]), f"{job}: {step['uses']} is not pinned by commit"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkouts_do_not_keep_the_token(path):
    for job, step in _steps(_load(path)):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step.get("with", {}).get("persist-credentials") is False, job


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_a_time_limit(path):
    for name, job in _load(path)["jobs"].items():
        assert 0 < job.get("timeout-minutes", 0) <= 60, name


def test_no_image_is_published():
    """The runbook builds on the server; a registry push would only widen CI's token."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "packages:" not in text
    assert "docker/" not in text


def test_private_terms_check_runs_trusted_code_on_the_pr_as_data():
    workflow = _load(ROOT / ".github" / "workflows" / "ci.yml")
    for name, step in _steps(workflow):
        if "PRIVATE_TERMS_SHA256" in str(step.get("env", {})):
            assert name == "private-terms", f"the denylist secret reaches job {name}"
    job = workflow["jobs"]["private-terms"]
    steps = job["steps"]
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    trusted = [s for s in checkouts if s["with"].get("path") == "trusted"]
    assert trusted and "base.sha" in trusted[0]["with"]["ref"]
    run = "\n".join(s.get("run", "") for s in steps)
    # No project code from the pull request runs: no uv, no pip, no npm, no script of its own.
    assert not re.search(r"\b(uv|pip|npm|npx)\b", run)
    assert "python3 -I pr/.trusted/check_private_terms.py" in run
    assert "trusted/scripts/check_private_terms.py pr/.trusted/" in run
    # Commit messages and author and committer addresses are checked too, not only files.
    assert "--message" in run and "%ae" in run and "%ce" in run


def test_gitleaks_download_is_verified_and_semgrep_is_pinned():
    text = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
    assert re.search(r"[0-9a-f]{64}  gitleaks_[\d.]+_linux_x64\.tar\.gz\" \| sha256sum -c", text)
    assert re.search(r"pip install --quiet semgrep==\d+\.\d+\.\d+", text)


def test_playwright_browser_is_cached_and_its_install_retried():
    steps = _load(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]["web"]["steps"]
    cache = [s for s in steps if s.get("uses", "").startswith("actions/cache@")]
    assert cache and "ms-playwright" in cache[0]["with"]["path"]
    run = "\n".join(s.get("run", "") for s in steps)
    assert "--with-deps" not in run  # one apt mirror failure no longer fails the job
    assert re.search(r"for attempt in 1 2 3", run)


def test_dependabot_watches_every_ecosystem():
    config = _load(ROOT / ".github" / "dependabot.yml")
    seen = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert seen == {("github-actions", "/"), ("uv", "/"), ("npm", "/web"), ("docker", "/")}
