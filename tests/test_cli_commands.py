"""The `slate` command line offers exactly its supported commands."""

from __future__ import annotations

import importlib.util

import pytest

from slate import cli


def test_the_cli_lists_only_the_supported_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for name in ("serve", "migrate", "docs", "check-targets"):
        assert name in out
    assert "import" not in out and "backfill-doc-text" not in out


@pytest.mark.parametrize("argv", [["import", "clickup", "--team", "1"], ["backfill-doc-text"]])
def test_removed_commands_are_refused(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_the_server_package_has_no_importers():
    assert importlib.util.find_spec("slate.importers") is None
