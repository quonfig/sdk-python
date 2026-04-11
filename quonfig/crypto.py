from __future__ import annotations

import base64

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import QuonfigDecryptionError

SEPARATOR = "--"


def decrypt(encrypted_value: str, encryption_key: str) -> str:
    """
    Decrypt an AES-256-GCM encrypted value.

    The encrypted format is "<nonce_hex>--<tag_hex>--<ciphertext_hex>"
    (3 parts separated by "--"). The encryption_key is a base64-encoded secret.
    """
    try:
        parts = encrypted_value.split(SEPARATOR)
        if len(parts) != 3:
            raise QuonfigDecryptionError(
                f"Invalid encrypted value format: expected 3 parts separated by '{SEPARATOR}'"
            )
        nonce_hex, tag_hex, ciphertext_hex = parts
        nonce = bytes.fromhex(nonce_hex)
        tag = bytes.fromhex(tag_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)

        # Decode the base64 key
        key_bytes = base64.b64decode(encryption_key)
        aesgcm = AESGCM(key_bytes)

        # AES-GCM: ciphertext + tag appended
        plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
        return plaintext.decode("utf-8")
    except QuonfigDecryptionError:
        raise
    except Exception as e:
        raise QuonfigDecryptionError(f"Decryption failed: {e}") from e


def encrypt(clear_text: str, encryption_key: str) -> str:
    """
    Encrypt a string using AES-256-GCM.

    Returns "<nonce_hex>--<tag_hex>--<ciphertext_hex>".
    The encryption_key should be a base64-encoded 32-byte key.
    """
    import secrets

    try:
        key_bytes = base64.b64decode(encryption_key)
        aesgcm = AESGCM(key_bytes)
        nonce = secrets.token_bytes(12)
        # AESGCM.encrypt returns ciphertext + tag (last 16 bytes)
        enc_and_tag = aesgcm.encrypt(nonce, clear_text.encode("utf-8"), None)
        ciphertext = enc_and_tag[:-16]
        tag = enc_and_tag[-16:]
        return SEPARATOR.join([nonce.hex(), tag.hex(), ciphertext.hex()])
    except Exception as e:
        raise QuonfigDecryptionError(f"Encryption failed: {e}") from e


def generate_new_b64_key() -> str:
    """Generate a new random 32-byte key, base64-encoded."""
    import secrets

    return base64.b64encode(secrets.token_bytes(32)).decode()
