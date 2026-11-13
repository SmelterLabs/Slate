"""Web Push (RFC 8030) with VAPID (RFC 8292) and aes128gcm payload encryption (RFC 8291).

No extra dependency: `cryptography` (already used for webhook secrets) does the ECDH, HKDF,
AES-GCM and ES256. The VAPID key pair is derived from `SLATE_SECRET_KEY`, so there is nothing
new to configure or back up, and every browser subscription survives a restart. Changing the
secret key changes the VAPID key: browsers must subscribe again (the board does it on its next
load, see web/src/lib/push.ts).

    public_key()            the application server key browsers subscribe with (base64url)
    encrypt(payload, p256dh, auth)   the request body for one subscription
    send(endpoint, p256dh, auth, payload)   POST it; returns (status_code, error)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from slate import crypto, outbound
from slate.service.errors import Invalid

# The VAPID contact when neither SLATE_PUSH_SUBJECT nor an https SLATE_PUBLIC_URL is set (see
# `subject()`). Apple refuses a token whose `sub` is not a real mailto: or https: URL.
SUBJECT = "https://slate.example.com"
TTL_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 10.0
RECORD_SIZE = 4096
# Tests swap in an `httpx.MockTransport`; None means real network.
transport: httpx.BaseTransport | None = None

_N = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)  # P-256 order


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


@lru_cache(maxsize=4)
def _private_for(key: bytes) -> ec.EllipticCurvePrivateKey:
    scalar = int.from_bytes(_hmac(key, b"slate-vapid\x00"), "big") % (_N - 1) + 1
    return ec.derive_private_key(scalar, ec.SECP256R1())


def private_key() -> ec.EllipticCurvePrivateKey:
    """Raises `crypto.SecretKeyError` when SLATE_SECRET_KEY is missing or weak."""
    return _private_for(crypto.require_key())


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def public_key() -> str:
    return b64url(_raw_public(private_key().public_key()))


def enabled() -> bool:
    try:
        crypto.require_key()
    except crypto.SecretKeyError:
        return False
    return True


def subject() -> str:
    """The VAPID `sub` claim: `SLATE_PUSH_SUBJECT` (a mailto: or https: URL), else the board's
    https `SLATE_PUBLIC_URL`, else the placeholder `SUBJECT`."""
    from slate.settings import settings

    if settings.push_subject.strip():
        return settings.push_subject.strip()
    if settings.public_url.strip().lower().startswith("https://"):
        return settings.public_url.strip().rstrip("/")
    return SUBJECT


def vapid_header(endpoint: str, *, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now or time.time()) + 12 * 3600),
        "sub": subject(),
    }
    head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{head}.{body}".encode()
    der = private_key().sign(signing, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    jwt = f"{head}.{body}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={public_key()}"


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None) -> bytes:
    """RFC 8291 aes128gcm body for one subscription (single record)."""
    ua_public = unb64url(p256dh)
    auth_secret = unb64url(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_private.public_key())
    shared = as_private.exchange(ec.ECDH(), ua_key)
    # IKM = HKDF(salt=auth_secret, ikm=ecdh, info="WebPush: info\0" || ua || as, L=32)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("push payload too large")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + ciphertext


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """The browser's side of `encrypt` (tests use it to read what was sent)."""
    salt, idlen = body[:16], body[20]
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    ua_public = _raw_public(ua_private.public_key())
    shared = ua_private.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    )
    ikm = _hmac(
        _hmac(unb64url(auth), shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01"
    )
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    return AESGCM(cek).decrypt(nonce, ciphertext, None).rstrip(b"\x00")[:-1]


def send(
    endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]
) -> tuple[int | None, str, str | None]:
    """POST one notification. Return status, error and optional Retry-After header.

    Through `slate.outbound.post`, like webhooks: the endpoint must still be a public address
    (unless `SLATE_WEBHOOK_PRIVATE_TARGETS`), the connection goes to the address that was
    checked, one deadline covers the whole exchange and the answer is drained up to a cap.
    A refused endpoint comes back as status 0, a final failure."""
    body = encrypt(json.dumps(payload, separators=(",", ":")).encode(), p256dh, auth)
    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS),
        "Urgency": "normal",
        "Authorization": vapid_header(endpoint),
    }
    try:
        r = outbound.post(endpoint, body, headers, timeout=TIMEOUT_SECONDS, transport=transport)
    except Invalid:
        return 0, "endpoint refused: private address", None
    except (httpx.HTTPError, OSError) as exc:  # OSError: the name did not resolve
        return None, type(exc).__name__, None
    return (
        r.status,
        "" if 200 <= r.status < 300 else f"HTTP {r.status}",
        r.headers.get("Retry-After"),
    )
