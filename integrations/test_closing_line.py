"""The `SLATE:` closing line has one grammar: the one Slate asks for in its briefs and reads
back from comments (`slate.service.brief`). Each adapter keeps its own copy, because each is one
self-contained file a runner can copy anywhere; this test keeps the copies from drifting."""

from __future__ import annotations

import importlib.util
import re
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

# The grammar the server seat is told to use. Adapters keep their own copy of this string.
CONTRACT_PATTERN = (
    r"^\s*SLATE:\s*(?:(done|review|later|answered)\s*(?::\s*(.+?)|\.?)"
    r"|ask\s+@?([A-Za-z][A-Za-z0-9_-]{1,31})\s*:\s*(.+?))\s*$"
)

# (outcome, who, body, question, options, status)
CONTRACT = [
    (
        "Done.\nSLATE: done: Sent the invoice. Nothing needed from you.",
        ("done", None, "Done.", None, None, "Sent the invoice. Nothing needed from you."),
    ),
    (
        "Draft.\nSLATE: review: Check the draft linked above.",
        ("review", None, "Draft.", None, None, "Check the draft linked above."),
    ),
    (
        "Wait.\nSLATE: later: Runs Friday at 9.",
        ("later", None, "Wait.", None, None, "Runs Friday at 9."),
    ),
    (
        "Ok.\nSLATE: answered: It is waiting on the bank, not on you.",
        ("answered", None, "Ok.", None, None, "It is waiting on the bank, not on you."),
    ),
    (
        "Ready.\nSLATE: ask alex: Start the import now? [Yes | No | Not yet]",
        ("ask", "alex", "Ready.", "Start the import now?", ["Yes", "No", "Not yet"], None),
    ),
    ("Here.\nSLATE: review", ("review", None, "Here.", None, None, None)),
    ("Here.\nSLATE: review.", ("review", None, "Here.", None, None, None)),
    ("Here.\nSLATE: done", ("done", None, "Here.", None, None, None)),
]

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
    *[text for text, _want in CONTRACT],
]


def test_adapters_use_the_contract_pattern():
    for adapter in ADAPTERS:
        assert adapter.SLATE_LINE.pattern == CONTRACT_PATTERN
        assert adapter.SLATE_LINE.flags & re.IGNORECASE


def test_example_copies_the_service_pattern_exactly():
    """The server's copy of the line. Red until that seat lands the same pattern."""
    adapter = ADAPTERS[1]
    assert adapter.SLATE_LINE.pattern == brief._LINE.pattern
    assert adapter.SLATE_LINE.flags == brief._LINE.flags


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("text,want", CONTRACT)
def test_contract_lines(adapter, text, want):
    assert adapter.closing(text) == want


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("text", CASES)
def test_every_adapter_reads_the_line_as_slate_does(adapter, text):
    mine = adapter.closing(text)
    theirs = brief.closing_line(text)
    got = (mine[0], mine[1], mine[3], tuple(mine[4] or ()), mine[5]) if mine else None
    server = (
        (
            theirs.outcome,
            theirs.waiting_on,
            theirs.question,
            theirs.options,
            getattr(theirs, "status", None),
        )
        if theirs
        else None
    )
    assert got == server
