"""Who is around right now: in memory, per process, never written to the database.

`seen(account_id, source)` is called on every authenticated REST and MCP request; `streaming()`
wraps each open SSE stream. `GET /api/v1/agents` reads both. Nothing here is a change to
anything a person or an agent owns, so none of it is an event; a restart forgets it, and the
roster falls back to each token's `last_used_at` (minute resolution) until accounts call again.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from slate.models import utcnow


@dataclass
class Seen:
    at: datetime
    source: str


class Presence:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, Seen] = {}
        self._streams: Counter[str] = Counter()

    def seen(self, account_id: str, source: str) -> None:
        self._seen[account_id] = Seen(utcnow(), source)  # one dict store; atomic under the GIL

    def last_seen(self, account_id: str) -> Seen | None:
        return self._seen.get(account_id)

    def streams(self, account_id: str) -> int:
        with self._lock:
            return self._streams[account_id]

    @contextmanager
    def streaming(self, account_id: str) -> Iterator[None]:
        with self._lock:
            self._streams[account_id] += 1
        self.seen(account_id, "stream")
        try:
            yield
        finally:
            with self._lock:
                self._streams[account_id] -= 1
                if self._streams[account_id] <= 0:
                    del self._streams[account_id]
            self.seen(account_id, "stream")

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
            self._streams.clear()


presence = Presence()
