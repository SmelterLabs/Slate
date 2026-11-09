"""Webhook secrets are encrypted at rest with SLATE_SECRET_KEY (AES-GCM), never hashed."""

from __future__ import annotations

import base64

import pytest

from slate import crypto
from slate.settings import settings


def test_round_trip_and_ciphertext_does_not_contain_the_plaintext():
    enc = crypto.encrypt("whsec_abc123", aad="wh1")
    assert "whsec_abc123" not in enc
    assert enc.startswith("v1:")
    assert crypto.decrypt(enc, aad="wh1") == "whsec_abc123"


def test_each_encryption_uses_a_fresh_nonce():
    assert crypto.encrypt("same", aad="x") != crypto.encrypt("same", aad="x")


def test_ciphertext_is_bound_to_its_row():
    enc = crypto.encrypt("secret", aad="wh1")
    with pytest.raises(crypto.SecretKeyError):
        crypto.decrypt(enc, aad="wh2")


def test_wrong_key_cannot_decrypt(monkeypatch):
    enc = crypto.encrypt("secret", aad="wh1")
    monkeypatch.setattr(settings, "secret_key", "ab" * 32)
    with pytest.raises(crypto.SecretKeyError):
        crypto.decrypt(enc, aad="wh1")


@pytest.mark.parametrize("value", [None, "", "short", "ab" * 15, "not base64 or hex!!"])
def test_missing_or_weak_key_is_refused(monkeypatch, value):
    monkeypatch.setattr(settings, "secret_key", value)
    with pytest.raises(crypto.SecretKeyError):
        crypto.require_key()


def test_base64_key_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", base64.b64encode(b"k" * 32).decode())
    crypto.require_key()
    assert crypto.decrypt(crypto.encrypt("s", aad="a"), aad="a") == "s"
