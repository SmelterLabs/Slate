"""Each state machine names its states once (`docs/state-machines.md`). These tests keep the
copies honest: what the API accepts or returns, what the database will store, and what the
doc lists are the same words as the declaration in code."""

import ast
import inspect
from pathlib import Path
from typing import get_args

import pytest

from slate.api import agents as agents_api
from slate.api import chat as chat_api
from slate.api import inbox as inbox_api
from slate.api import schemas
from slate.api import work as work_api
from slate.models import (
    AgentState,
    EmailDelivery,
    PushDelivery,
    SendState,
)
from slate.service import inbox, reports, turns, work

DOC = Path(__file__).resolve().parents[1] / "docs" / "state-machines.md"


def _literal(model, field: str) -> set[str]:
    return set(get_args(model.model_fields[field].annotation))


# (declared in code, every API field that repeats it)
VOCABULARIES = [
    (set(reports.STATES), [(schemas.ReportIn, "state")]),
    (
        {s.value for s in AgentState},
        [(schemas.AgentFlag, "state"), (agents_api.WorkOut, "state")],
    ),
    (set(work.OUTCOMES), [(work_api.FinishIn, "outcome")]),
    (
        set(turns.OUTCOMES),
        [(work_api.ChatFinishIn, "outcome"), (work_api.DocFinishIn, "outcome")],
    ),
    (
        set(work.LEASE_STATES),
        [(work_api.LeaseOut, "state"), (agents_api.PlanLeaseOut, "state")],
    ),
    (set(turns.STATES), [(chat_api.AgentTurnOut, "state")]),
    ({s.value for s in SendState}, []),
]


def test_the_api_repeats_each_vocabulary_exactly():
    for declared, fields in VOCABULARIES:
        for model, field in fields:
            assert _literal(model, field) == declared, f"{model.__name__}.{field}"


def test_the_inbox_serves_every_reason_it_can_give():
    """A reason the service returns and the route's model does not know is a 500 for whoever
    has that entry in their inbox."""
    assert set(get_args(inbox_api.Reason)) == set(inbox.REASONS)
    tree = ast.parse(inspect.getsource(inbox.reason_for))
    given = set()
    for ret in ast.walk(tree):
        if not isinstance(ret, ast.Return):
            continue
        # `return "x"` or `return "x" if ... else None`: the condition's strings are not reasons.
        value = ret.value.body if isinstance(ret.value, ast.IfExp) else ret.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            given.add(value.value)
    assert given and given <= set(inbox.REASONS), given - set(inbox.REASONS)


def test_the_badge_states_are_the_reports_that_set_a_flag():
    assert {s.value for s in AgentState} == set(reports.STATES) - {"done", "idle"}


@pytest.mark.parametrize(
    ("model", "states"),
    [(PushDelivery, SendState), (EmailDelivery, SendState)],
)
def test_a_queue_row_refuses_a_state_outside_its_vocabulary(model, states):
    row = model()
    for s in states:
        row.state = s.value
    with pytest.raises(ValueError, match=states.__name__):
        row.state = "delivered"


def test_the_doc_lists_every_state():
    text = DOC.read_text(encoding="utf-8")
    for declared, _ in VOCABULARIES:
        for word in declared:
            assert f"`{word}`" in text, f"docs/state-machines.md does not name `{word}`"


def test_an_automation_can_trigger_on_every_report_state():
    from slate.service import automations

    assert set(automations.AGENT_STATES) == set(reports.STATES)
