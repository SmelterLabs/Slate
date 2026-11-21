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
    "Wait for Friday.\n**SLATE: later**",
    "I replied.\nSLATE: ANSWERED.",
    "The file is ready.\nSLATE: ask Alex: Start the import now? [Yes | No | Not yet]",
    "SLATE: ask alex: Start now? [Yes || No]",
    "SLATE: ask alex: Start now? [Yes [now] | No]",
    "SLATE: ask alex: [Yes | No]",
    "`SLATE: ask @alex: Which file should I use?`",
    "Need you.\nSLATE: blocked waiting_on @alex.",
    "SLATE: failed",
    "SLATE: done\nmore text after",
    "SLATE: finished",
    "no line at all",
    "",
]


def test_example_copies_the_service_pattern_exactly():
    adapter = ADAPTERS[1]
    assert adapter.SLATE_LINE.pattern == brief._LINE.pattern
    assert adapter.SLATE_LINE.flags == brief._LINE.flags


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("text", CASES)
def test_every_adapter_reads_the_line_as_slate_does(adapter, text):
    mine = adapter.closing(text)
    theirs = brief.closing_line(text)
    assert ((mine[0], mine[1], mine[3], tuple(mine[4] or ())) if mine else None) == (
        (theirs.outcome, theirs.waiting_on, theirs.question, theirs.options) if theirs else None
    )
