"""Slate's outbound POST: webhook deliveries, automation `webhook` actions and Web Push.

    post(url, body, headers, *, timeout, transport=None) -> Reply(status, headers)
    sign(secret, body) / signed_headers(secret, body, now=None)

One function, so every sender gets the same limits:

- **Resolve once, connect to what was checked.** The host is looked up once
  (`service.webhooks.check_target`). With private targets off (`SLATE_WEBHOOK_PRIVATE_TARGETS`,
  the default) every answer must be a public address, and a failed lookup is an error, never
  a pass. The connection then goes to exactly those addresses (`_Pinned`): the request's Host
  header and the TLS server name stay the URL's name, but a resolver that answers differently
  the second time (DNS rebinding) is never asked again.
- **One wall-clock deadline** (`timeout` seconds) covers connecting, TLS, the response headers
  and the body: every socket read and write is given only what is left of it (`_Deadline`),
  so a receiver that drips one byte at a time cannot hold a sender past it.
- **The answer is drained, never kept:** at most `RESPONSE_CAP_BYTES` of the body are read and
  thrown away; the caller gets the status and the headers. Echoing a receiver's body into a
  delivery log would let a subscriber read whatever its URL points at.
- No redirects, no proxies from the environment (the connection is made directly).

Tests pass an `httpx.MockTransport` as `transport`; the target check still runs, and nothing
connects.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx

from slate.service import webhooks

RESPONSE_CAP_BYTES = 64 * 1024


@dataclass(frozen=True)
class Reply:
    status: int
    headers: httpx.Headers


def sign(secret: str, body: bytes) -> str:
    """`sha256=<hex HMAC-SHA256 of the raw body>`: the original body-only signature."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signed_headers(secret: str, body: bytes, now: float | None = None) -> dict[str, str]:
    """The signature headers. `X-Slate-Signature-V2` signs `<timestamp>.<raw body>` with the
    `X-Slate-Timestamp` (Unix seconds) it is sent with, so a receiver that rejects old
    timestamps cannot be fed a captured delivery again; `X-Slate-Signature` (body only) is
    still sent for receivers that check it."""
    stamp = str(int(time.time() if now is None else now))
    v2 = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {
        "X-Slate-Timestamp": stamp,
        "X-Slate-Signature-V2": f"sha256={v2}",
        "X-Slate-Signature": sign(secret, body),
    }


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise httpcore.ReadTimeout("the request ran past its deadline")
    return left


def _bounded(timeout: float | None, deadline: float) -> float:
    left = _left(deadline)
    return left if timeout is None else min(timeout, left)


class _Deadline(httpcore.NetworkStream):
    """A stream whose every operation gets at most what is left of one deadline."""

    def __init__(self, inner: httpcore.NetworkStream, deadline: float) -> None:
        self._inner = inner
        self._deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, _bounded(timeout, self._deadline))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, _bounded(timeout, self._deadline))

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        inner = self._inner.start_tls(
            ssl_context, server_hostname, _bounded(timeout, self._deadline)
        )
        return _Deadline(inner, self._deadline)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _Pinned(httpcore.SyncBackend):
    """Connects to the addresses already checked, never resolving the name again."""

    def __init__(self, addresses: list[str], deadline: float) -> None:
        self._addresses = addresses
        self._deadline = deadline

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        error: Exception = httpcore.ConnectError(f"no address for {host}")
        for address in self._addresses:
            try:
                stream = super().connect_tcp(
                    address,
                    port,
                    timeout=_bounded(timeout, self._deadline),
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
                continue
            return _Deadline(stream, self._deadline)
        raise error


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self, addresses: list[str], deadline: float) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=1,
            network_backend=_Pinned(addresses, deadline),
        )


def post(
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> Reply:
    """POST once. Raises `Invalid` when the target is refused (a private address: final),
    `httpx.HTTPError` for network errors and an overrun deadline (worth a retry)."""
    deadline = time.monotonic() + timeout
    addresses = webhooks.check_target(url)
    if transport is None:
        transport = _PinnedTransport(addresses, deadline)
    with (
        httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
        ) as client,
        client.stream("POST", url, content=body, headers=headers) as response,
    ):
        got = 0
        for chunk in response.iter_bytes():
            got += len(chunk)
            if got >= RESPONSE_CAP_BYTES or time.monotonic() > deadline:
                break
        return Reply(response.status_code, response.headers)
