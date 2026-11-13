"""Outbound POSTs (webhooks, automation webhooks, Web Push): the address that was checked is
the address connected to, a failed lookup is refused, one deadline covers the whole exchange,
and deliveries carry a timestamped signature.

Receivers are local listeners on 127.0.0.1 or mock transports; name lookups and connections
to anything but 127.0.0.1 are faked, so nothing leaves the machine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time

import httpx
import pytest

from slate import automator, notify, webpush
from slate.service import accounts, items, lists, push, sections, webhooks
from slate.service.core import Actor
from slate.service.errors import Invalid
from slate.settings import settings

REAL_LOOKUP = getattr(webhooks, "_lookup", None)
REAL_GETADDRINFO = socket.getaddrinfo
REAL_CREATE_CONNECTION = socket.create_connection
PUBLIC = "93.184.215.14"


class Listener:
    """A TCP listener on 127.0.0.1 that counts connections; `drip` answers one header byte
    at a time, otherwise it answers 204 at once."""

    def __init__(self, *, drip: float | None = None) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.requests: list[bytes] = []
        self.drip = drip
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        self.sock.settimeout(0.1)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.connections += 1
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    data += conn.recv(4096)
                head, _, rest = data.partition(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":")[1])
                while len(rest) < length:  # read the body, or closing would reset the socket
                    rest += conn.recv(4096)
                self.requests.append(data)
                if self.drip is None:
                    conn.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                    return
                for byte in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 200:
                    if self.stop.is_set():
                        return
                    conn.sendall(bytes([byte]))
                    time.sleep(self.drip)
            except OSError:
                return

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


@pytest.fixture
def listener():
    made: list[Listener] = []

    def make(**kw) -> Listener:
        made.append(Listener(**kw))
        return made[-1]

    yield make
    for x in made:
        x.close()


class Net:
    """A fake resolver and a guard on outgoing connections: only 127.0.0.1 is reachable."""

    def __init__(self) -> None:
        self.answers: dict[str, list] = {}
        self.lookups: list[str] = []
        self.dialled: list[tuple[str, int]] = []

    def getaddrinfo(self, host, port, *args, **kwargs):
        try:
            socket.inet_pton(socket.AF_INET, host)
            return REAL_GETADDRINFO(host, port, *args, **kwargs)
        except (OSError, TypeError):
            pass
        self.lookups.append(host)
        queue = self.answers.get(host) or [PUBLIC]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, port or 0))]

    def create_connection(self, address, timeout=None, source_address=None, **kwargs):
        host, port = address
        resolved = self.getaddrinfo(host, port)[0][4][0]
        self.dialled.append((resolved, port))
        if resolved != "127.0.0.1":
            raise ConnectionRefusedError("tests reach 127.0.0.1 only")
        return REAL_CREATE_CONNECTION(("127.0.0.1", port), timeout, source_address)


@pytest.fixture
def net(monkeypatch) -> Net:
    fake = Net()
    if REAL_LOOKUP is not None:
        monkeypatch.setattr(webhooks, "_lookup", REAL_LOOKUP)
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", fake.create_connection)
    return fake


def _webhook_attempt(db, url):
    alex, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=alex.id, source="api")
    section = sections.create_section(db, actor, name="P", audience="personal")
    todo = lists.create_list(db, actor, section_id=section.id, name="Todo")
    _, secret = webhooks.create_webhook(db, actor, url=url, events=["*"])
    d = notify.Dispatcher()
    d.start()
    items.create_item(db, actor, list_id=todo.id, title="x")
    assert d.fan_out_once() >= 1
    [delivery_id] = d.claim(1)
    row = notify.attempt(delivery_id)
    row.secret = secret
    return row


# --- the address that was checked is the one connected to -----------------------------------


def test_a_name_that_rebinds_to_loopback_after_the_check_is_never_reached(db, net, listener):
    rx = listener()
    url = f"https://rebind.example.com:{rx.port}/hook"
    net.answers["rebind.example.com"] = [PUBLIC, "127.0.0.1"]
    row = _webhook_attempt(db, url)
    assert rx.connections == 0
    assert net.lookups.count("rebind.example.com") == 1
    assert net.dialled == [(PUBLIC, rx.port)]
    assert row.state == "retry"  # the checked public address refused: a network error
    net.answers["rebind.example.com"] = [PUBLIC, "127.0.0.1"]
    ok, _ = automator.send(url, {"automation": {"id": "a"}}, "s")
    assert ok is False and rx.connections == 0


def test_a_failed_lookup_is_refused_not_passed(db, net, listener):
    rx = listener()
    url = f"https://flaky.example.com:{rx.port}/hook"
    net.answers["flaky.example.com"] = [socket.gaierror("SERVFAIL"), "127.0.0.1"]
    row = _webhook_attempt(db, url)
    assert rx.connections == 0 and net.dialled == []
    assert row.state == "retry" and "resolve" in row.error
    net.answers["flaky.example.com"] = [socket.gaierror("SERVFAIL"), "127.0.0.1"]
    ok, detail = automator.send(url, {"automation": {"id": "a"}}, "s")
    assert ok is False and "resolve" in detail and rx.connections == 0


def test_the_checked_address_keeps_the_url_name_as_host(db, net, listener, monkeypatch):
    monkeypatch.setattr(settings, "webhook_private_targets", True)
    rx = listener()
    net.answers["rx.example.com"] = ["127.0.0.1", "10.9.9.9"]
    ok, detail = automator.send(
        f"http://rx.example.com:{rx.port}/hook", {"automation": {"id": "a"}}, "s"
    )
    assert ok is True, detail
    assert net.lookups == ["rx.example.com"]
    assert f"host: rx.example.com:{rx.port}" in rx.requests[0].decode().lower()


# --- one deadline for the whole exchange -----------------------------------------------------


def test_a_receiver_that_drips_its_headers_is_cut_off_at_the_deadline(
    db, net, listener, monkeypatch
):
    monkeypatch.setattr(settings, "webhook_private_targets", True)
    monkeypatch.setattr(settings, "webhook_timeout_seconds", 1.0)
    monkeypatch.setattr(automator, "TIMEOUT_SECONDS", 1.0)
    rx = listener(drip=0.2)
    url = f"http://127.0.0.1:{rx.port}/hook"
    started = time.monotonic()
    ok, detail = automator.send(url, {"automation": {"id": "a"}}, "s")
    assert ok is False and "Timeout" in detail
    assert time.monotonic() - started < 3
    started = time.monotonic()
    row = _webhook_attempt(db, url)
    assert time.monotonic() - started < 3
    assert row.state == "retry" and "Timeout" in row.error


# --- signatures carry a timestamp ------------------------------------------------------------


def _check_signatures(request: httpx.Request, secret: str) -> None:
    body = request.read()
    stamp = request.headers["X-Slate-Timestamp"]
    assert abs(int(stamp) - time.time()) < 60
    v2 = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert request.headers["X-Slate-Signature-V2"] == f"sha256={v2}"
    v1 = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert request.headers["X-Slate-Signature"] == f"sha256={v1}"  # still sent


def test_webhook_and_automation_deliveries_sign_a_timestamp(db, monkeypatch):
    seen: list[httpx.Request] = []

    def rx(request):
        seen.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(notify, "transport", httpx.MockTransport(rx))
    monkeypatch.setattr(automator, "transport", httpx.MockTransport(rx))
    row = _webhook_attempt(db, "https://rx.example.com/hook")
    assert row.state == "ok"
    _check_signatures(seen[0], row.secret)
    ok, _ = automator.send("https://rx.example.com/a", {"automation": {"id": "a"}}, "whsec_x")
    assert ok
    _check_signatures(seen[1], "whsec_x")
    assert json.loads(seen[1].read()) == {"automation": {"id": "a"}}


# --- Web Push: private endpoints and bounded answers -----------------------------------------


def _browser_keys() -> dict[str, str]:
    from cryptography.hazmat.primitives.asymmetric import ec

    browser = ec.generate_private_key(ec.SECP256R1())
    return {
        "p256dh": webpush.b64url(webpush._raw_public(browser.public_key())),
        "auth": webpush.b64url(b"0123456789abcdef"),
    }


def test_a_wildcard_push_allowlist_still_refuses_private_endpoints(monkeypatch, db, net):
    monkeypatch.setattr(settings, "push_endpoint_hosts", ["*"])
    alex, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=alex.id, source="api")
    keys = _browser_keys()
    with pytest.raises(Invalid, match="private address"):
        push.subscribe(db, actor, endpoint="https://10.0.0.5/push/x", **keys)
    net.answers["inside.example.com"] = ["10.0.0.5"]
    with pytest.raises(Invalid, match="private address"):
        push.subscribe(db, actor, endpoint="https://inside.example.com/push/x", **keys)
    sent: list = []
    monkeypatch.setattr(webpush, "transport", httpx.MockTransport(sent.append))
    status, error, _ = webpush.send(
        "https://inside.example.com/push/x", keys["p256dh"], keys["auth"], {"title": "x"}
    )
    assert sent == [] and status == 0 and "private" in error


def test_a_push_answer_is_read_up_to_a_cap(monkeypatch):
    from slate import outbound

    keys = _browser_keys()
    pulled = {"chunks": 0}

    def endless():
        while True:
            pulled["chunks"] += 1
            yield b"x" * 4096

    monkeypatch.setattr(
        webpush,
        "transport",
        httpx.MockTransport(lambda r: httpx.Response(201, content=endless())),
    )
    status, error, _ = webpush.send("https://push.example.com/s/1", **keys, payload={"t": 1})
    assert status == 201 and error == ""
    assert pulled["chunks"] <= outbound.RESPONSE_CAP_BYTES // 4096 + 1
