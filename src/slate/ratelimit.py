"""Per-token rate limits: one token bucket for reads and one for writes, in memory.

A runaway agent loop should slow down, not fill the event log. Each token gets
`SLATE_RATE_LIMIT_READS_PER_MINUTE` / `SLATE_RATE_LIMIT_WRITES_PER_MINUTE`, refilled
continuously, with a burst of the same size (a full minute's worth at once is fine; a sustained
flood is not). `0` turns a bucket off. Over the limit, REST answers 429 with `Retry-After`
and MCP returns an error result saying how long to wait.

Buckets live in this process only (Slate is one process), so a restart forgives everyone.
`throttled` counts refusals per account for `GET /api/v1/agents`; `requests` counts every
request per token and kind (`read`/`write`) for `GET /api/v1/accounts/{id}/usage`. Both start
at zero when the process starts.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from slate.settings import settings


@dataclass
class _Bucket:
    tokens: float
    at: float


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    limit: int
    retry_after: int = 0  # whole seconds, at least 1 when refused
    remaining: int | None = None  # whole requests left in the bucket; None when unlimited


class Limiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self.throttled: dict[str, int] = defaultdict(int)
        self.requests: dict[tuple[str, str], int] = defaultdict(int)  # (token, kind) -> count

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self.throttled.clear()
            self.requests.clear()

    def counts(self, token_id: str) -> dict[str, int]:
        """Requests this token made since the process started, by kind."""
        return {k: self.requests.get((token_id, k), 0) for k in ("read", "write")}

    def take(self, token_id: str, account_id: str, kind: str, now: float | None = None) -> Verdict:
        """Spend one request of `kind` (`read` or `write`) for this token."""
        per_minute = (
            settings.rate_limit_writes_per_minute
            if kind == "write"
            else settings.rate_limit_reads_per_minute
        )
        if not settings.rate_limit_enabled or per_minute <= 0:
            with self._lock:
                self.requests[(token_id, kind)] += 1
            return Verdict(True, per_minute)
        now = time.monotonic() if now is None else now
        rate = per_minute / 60.0
        with self._lock:
            self.requests[(token_id, kind)] += 1
            b = self._buckets.get((token_id, kind))
            if b is None:
                b = self._buckets[(token_id, kind)] = _Bucket(float(per_minute), now)
            b.tokens = min(float(per_minute), b.tokens + (now - b.at) * rate)
            b.at = now
            if b.tokens >= 1:
                b.tokens -= 1
                return Verdict(True, per_minute, remaining=int(b.tokens))
            self.throttled[account_id] += 1
            return Verdict(False, per_minute, max(1, math.ceil((1 - b.tokens) / rate)), remaining=0)


limiter = Limiter()
