"""Primary keys: 12-character base62 strings generated in Python.

Base62 (0-9A-Za-z) is URL-safe and has no `_`, so a token `slate_<id>_<secret>` splits cleanly.
The alphabet is in ASCII order, so string comparison matches numeric order.
"""

from __future__ import annotations

import re
import secrets
import threading
import time

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ID_LENGTH = 12


def new_id() -> str:
    """Random id (~71 bits). Used for every table except events."""
    return "".join(secrets.choice(ALPHABET) for _ in range(ID_LENGTH))


def _base62(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        n, r = divmod(n, 62)
        out.append(ALPHABET[r])
    return "".join(reversed(out))


_lock = threading.Lock()
_last_ms = 0


def new_event_id() -> str:
    """Time-ordered id: 7 chars of milliseconds + 5 random chars.

    Event ids sort in creation order within this process (one process by design), so the
    SSE `since` cursor and the polling `GET /events?since=` can use the id directly. Two ids
    in the same millisecond get consecutive logical milliseconds, never a tie.
    """
    global _last_ms
    with _lock:
        ms = max(int(time.time() * 1000), _last_ms + 1)
        _last_ms = ms
    return _base62(ms, 7) + "".join(secrets.choice(ALPHABET) for _ in range(5))


def slugify(name: str) -> str:
    """`Read / Watch` -> `read-watch`. Used for section and list slugs."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "untitled"
