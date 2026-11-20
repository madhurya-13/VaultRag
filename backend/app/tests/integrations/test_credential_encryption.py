"""Stored provider credentials must be unreadable without the deployment key."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integrations import crypto
from app.integrations.exceptions import CredentialEncryptionError


def _key() -> bytes:
    return crypto.decode_key(crypto.generate_key())


def test_generated_key_is_a_32_byte_base64url_value():
    raw = crypto.generate_key()
    decoded = crypto.decode_key(raw)
    assert len(decoded) == 32
    assert crypto.decode_key(raw) == decoded


def test_round_trip_with_tenant_bound_associated_data():
    key = _key()
    aad = crypto.build_aad(tenant_id="t1", integration_id="i1", provider="google_drive")
    secret = crypto.encrypt_secret('{"refresh_token": "rt-123"}', aad=aad, key=key)

    assert b"rt-123" not in secret.ciphertext
    assert len(secret.nonce) == 12
    assert secret.key_version == crypto.CURRENT_KEY_VERSION
    assert (
        crypto.decrypt_secret(secret, aad=aad, key=key)
        == '{"refresh_token": "rt-123"}'
    )


def test_ciphertext_cannot_be_moved_to_another_tenant():
    key = _key()
    aad = crypto.build_aad(tenant_id="t1", integration_id="i1", provider="google_drive")
    other = crypto.build_aad(
        tenant_id="t2", integration_id="i1", provider="google_drive"
    )
    secret = crypto.encrypt_secret("refresh", aad=aad, key=key)

    with pytest.raises(CredentialEncryptionError):
        crypto.decrypt_secret(secret, aad=other, key=key)
    with pytest.raises(CredentialEncryptionError):
        crypto.decrypt_secret(secret, aad=None, key=key)


def test_tampered_ciphertext_and_wrong_key_are_rejected():
    key = _key()
    secret = crypto.encrypt_secret("refresh", key=key)
    tampered = crypto.EncryptedSecret(
        ciphertext=bytes([secret.ciphertext[0] ^ 0xFF]) + secret.ciphertext[1:],
        nonce=secret.nonce,
        key_version=secret.key_version,
    )

    with pytest.raises(CredentialEncryptionError):
        crypto.decrypt_secret(tampered, key=key)
    with pytest.raises(CredentialEncryptionError):
        crypto.decrypt_secret(secret, key=_key())


def test_each_encryption_uses_a_fresh_nonce():
    key = _key()
    first = crypto.encrypt_secret("same", key=key)
    second = crypto.encrypt_secret("same", key=key)
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


def test_unsupported_key_version_is_refused():
    key = _key()
    secret = crypto.encrypt_secret("refresh", key=key)
    future = crypto.EncryptedSecret(
        ciphertext=secret.ciphertext,
        nonce=secret.nonce,
        key_version=secret.key_version + 1,
    )
    with pytest.raises(CredentialEncryptionError):
        crypto.decrypt_secret(future, key=key)


def test_missing_deployment_key_fails_closed(monkeypatch):
    monkeypatch.setattr(
        crypto,
        "get_settings",
        lambda: SimpleNamespace(integration_token_encryption_key=None),
    )
    with pytest.raises(CredentialEncryptionError):
        crypto.get_encryption_key()


def test_malformed_keys_are_rejected():
    for value in ("", "not base64!!", "c2hvcnQ="):
        with pytest.raises(CredentialEncryptionError):
            crypto.decode_key(value)
