"""The `SLATE:` closing line has one grammar: the one Slate asks for in its briefs and reads
back from comments (`slate.service.brief`). Each adapter keeps its own copy, because each is one
self-contained file a runner can copy anywhere; this test keeps the copies from drifting."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from slate.service import brief

HERE = Path(__file__).resolve().parent


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(rel.replace("/", "_")[:-3], HERE / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADAPTERS = [_load("hermes/run_task.py"), _load("runner-example/run_task.py")]

CASES = [
    "All set.\nSLATE: done",
    "Here it is.\n\n`SLATE: review`\n",
    "Need you.\nSLATE: blocked waiting_on @alex.",
    "Need you.\nSLATE: Blocked waiting on alex",
    "Wait for Friday.\n**SLATE: later**",
    "SLATE: done\nmore text after",
    "SLATE: finished",
    "no line at all",
    "",
]


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda m: m.__name__)
def test_every_adapter_reads_the_line_as_slate_does(adapter):
    assert adapter.SLATE_LINE.pattern == brief._LINE.pattern
    assert adapter.SLATE_LINE.flags == brief._LINE.flags
    for text in CASES:
        mine = adapter.closing(text)
        theirs = brief.closing_line(text)
        assert (mine[:2] if mine else None) == (
            (theirs.outcome, theirs.waiting_on) if theirs else None
        ), text
