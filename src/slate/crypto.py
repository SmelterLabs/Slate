"""Encryption at rest for webhook secrets.

Webhook secrets cannot be hashed like tokens: Slate needs the plaintext to sign each delivery.
They are encrypted with AES-256-GCM under a key derived from `SLATE_SECRET_KEY` (32+ random
bytes, given as hex or base64). The row id is the associated data, so a ciphertext copied onto
another row does not decrypt. Stored form: `v1:<base64(nonce || ciphertext+tag)>`.

Losing or changing the key makes every stored secret unreadable: deliveries then fail with a
clear error, and each subscriber must be re-created. Generate a key with
`python -c "import secrets; print(secrets.token_hex(32))"`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from slate.settings import settings

MIN_KEY_BYTES = 32
_PREFIX = "v1:"


class SecretKeyError(RuntimeError):
    """SLATE_SECRET_KEY is missing or weak, or a stored secret does not decrypt with it."""


def _decode(value: str) -> bytes:
    value = value.strip()
    try:
        return bytes.fromhex(value)
    except ValueError:
        pass
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=False)
    except (binascii.Error, ValueError):
        return b""


def require_key() -> bytes:
    """The 32-byte AES key, or `SecretKeyError` if SLATE_SECRET_KEY is unset or too short."""
    raw = _decode(settings.secret_key or "")
    if len(raw) < MIN_KEY_BYTES:
        raise SecretKeyError(
            "SLATE_SECRET_KEY must be set to 32+ random bytes (hex or base64) to use webhooks"
        )
    # A fixed-length key from any 32+ byte input; the input is already high-entropy.
    return hashlib.sha256(b"slate-webhook-secrets\x00" + raw).digest()


def encrypt(plaintext: str, *, aad: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(require_key()).encrypt(nonce, plaintext.encode(), aad.encode())
    return _PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt(stored: str, *, aad: str) -> str:
    key = require_key()
    if not stored.startswith(_PREFIX):
        raise SecretKeyError("stored secret has an unknown format")
    try:
        blob = base64.b64decode(stored[len(_PREFIX) :], validate=True)
        if len(blob) < 12 + 16:
            raise ValueError("too short")
        return AESGCM(key).decrypt(blob[:12], blob[12:], aad.encode()).decode()
    except (InvalidTag, ValueError):  # binascii.Error is a ValueError
        raise SecretKeyError(
            "stored webhook secret does not decrypt; was SLATE_SECRET_KEY changed?"
        ) from None
