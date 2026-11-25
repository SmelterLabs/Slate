"""Slate knows nothing about any agent runtime (AGENTS.md rule 5), and `integrations/` is
optional: nothing under `src/` names a runtime, nothing under `tests/` reaches into
`integrations/` (CI runs this suite with the folder moved away), and the runner package builds
without it (the bundled reference adapters are then left out)."""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Agent runtimes and agent apps Slate must never know by name.
RUNTIMES = re.compile(r"\b(hermes|claude|codex|openclaw|gemini|langchain|crewai)\b", re.I)
# Never allowed anywhere in src/, even inside built-in AI add provider code.
STRICT_RUNTIMES = re.compile(
    r"\b(hermes|openclaw|langchain|crewai|claude\s+code|codex\s+cli)\b", re.I
)
# Built-in AI add may name model providers (Anthropic Claude, OpenAI, Gemini, Ollama).
AI_ADD_FILES = frozenset(
    {
        "src/slate/service/aiadd.py",
        "src/slate/service/ai_settings.py",
        "src/slate/settings.py",
        "src/slate/api/items.py",
        "src/slate/api/settings.py",
    }
)


def _files(folder: str, *suffixes: str) -> list[Path]:
    return [p for p in (ROOT / folder).rglob("*") if p.suffix in suffixes and p.is_file()]


def test_src_names_no_agent_runtime():
    hits = []
    for p in _files("src", ".py", ".html", ".js"):
        rel = p.relative_to(ROOT).as_posix()
        pattern = STRICT_RUNTIMES if rel in AI_ADD_FILES else RUNTIMES
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{rel}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_tests_never_reach_into_integrations():
    me = Path(__file__).resolve()
    hits = [
        str(p.relative_to(ROOT))
        for p in _files("tests", ".py", ".mjs")
        if p.resolve() != me and "integrations" in p.read_text(encoding="utf-8")
    ]
    assert not hits


@pytest.fixture
def build_hook(monkeypatch):
    """clients/python/hatch_build.py with a stand-in for hatchling's hook base class."""
    names = ["hatchling", "hatchling.builders", "hatchling.builders.hooks"]
    names += ["hatchling.builders.hooks.plugin", "hatchling.builders.hooks.plugin.interface"]
    for name in names:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    class BuildHookInterface:
        def __init__(self, root: str) -> None:
            self.root = root

    sys.modules[names[-1]].BuildHookInterface = BuildHookInterface
    path = ROOT / "clients" / "python" / "hatch_build.py"
    spec = importlib.util.spec_from_file_location("hatch_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CustomBuildHook


def _layout(tmp_path: Path, with_integrations: bool) -> Path:
    client = tmp_path / "clients" / "python"
    client.mkdir(parents=True)
    (client / "run_one_example.py").write_text("print()\n")
    if with_integrations:
        for rel in ("hermes/run_task.py", "hermes/run_task_cli.py", "runner-example/run_task.py"):
            f = tmp_path / "integrations" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("# adapter\n")
    return client


def test_the_runner_package_builds_without_integrations(tmp_path, build_hook):
    data = {"force_include": {}}
    build_hook(str(_layout(tmp_path, with_integrations=False))).initialize("0", data)
    assert list(data["force_include"].values()) == ["slate_runner_assets/run_one_example.py"]


def test_the_runner_package_bundles_the_adapters_when_they_are_there(tmp_path, build_hook):
    data = {"force_include": {}}
    build_hook(str(_layout(tmp_path, with_integrations=True))).initialize("0", data)
    assert sorted(data["force_include"].values()) == [
        "slate_runner_assets/hermes/run_task.py",
        "slate_runner_assets/hermes/run_task_cli.py",
        "slate_runner_assets/run_one_example.py",
        "slate_runner_assets/runner-example/run_task.py",
    ]
