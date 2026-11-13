"""The tests that look at Git pass or skip in a source archive, which has no `.git`.

README offers a source archive as well as a clone. The tests below run the Git-aware test files
against a copy of the tree with no `.git`; each test there must pass or skip, never fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_AWARE = ["test_private_terms.py", "test_build.py", "test_portable_package.py"]


def test_git_aware_tests_skip_outside_a_checkout(tmp_path):
    tree = tmp_path / "archive"
    (tree / "tests").mkdir(parents=True)
    for name in ["conftest.py", *GIT_AWARE]:
        shutil.copyfile(ROOT / "tests" / name, tree / "tests" / name)
    shutil.copytree(ROOT / "scripts", tree / "scripts")
    (tree / "src" / "slate").mkdir(parents=True)
    shutil.copyfile(ROOT / "src/slate/build.py", tree / "src/slate/build.py")
    for name in ["pyproject.toml", "uv.lock", "compose.yaml", "Dockerfile", ".dockerignore"]:
        shutil.copyfile(ROOT / name, tree / name)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SLATE_PRIVATE"))}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)  # Git must not find a repository above
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"]
        + ["--basetemp", str(tmp_path / "inner")]
        # The wheel-building tests need no Git and are slow; the rest run.
        + ["-k", "not outside_checkout and not sdist_is_minimal and not installed_wheel"]
        + [f"tests/{name}" for name in GIT_AWARE],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not a git checkout" in result.stdout
