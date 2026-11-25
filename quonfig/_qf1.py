"""qf1 secrets, read side (spec ``project/plans/secrets-qf1-spec.md``).

::

    qf1:<KEYSET>:<FINGERPRINT>:<base64url-no-pad(E || C || T)>

X25519 (RFC 7748) and AES-256-GCM come from ``cryptography``. HKDF-SHA256
(RFC 5869) is written out from HMAC so no library HKDF quirk can matter
(spec 5.3). The private key for a value is found by (keyset name,
fingerprint): the keyset name is the env var that holds the keys, and the
fingerprint picks one of them. Keys are never tried one after another.

Every failure raises :class:`Qf1Error`, a :class:`QuonfigDecryptionError`
whose message starts ``qf1 <class>: `` (spec 9). Messages name keysets,
fingerprints and entry positions only, never key material, env values,
plaintext or the payload. A failure is always raised OUTSIDE the ``except``
block that saw the library error, so nothing secret rides along on
``__cause__`` / ``__context__`` (a ``UnicodeDecodeError``, for one, carries
the plaintext bytes).

Internal module: not part of the public API.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import QuonfigDecryptionError

# Spec 5.1.
HKDF_INFO = b"quonfig.com/qf1/X25519"
OKM_LENGTH = 44
TAG_LENGTH = 16
MIN_PAYLOAD = 32 + TAG_LENGTH
PRIVATE_KEY_PREFIX = "qf1-priv-"
_ENTRY_BODY_LEN = 43
_BASE_POINT = bytes([9]) + bytes(31)

# Full-string patterns (spec 0): always used with ``fullmatch``.
KEYSET_NAME_RE = re.compile(r"QUONFIG_KEYSET(?:_[A-Z0-9]+)*")
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{8}")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
# Step 1 of spec 3.5 is a prefix test, so ``match`` is right there.
_OTHER_VERSION_RE = re.compile(r"qf[1-9][0-9]*:")
# Spec 6.2 step 2: ONLY these four; never the platform's general strip().
_ENV_STRIP = " \t\r\n"


class Qf1Error(QuonfigDecryptionError):
    """A qf1 value could not be read. ``code`` is the spec 9.1 class."""

    code: str

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"qf1 {code}: {detail}", code=code)
        self.detail = detail


# --------------------------------------------------------------------------
# HKDF-SHA256 from HMAC (RFC 5869; spec 5.3)
# --------------------------------------------------------------------------


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """RFC 5869 2.2. An empty salt is 32 zero bytes."""
    return _hmac_sha256(salt if salt else bytes(32), ikm)


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 2.3: T(i) = HMAC(PRK, T(i-1) || info || i)."""
    if length < 0 or length > 255 * 32:
        raise ValueError("HKDF length out of range")
    okm = b""
    t = b""
    i = 1
    while len(okm) < length:
        t = _hmac_sha256(prk, t + info + bytes([i]))
        okm += t
        i += 1
    return okm[:length]


@dataclass(frozen=True)
class Derived:
    salt: bytes
    prk: bytes
    okm: bytes
    key: bytes
    nonce: bytes


def derive(shared: bytes, ephemeral: bytes, recipient_public: bytes) -> Derived:
    """salt = E || pub_r (E as transmitted); K = OKM[0:32]; N = OKM[32:44]."""
    salt = ephemeral + recipient_public
    prk = hkdf_extract(salt, shared)
    okm = hkdf_expand(prk, HKDF_INFO, OKM_LENGTH)
    return Derived(salt=salt, prk=prk, okm=okm, key=okm[:32], nonce=okm[32:44])


def aad(keyset: str, fingerprint_hex: str) -> bytes:
    """Spec 5.4: ASCII ``qf1:<KEYSET>:<FINGERPRINT>:``."""
    return f"qf1:{keyset}:{fingerprint_hex}:".encode("ascii")


# --------------------------------------------------------------------------
# X25519 (RFC 7748; spec 5.2)
# --------------------------------------------------------------------------


def x25519(scalar: bytes, u: bytes) -> bytes:
    """X25519 with RFC 7748 clamping, top-bit masking and mod-p reduction
    (OpenSSL does all three). ``cryptography`` refuses a small-order input /
    all-zero result with ``ValueError``; ONLY that refusal becomes 32 zero
    bytes, so the caller's all-zero check covers both behaviours. Any other
    error propagates as the bug it is."""
    if len(scalar) != 32 or len(u) != 32:
        raise ValueError("x25519 needs 32-byte inputs")
    unsupported = False
    try:
        private = X25519PrivateKey.from_private_bytes(scalar)
        public = X25519PublicKey.from_public_bytes(u)
    except UnsupportedAlgorithm:
        unsupported = True
    if unsupported:
        # A FIPS-restricted OpenSSL (or similar) refuses X25519. That is the
        # runtime, not the key entry: fail each value with a clear reason.
        raise Qf1Error(
            "unsupported_runtime",
            "this runtime's cryptography/OpenSSL build does not allow X25519 "
            "(for example a FIPS-restricted OpenSSL), so qf1 secrets cannot be read here",
        )
    try:
        return private.exchange(public)
    except ValueError:
        return bytes(32)


