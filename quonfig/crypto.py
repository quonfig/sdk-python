"""DEPRECATED: helpers for the old ``ciphertext--iv--tag`` secret format.

The SDK no longer reads that format: secrets live in ``secrets/`` as qf1
values (qfg-8seuf.10), and a config value that still carries ``decryptWith``
fails with ``QuonfigDecryptionError``. Nothing in the SDK imports this module.
It stays importable, unchanged in behaviour, so existing callers keep working;
each function emits a ``DeprecationWarning``. Removal is planned for a future
major version.
"""

from __future__ import annotations

import base64
import warnings

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import QuonfigDecryptionError

SEPARATOR = "--"

_DEPRECATION = (
    "quonfig.crypto.{name} is deprecated: the old decryptWith secret format is no "
    "longer read by the SDK; use qf1 secrets (secrets/, QUONFIG_KEYSET*) instead"
)


def _warn(name: str) -> None:
    warnings.warn(_DEPRECATION.format(name=name), DeprecationWarning, stacklevel=3)


def _decode_key(encryption_key: str) -> bytes:
    """Decode an encryption key from hex or base64 encoding.

    The key is a 64-character hex string representing a 32-byte AES-256 key.
    Matching the Node SDK: Buffer.from(keyStringHex, 'hex').
    """
    # Try hex first (preferred — matches Node/Go SDKs)
    if len(encryption_key) == 64:
        try:
            return bytes.fromhex(encryption_key)
        except ValueError:
            pass
    # Fallback: try base64
    try:
        key_bytes = base64.b64decode(encryption_key)
        if len(key_bytes) in (16, 24, 32):
            return key_bytes
    except Exception:
        pass
    raise QuonfigDecryptionError(
        f"Invalid encryption key format. Expected a 64-character hex string."
        f" Got {len(encryption_key)} chars."
    )


def decrypt(encrypted_value: str, encryption_key: str) -> str:
    """
    DEPRECATED (old decryptWith format; see module docstring).

    Decrypt an AES-256-GCM encrypted value.

    The encrypted format matches the Node SDK:
        "<ciphertext_hex>--<iv_hex>--<auth_tag_hex>"

    The encryption_key is a hex-encoded 32-byte key (64 hex characters).
    """
    _warn("decrypt")
    try:
        parts = encrypted_value.split(SEPARATOR)
        if len(parts) != 3:
            raise QuonfigDecryptionError(
                f"Invalid encrypted value format: expected 3 parts separated by '{SEPARATOR}'"
            )
        ciphertext_hex, iv_hex, auth_tag_hex = parts
        ciphertext = bytes.fromhex(ciphertext_hex)
        iv = bytes.fromhex(iv_hex)
        auth_tag = bytes.fromhex(auth_tag_hex)

        key_bytes = _decode_key(encryption_key)
        aesgcm = AESGCM(key_bytes)

        # AESGCM expects ciphertext + auth_tag concatenated
        plaintext = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
        return plaintext.decode("utf-8")
    except QuonfigDecryptionError:
        raise
    except Exception as e:
        raise QuonfigDecryptionError(f"Decryption failed: {e}") from e


def encrypt(clear_text: str, encryption_key: str) -> str:
    """
    DEPRECATED (old decryptWith format; see module docstring).

    Encrypt a string using AES-256-GCM.

    Returns "<ciphertext_hex>--<iv_hex>--<auth_tag_hex>", matching the Node SDK format.
    The encryption_key is a hex-encoded 32-byte key (64 hex characters).
    """
    _warn("encrypt")
    import secrets

    try:
        key_bytes = _decode_key(encryption_key)
        aesgcm = AESGCM(key_bytes)
        iv = secrets.token_bytes(12)
        # AESGCM.encrypt returns ciphertext + auth_tag (last 16 bytes)
        enc_and_tag = aesgcm.encrypt(iv, clear_text.encode("utf-8"), None)
        ciphertext = enc_and_tag[:-16]
        tag = enc_and_tag[-16:]
        return SEPARATOR.join([ciphertext.hex(), iv.hex(), tag.hex()])
    except QuonfigDecryptionError:
        raise
    except Exception as e:
        raise QuonfigDecryptionError(f"Encryption failed: {e}") from e


def generate_new_hex_key() -> str:
    """DEPRECATED (old decryptWith format). Generate a new random 32-byte key,
    hex-encoded (64 characters)."""
    _warn("generate_new_hex_key")
    import secrets

    return secrets.token_hex(32)


# Legacy alias
def generate_new_b64_key() -> str:
    """DEPRECATED (old decryptWith format, and a legacy alias of
    generate_new_hex_key)."""
    _warn("generate_new_b64_key")
    import secrets

    return secrets.token_hex(32)
