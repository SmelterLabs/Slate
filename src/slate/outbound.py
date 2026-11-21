"""Slate's outbound POST: automation `webhook` actions and Web Push.

    post(url, body, headers, *, timeout, transport=None) -> Reply(status, headers)
    sign(secret, body) / signed_headers(secret, body, now=None)

One function, so every sender gets the same limits:

- **Resolve once, connect to what was checked.** The host is looked up once
  (`check_target`). With private targets off (`SLATE_OUTBOUND_PRIVATE_TARGETS`,
  the default) every answer must be a public address, and a failed lookup is an error, never
  a pass. The connection then goes to exactly those addresses (`_Pinned`): the request's Host
  header and the TLS server name stay the URL's name, but a resolver that answers differently
  the second time (DNS rebinding) is never asked again.
- **One wall-clock deadline** (`timeout` seconds) covers connecting, TLS, the response headers
  and the body: every socket read and write is given only what is left of it (`_Deadline`),
  so a receiver that drips one byte at a time cannot hold a sender past it.
- **The answer is drained, never kept:** at most `RESPONSE_CAP_BYTES` of the body are read and
  thrown away; the caller gets the status and the headers. Echoing a receiver's body into a
  delivery log would let a caller read whatever its URL points at.
- No redirects, no proxies from the environment (the connection is made directly).

Tests pass an `httpx.MockTransport` as `transport`; the target check still runs, and nothing
connects.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from slate.service.errors import Invalid
from slate.settings import settings

RESPONSE_CAP_BYTES = 64 * 1024
_PRIVATE_SUFFIXES = (".ts.net", ".local", ".internal", ".lan", ".home.arpa")


def is_private_host(host: str) -> bool:
    """Whether a literal address or a host name is private, without resolving it."""
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    host = host.lower().rstrip(".")
    return host == "localhost" or "." not in host or host.endswith(_PRIVATE_SUFFIXES)


_PRIVATE_REFUSED = (
    "url points at a private address, which this Slate does not allow (set "
    "SLATE_OUTBOUND_PRIVATE_TARGETS=true for a receiver on this host or network)"
)


def validate_url(url: str) -> str:
    """https to any public host. Private targets (a private IP, localhost, a bare name, a
    tailnet or .local name) only with `SLATE_OUTBOUND_PRIVATE_TARGETS`, and then over plain
    http too. Judged by the name here; `check_target()` resolves it at send time."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Invalid("url must be an absolute http(s) URL")
    if len(url) > 2000:
        raise Invalid("url is longer than 2000 characters")
    private = is_private_host(parts.hostname)
    if private and not settings.outbound_private_targets:
        raise Invalid(_PRIVATE_REFUSED)
    if parts.scheme == "http" and not private:
        raise Invalid("plain http is allowed only to private addresses; use https")
    return url


class LookupFailed(OSError):
    """The target's name did not resolve: a network error, retried like one."""


def _lookup(host: str) -> list[str]:
    """Every address `host` resolves to, in the resolver's order (an IP literal is itself)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def check_target(url: str) -> list[str]:
    """Send-time check of a validated URL; returns the addresses to connect to.

    The name is resolved here, once, and the sender connects to exactly these addresses
    (`slate.outbound`), so a name that answers differently a second time cannot reach another
    host. With private targets off, the host must still be public *and* every address it
    resolves to must be public (a name can be pointed at 127.0.0.1 after it was saved):
    `Invalid` otherwise. A lookup that fails raises `LookupFailed` (fail closed)."""
    host = urlsplit(url).hostname or ""
    if not settings.outbound_private_targets and is_private_host(host):
        raise Invalid(_PRIVATE_REFUSED)
    try:
        addresses = _lookup(host)
    except (OSError, UnicodeError) as exc:  # UnicodeError: a name IDNA cannot encode
        raise LookupFailed(f"could not resolve {host}: {exc}") from None
    if not addresses:
        raise LookupFailed(f"could not resolve {host}")
    if not settings.outbound_private_targets:
        for address in addresses:
            # A link-local IPv6 answer carries %scope.
            if not ipaddress.ip_address(address.split("%")[0]).is_global:
                raise Invalid(
                    f"{host} resolves to a private address, which this Slate does not allow"
                )
    return addresses


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
    addresses = check_target(url)
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