def public_key(private: bytes) -> bytes:
    """X25519(priv, 9)."""
    return x25519(private, _BASE_POINT)


def fingerprint(public: bytes) -> str:
    """Spec 4: lowercase hex of SHA-256(pub)[0:4]."""
    return hashlib.sha256(public).digest()[:4].hex()


# --------------------------------------------------------------------------
# base64url, strict (spec 3.4)
# --------------------------------------------------------------------------


def _b64url_decode_strict(text: str) -> Optional[bytes]:
    """Canonical unpadded base64url, or ``None``. ``urlsafe_b64decode`` alone
    silently drops characters outside the alphabet, so the alphabet, length
    and re-encode checks are all needed."""
    if not text or not _BASE64URL_RE.fullmatch(text) or len(text) % 4 == 1:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != text:
        return None  # non-zero trailing bits
    return raw


# --------------------------------------------------------------------------
# Key entries and the keyset env var (spec 6)
# --------------------------------------------------------------------------


def parse_private_key_entry(entry: str) -> bytes:
    """``qf1-priv-`` + 43 canonical base64url characters (32 bytes, any value:
    keys are stored unclamped)."""
    raw: Optional[bytes] = None
    if isinstance(entry, str) and entry.startswith(PRIVATE_KEY_PREFIX):
        body = entry[len(PRIVATE_KEY_PREFIX) :]
        if len(body) == _ENTRY_BODY_LEN:
            raw = _b64url_decode_strict(body)
    if raw is None or len(raw) != 32:
        raise Qf1Error(
            "invalid_key_entry",
            f'private key entry is not "{PRIVATE_KEY_PREFIX}" followed by '
            f"{_ENTRY_BODY_LEN} canonical base64url characters",
        )
    return raw


@dataclass(frozen=True)
class HeldKey:
    private: bytes = field(repr=False)  # never in a repr, log or assertion diff
    public: bytes = b""


def parse_keyset_env(keyset: str, value: Optional[str]) -> Dict[str, HeldKey]:
    """Spec 6.2: the env var's value -> {fingerprint: key}."""
    if value is None:
        raise Qf1Error("keyset_missing", f"this runtime has no {_name(keyset)}")
    pieces = [p.strip(_ENV_STRIP) for p in value.split(",")]
    entries = [p for p in pieces if p]
    if not entries:
        raise Qf1Error("keyset_missing", f"{_name(keyset)} is set but holds no keys")
    held: Dict[str, HeldKey] = {}
    for position, entry in enumerate(entries, start=1):
        bad = False
        try:
            private = parse_private_key_entry(entry)
        except Qf1Error:
            bad = True
        if bad:
            # Raised outside the except: nothing chained.
            raise Qf1Error(
                "keyset_invalid", f"{_name(keyset)} entry {position} is not a qf1 private key"
            )
        public = public_key(private)
        fp = fingerprint(public)
        existing = held.get(fp)
        if existing is not None:
            if existing.public == public:
                continue  # one key written twice, or clamp-equivalent bytes
            raise Qf1Error(
                "keyset_invalid", f"{_name(keyset)} holds two different keys with fingerprint {fp}"
            )
        held[fp] = HeldKey(private=private, public=public)
    return held


# Decision 33 allows caching a parsed keyset for the life of the process. The
# cache is keyed on the env var's VALUE as well as its name, so a read made
# while the variable was unset (or held something else) never decides a later
# read; failures are not cached at all. One entry per keyset name.
_keyset_cache: Dict[str, Tuple[str, Dict[str, HeldKey]]] = {}
_keyset_cache_lock = threading.Lock()
KEYSET_CACHE_MAX = 64


def _clear_keyset_cache() -> None:
    with _keyset_cache_lock:
        _keyset_cache.clear()


def _held_keys(keyset: str, env: Mapping[str, str]) -> Dict[str, HeldKey]:
    value = env.get(keyset)
    with _keyset_cache_lock:
        cached = _keyset_cache.get(keyset)
        if cached is not None and cached[0] != value:
            # Unset, emptied or changed since it was cached: drop it, so a
            # name whose variable is now unset is never held in the cache.
            del _keyset_cache[keyset]
            cached = None
    if cached is not None:
        return cached[1]
    if value is None:
        return parse_keyset_env(keyset, None)  # raises keyset_missing
    held = parse_keyset_env(keyset, value)  # raises for empty / invalid: not cached
    with _keyset_cache_lock:
        # Keyset names are writer-chosen (any valid name can appear in a
        # value), so the cache is bounded; the oldest name is evicted.
        _keyset_cache.pop(keyset, None)
        while len(_keyset_cache) >= KEYSET_CACHE_MAX:
            _keyset_cache.pop(next(iter(_keyset_cache)))
        _keyset_cache[keyset] = (value, held)
    return held


