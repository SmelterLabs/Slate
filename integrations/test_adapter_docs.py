"""Check the agent-facing instructions shipped with the optional adapters."""

from pathlib import Path

import pytest


@pytest.mark.parametrize("adapter", ["hermes", "runner-example"])
def test_adapter_readme_explains_questions_in_reply_prose(adapter):
    text = (Path(__file__).parent / adapter / "README.md").read_text(encoding="utf-8")
    assert (
        "A question written only in your reply is not shown to anyone as a question."
        in " ".join(text.split())
    )
