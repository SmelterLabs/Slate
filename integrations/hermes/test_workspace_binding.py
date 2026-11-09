"""The chosen workspace must reach real sessions, not only the setup probe."""

from test_hermes_adapter import Gateway
from test_hermes_adapter import go as run_card
from test_hermes_chat_adapter import gateway
from test_hermes_chat_adapter import go as run_chat


def test_card_session_uses_selected_workspace(tmp_path):
    gw = Gateway(
        events=[
            ("assistant.completed", {"content": "Finished.\nSLATE: done"}),
            ("run.completed", {}),
        ]
    )
    out, _, _ = run_card(
        gw, env={"HERMES_CWD": str(tmp_path), "HERMES_STATE": str(tmp_path / "state.json")}
    )
    assert out["outcome"] == "done"
    assert gw.created[0]["cwd"] == str(tmp_path)
    assert "model" not in gw.created[0] and "provider" not in gw.created[0]


def test_chat_session_uses_selected_workspace(tmp_path):
    gw = gateway()
    out, _, _, _ = run_chat(
        {"HERMES_CWD": str(tmp_path), "HERMES_STATE": str(tmp_path / "state.json")}, gw=gw
    )
    assert out["outcome"] == "done"
    assert gw.created[0]["cwd"] == str(tmp_path)
