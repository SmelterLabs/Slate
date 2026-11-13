"""The build revision: environment, then build.json, then git, then `dev`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import build
from slate.build import read_build


def test_env_wins(tmp_path: Path):
    (tmp_path / "build.json").write_text(json.dumps({"rev": "5", "commit": "aaaaaaa"}))
    b = read_build({"SLATE_REV": "42", "SLATE_COMMIT": "abc1234"}, tmp_path / "build.json")
    assert b == {"rev": "42", "commit": "abc1234"}


def test_build_file_from_the_image(tmp_path: Path):
    (tmp_path / "build.json").write_text(json.dumps({"rev": 123, "commit": "deadbee"}))
    assert read_build({}, tmp_path / "build.json", use_git=False) == {
        "rev": "123",
        "commit": "deadbee",
    }


def test_git_in_a_checkout(tmp_path: Path):
    # This test runs inside the repo, so git answers with a short sha, and with the commit
    # count unless the clone is shallow (CI), where a count would be a lie.
    if build._git("rev-parse", "HEAD") is None:
        pytest.skip("not a git checkout")
    b = read_build({}, tmp_path / "missing.json")
    assert b["rev"] == "dev" or (b["rev"].isdigit() and int(b["rev"]) > 0)
    assert b["commit"] and len(b["commit"]) >= 7


def test_dev_when_nothing_knows(tmp_path: Path):
    (tmp_path / "build.json").write_text("not json")
    assert read_build({}, tmp_path / "build.json", use_git=False) == {"rev": "dev", "commit": None}


def test_healthz_carries_the_rev(client):
    body = client.get("/healthz").json()
    assert body["rev"] and (body["rev"] == "dev" or body["rev"].isdigit())
    assert "commit" in body