# --------------------------------------------------------------------------
# The ciphertext string (spec 3)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Parsed:
    keyset: str
    fingerprint: str
    ephemeral: bytes
    ciphertext_and_tag: bytes
    aad: bytes


def _name(keyset: str) -> str:
    """A VALID keyset name as echoed in error text: truncated to 64
    characters. An invalid name is never echoed at all (see ``parse``)."""
    return keyset if len(keyset) <= 64 else keyset[:64] + "..."


def parse(value: str) -> Parsed:
    """Spec 3.5, in order; the first failing step decides the class. A
    small-order E is not rejected here: decrypt checks S instead (spec 8)."""
    if not isinstance(value, str):
        raise Qf1Error("malformed", "secret value is not a string")
    # 1. version
    if not value.startswith("qf1:"):
        if _OTHER_VERSION_RE.match(value):
            raise Qf1Error(
                "unsupported_version", "value uses a newer qf format than this SDK reads"
            )
        raise Qf1Error("malformed", 'value does not start with "qf1:"')
    # 2. exactly four fields; str.split keeps empty and trailing fields
    fields = value.split(":")
    if len(fields) != 4:
        raise Qf1Error("malformed", f"value has {len(fields)} colon-separated fields, expected 4")
    _version, keyset, fp, payload_text = fields
    # 3. keyset name, before any env lookup
    if not KEYSET_NAME_RE.fullmatch(keyset):
        raise Qf1Error(
            "invalid_keyset_name",
            # Never quoted: whatever a writer put in this field (a pasted key,
            # a plaintext) would otherwise land in errors and logs.
            f"the keyset field ({len(keyset)} characters) is not a valid keyset name "
            "(^QUONFIG_KEYSET(_[A-Z0-9]+)*$)",
        )
    # 4. fingerprint
    if not _FINGERPRINT_RE.fullmatch(fp):
        raise Qf1Error("malformed", "fingerprint is not 8 lowercase hex characters")
    # 5. payload
    payload = _b64url_decode_strict(payload_text)
    if payload is None:
        raise Qf1Error("malformed", "payload is not canonical unpadded base64url")
    if len(payload) < MIN_PAYLOAD:
        raise Qf1Error(
            "malformed", f"payload is {len(payload)} bytes, at least {MIN_PAYLOAD} needed"
        )
    # 6. top bit of E
    ephemeral = payload[:32]
    if ephemeral[31] & 0x80:
        raise Qf1Error("malformed", "ephemeral public key has its top bit set")
    # 7.
    return Parsed(
        keyset=keyset,
        fingerprint=fp,
        ephemeral=ephemeral,
        ciphertext_and_tag=payload[32:],
        aad=aad(keyset, fp),
    )


# --------------------------------------------------------------------------
# Decrypt (spec 8)
# --------------------------------------------------------------------------


def decrypt(value: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Decrypt one stored qf1 value. ``env`` defaults to ``os.environ``; only
    the variable named by the value's keyset is read."""
    parsed = parse(value)
    lookup_error: Optional[Qf1Error] = None
    held: Dict[str, HeldKey] = {}
    try:
        held = _held_keys(parsed.keyset, os.environ if env is None else env)
    except Qf1Error as e:
        lookup_error = e
    if lookup_error is not None:
        # Name the value's keyset and fingerprint too (spec 9.2), raised
        # outside the except so nothing is chained.
        raise Qf1Error(
            lookup_error.code,
            f"value is encrypted to {_name(parsed.keyset)} fingerprint {parsed.fingerprint}; "
            f"{lookup_error.detail}",
        )
    key = held.get(parsed.fingerprint)
    if key is None:
        held_list = ", ".join(sorted(held))
        raise Qf1Error(
            "key_not_held",
            f"value is encrypted to {_name(parsed.keyset)} fingerprint {parsed.fingerprint}; "
            f"this runtime holds {_name(parsed.keyset)} fingerprints [{held_list}]",
        )
    shared = x25519(key.private, parsed.ephemeral)
    if not any(shared):
        raise Qf1Error(
            "invalid_ephemeral_key",
            "the ephemeral public key gives an all-zero shared secret",
        )
    d = derive(shared, parsed.ephemeral, key.public)
    plaintext_bytes: Optional[bytes] = None
    try:
        plaintext_bytes = AESGCM(d.key).decrypt(d.nonce, parsed.ciphertext_and_tag, parsed.aad)
    except InvalidTag:
        pass
    if plaintext_bytes is None:
        raise Qf1Error(
            "decrypt_failed",
            f"authentication failed for a value on {_name(parsed.keyset)} "
            f"fingerprint {parsed.fingerprint}",
        )
    text: Optional[str] = None
    try:
        text = plaintext_bytes.decode("utf-8")  # strict: no replacement, BOM kept
    except UnicodeDecodeError:
        pass
    if text is None:
        raise Qf1Error("decrypt_failed", "the decrypted value is not valid UTF-8")
    return text
