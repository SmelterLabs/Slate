"""Which build this is: a revision number (commits on the branch, so it only ever grows) and
the short commit it was built from. Shown in the board's top-left corner and on /healthz.

Looked up once, in this order: `SLATE_REV`/`SLATE_COMMIT` in the environment; `build.json`
next to this file (the Docker build writes it from the repo's `.git`); `git` in a checkout
(dev servers); else `dev`. Not a Settings field on purpose: nobody sets it by hand."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

BUILD_FILE = Path(__file__).with_name("build.json")
UNKNOWN = "dev"


class Build(TypedDict):
    rev: str
    commit: str | None


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def read_build(
    env: dict[str, str] | None = None, file: Path = BUILD_FILE, use_git: bool = True
) -> Build:
    """The build from the environment, the file, then git; `dev` when none knows."""
    env = os.environ if env is None else env
    rev = env.get("SLATE_REV", "").strip()
    if rev:
        return {"rev": rev, "commit": env.get("SLATE_COMMIT", "").strip() or None}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and str(data.get("rev", "")).strip():
            commit = data.get("commit")
            return {"rev": str(data["rev"]).strip(), "commit": str(commit) if commit else None}
    except (OSError, ValueError):
        pass
    if use_git:
        commit = _git("rev-parse", "--short", "HEAD")
        count = _git("rev-list", "--count", "HEAD")
        # A shallow clone (CI, some cloud sessions) counts only what it fetched: say so.
        shallow = _git("rev-parse", "--is-shallow-repository") == "true"
        if commit:
            return {"rev": count if count and not shallow else UNKNOWN, "commit": commit}
    return {"rev": UNKNOWN, "commit": None}


@lru_cache(maxsize=1)
def build_info() -> Build:
    return read_build()
