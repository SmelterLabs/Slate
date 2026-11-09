"""The lists of push reasons agree: what the server accepts in prefs (`accounts.PUSH_REASONS`),
what it sends by default (`push.DEFAULT_REASONS`), and what the board offers and defaults to
(`web/src/lib/push.ts`). A reason added to one and not the others is a save the board cannot
make: the panel sends its default list and the server answers 422."""

from __future__ import annotations

import re
from pathlib import Path

from slate.service import accounts, email, push

PUSH_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "push.ts"


def _board_lists() -> tuple[list[str], list[str]]:
    source = PUSH_TS.read_text()
    table = source[source.index("export const PUSH_REASONS") :]
    table = table[: table.index("];")]
    offered = re.findall(r"key: \"([a-z_]+)\"", table)
    defaults = source[source.index("export const DEFAULT_PUSH_REASONS") :]
    defaults = defaults[: defaults.index("];")]
    chosen = re.findall(r"\"([a-z_]+)\"", defaults)
    return offered, chosen


def test_server_defaults_are_all_accepted():
    assert set(push.DEFAULT_REASONS) <= set(accounts.PUSH_REASONS)


def test_board_offers_only_what_the_server_accepts():
    offered, _ = _board_lists()
    assert set(offered) <= set(accounts.PUSH_REASONS)
    assert set(push.DEFAULT_REASONS) <= set(offered)


def test_board_defaults_match_the_server_defaults():
    _, chosen = _board_lists()
    assert chosen == list(push.DEFAULT_REASONS)


def test_every_grid_row_is_a_board_reason_and_every_board_reason_has_a_row():
    """The Notifications grid (NOTIFY_GROUPS) covers exactly the board's push reasons, and the
    email defaults are reasons the server emails."""
    source = PUSH_TS.read_text()
    table = source[source.index("export const NOTIFY_GROUPS") :]
    table = table[: table.index("];")]
    grouped = re.findall(r"\"([a-z_]+)\"", "".join(re.findall(r"reasons: \[([^\]]*)\]", table)))
    offered, _ = _board_lists()
    assert sorted(grouped) == sorted(offered)
    defaults = source[source.index("export const DEFAULT_EMAIL_REASONS") :]
    defaults = defaults[: defaults.index("];")]
    assert re.findall(r"\"([a-z_]+)\"", defaults) == list(email.DEFAULT_REASONS)
    assert set(email.DEFAULT_REASONS) <= set(accounts.EMAIL_REASONS)
