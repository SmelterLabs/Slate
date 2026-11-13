"""The board hears every event type: EventSource only delivers a named SSE event to a listener
for that exact name, so a type missing from the board's `EVENT_TYPES` never reaches it live."""

from __future__ import annotations

import re
from pathlib import Path

from slate import docgen

STORE = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "store.svelte.ts"


def test_board_listens_for_every_event_type():
    source = STORE.read_text()
    block = source[source.index("export const EVENT_TYPES") :]
    block = block[: block.index("];")]
    listed = set(re.findall(r"'([a-z_]+\.[a-z_]+)'", block))
    missing = docgen.event_types_in_code() - listed
    assert not missing, f"add to EVENT_TYPES in web/src/lib/store.svelte.ts: {sorted(missing)}"


def test_board_listens_for_no_event_type_the_server_never_sends():
    source = STORE.read_text()
    block = source[source.index("export const EVENT_TYPES") :]
    block = block[: block.index("];")]
    listed = set(re.findall(r"'([a-z_]+\.[a-z_]+)'", block))
    extra = listed - docgen.event_types_in_code()
    assert not extra, f"remove from EVENT_TYPES in web/src/lib/store.svelte.ts: {sorted(extra)}"
