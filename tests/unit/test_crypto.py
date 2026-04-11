"""Tests for AES-256-GCM encryption/decryption round-trip."""
from __future__ import annotations

import pytest

from quonfig.crypto import decrypt, encrypt, generate_new_b64_key
from quonfig.exceptions import QuonfigDecryptionError


class TestEncryptionRoundTrip:
    def test_basic_round_trip(self):
        key = generate_new_b64_key()
        clear_text = "hello world"
        encrypted = encrypt(clear_text, key)
        assert decrypt(encrypted, key) == clear_text

    def test_empty_string_round_trip(self):
        key = generate_new_b64_key()
        encrypted = encrypt("", key)
        assert decrypt(encrypted, key) == ""

    def test_unicode_round_trip(self):
        key = generate_new_b64_key()
        clear_text = "unicode: \u00e9\u00e0\u00fc\u6c49\u5b57"
        encrypted = encrypt(clear_text, key)
        assert decrypt(encrypted, key) == clear_text

    def test_long_string_round_trip(self):
        key = generate_new_b64_key()
        clear_text = "x" * 10000
        encrypted = encrypt(clear_text, key)
        assert decrypt(encrypted, key) == clear_text

    def test_different_encryptions_produce_different_ciphertext(self):
        """Each encryption call uses a random nonce, so output differs."""
        key = generate_new_b64_key()
        enc1 = encrypt("hello", key)
        enc2 = encrypt("hello", key)
        assert enc1 != enc2

    def test_wrong_key_raises_decryption_error(self):
        key1 = generate_new_b64_key()
        key2 = generate_new_b64_key()
        encrypted = encrypt("secret", key1)
        with pytest.raises(QuonfigDecryptionError):
            decrypt(encrypted, key2)

    def test_tampered_ciphertext_raises(self):
        key = generate_new_b64_key()
        encrypted = encrypt("secret", key)
        # Tamper with the ciphertext: flip the last char
        parts = encrypted.split("--")
        parts[2] = parts[2][:-1] + ("0" if parts[2][-1] != "0" else "1")
        tampered = "--".join(parts)
        with pytest.raises(QuonfigDecryptionError):
            decrypt(tampered, key)

    def test_invalid_format_raises(self):
        key = generate_new_b64_key()
        with pytest.raises(QuonfigDecryptionError):
            decrypt("not--a--valid--encrypted--value", key)

    def test_generate_new_key_produces_valid_hex(self):
        key = generate_new_b64_key()  # now returns hex (legacy alias)
        # Should be a 64-character hex string representing 32 bytes
        assert len(key) == 64
        # Should be valid hex
        decoded = bytes.fromhex(key)
        assert len(decoded) == 32

    def test_multiple_round_trips_same_key(self):
        key = generate_new_b64_key()
        for i in range(5):
            text = f"message number {i}"
            assert decrypt(encrypt(text, key), key) == text

    def test_encrypted_format_has_three_parts(self):
        key = generate_new_b64_key()
        encrypted = encrypt("test", key)
        parts = encrypted.split("--")
        assert len(parts) == 3
        # Each part should be valid hex
        for part in parts:
            bytes.fromhex(part)  # Should not raise
