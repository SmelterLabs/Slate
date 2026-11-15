"""What an agent is doing right now on a task it holds: in memory, per process, while it runs.

A runner posts short lines while its agent works ("terminal: uv run pytest", "Reading
docs/board.md"); the task window shows them in a small box under "is typing…" so a person can
tell a long run from a stuck one. The lines are a view of a run in flight, not a change to the
task: nothing here writes an event row, a restart forgets them, and the card's history keeps
only what the agent reports (`item.reported`) and says (its comment). They reach open boards
as an id-less `item.activity` message on the live stream (`bus.publish_live`), which no
cursor, replay, webhook or check-in ever sees.

When the run ends, its finish keeps the lines with the answer it posted (`run_lines`, then
`clear`; `service/work.py` stores them as the comment's run log), so a person can open what the
agent did after the fact. A run that posts no comment, or a restart mid-run, keeps nothing.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from slate.models import utcnow
from slate.service.bus import bus

TYPE = "item.activity"
KEEP = 60  # lines the live box shows (it scrolls back this far)
LOG_KEEP = 400  # lines remembered per run, for the run log kept with its answer
MAX_LINES = 20  # per post
MAX_CHARS = 300  # per line
STALE = timedelta(hours=6)  # a run this quiet is over; its lines are dropped


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean(lines: list[str]) -> list[str]:
    """One display line each: no line breaks or control characters, trimmed, capped."""
    out = []
    for raw in lines:
        text = " ".join("".join(c if c.isprintable() else " " for c in str(raw)).split())
        if text:
            out.append(text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…")
    return out


class LiveActivity:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # item id -> (run id, account id, last post, lines)
        self._runs: dict[str, tuple[str, str, datetime, deque[dict[str, str]]]] = {}

    def add(
        self,
        item_id: str,
        account_id: str,
        run_id: str,
        lines: list[str],
        now: datetime | None = None,
    ) -> list[dict[str, str]]:
        """Remember the lines and send them to open boards. A new run starts a fresh box."""
        now = now or utcnow()
        added = [{"at": _stamp(now), "text": text} for text in clean(lines)]
        if not added:
            return []
        with self._lock:
            for key in [k for k, v in self._runs.items() if now - v[2] > STALE]:
                del self._runs[key]
            held = self._runs.get(item_id)
            kept = held[3] if held and held[0] == run_id else deque(maxlen=LOG_KEEP)
            kept.extend(added)
            self._runs[item_id] = (run_id, account_id, now, kept)
        bus.publish_live(
            {
                "type": TYPE,
                "item_id": item_id,
                "subject_id": item_id,
                "actor_id": account_id,
                "at": added[-1]["at"],
                "after": {"lines": added},
            }
        )
        return added

    def lines(self, item_id: str, account_id: str | None = None) -> dict[str, Any]:
        """The current run's lines, oldest first; none unless `account_id` (who works on the
        task now) is the one that posted them."""
        with self._lock:
            held = self._runs.get(item_id)
            if held is None or (account_id is not None and held[1] != account_id):
                return {"by": None, "lines": []}
            return {"by": held[1], "lines": list(held[3])[-KEEP:]}

    def run_lines(self, item_id: str, account_id: str) -> list[dict[str, str]]:
        """Every line remembered of the account's run on the task, oldest first."""
        with self._lock:
            held = self._runs.get(item_id)
            return list(held[3]) if held is not None and held[1] == account_id else []

    def clear(self, item_id: str) -> None:
        with self._lock:
            self._runs.pop(item_id, None)

    def reset(self) -> None:
        with self._lock:
            self._runs.clear()


live = LiveActivity()
