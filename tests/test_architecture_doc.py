"""ARCHITECTURE.md stays true to the tree for the parts a machine can check: every source
module is in its Modules table, nothing in the table is gone, every doc it cites exists, and
every topic doc is in its index."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
SRC = ROOT / "src" / "slate"


def _section(title: str) -> str:
    start = DOC.index(f"\n{title}\n")
    end = re.compile(r"^## ", re.M).search(DOC, start + len(title) + 2)
    return DOC[start : end.start() if end else len(DOC)]


MODULES = _section("### Modules")
# `app.py`, `service/core.py`, `doc_tree.py` (a bare name after a `service/x.py` in the same row).
NAMED = set(re.findall(r"`((?:service/|api/)?[a-z_]+\.py)`", MODULES))
# The routers the last row lists by resource name.
REST = re.search(r"\| `api/\*\.py` \(the rest\) \| (.*) \|", MODULES).group(1)


def _stems(folder: Path) -> set[str]:
    return {p.stem for p in folder.glob("*.py") if not p.stem.startswith("__")}


def test_every_source_module_is_in_the_modules_table():
    bare = {name for name in NAMED if "/" not in name}
    missing = sorted(f"{s}.py" for s in _stems(SRC) if f"{s}.py" not in bare)
    assert not missing, f"add to ARCHITECTURE.md Modules: {missing}"
    service = {n.removeprefix("service/") for n in NAMED} | bare
    missing = sorted(f"service/{s}.py" for s in _stems(SRC / "service") if f"{s}.py" not in service)
    assert not missing, f"add to ARCHITECTURE.md Modules: {missing}"
    api = {n.removeprefix("api/") for n in NAMED if n.startswith("api/")}
    listed = set(re.findall(r"[a-z_]+", REST))
    missing = sorted(
        f"api/{s}.py" for s in _stems(SRC / "api") if f"{s}.py" not in api and s not in listed
    )
    assert not missing, f"add to ARCHITECTURE.md Modules: {missing}"


def test_every_module_the_table_names_exists():
    known = (
        {f"{s}.py" for s in _stems(SRC)}
        | {f"{s}.py" for s in _stems(SRC / "service")}
        | {f"service/{s}.py" for s in _stems(SRC / "service")}
        | {f"api/{s}.py" for s in _stems(SRC / "api")}
    )
    gone = sorted(NAMED - known)
    assert not gone, f"ARCHITECTURE.md Modules names files that do not exist: {gone}"


def test_every_cited_doc_exists_and_every_topic_doc_is_indexed():
    cited = set(re.findall(r"`(docs/[A-Za-z0-9_./-]+\.md)`", DOC))
    gone = sorted(c for c in cited if not (ROOT / c).is_file())
    assert not gone, f"ARCHITECTURE.md cites docs that do not exist: {gone}"
    index = _section("## Topic docs (the rest of the internals)")
    unlisted = sorted(
        f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md") if f"docs/{p.name}" not in index
    )
    assert not unlisted, f"add to ARCHITECTURE.md Topic docs: {unlisted}"
