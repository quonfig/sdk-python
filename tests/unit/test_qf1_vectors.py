"""qf1 known-answer vectors (spec ``project/plans/secrets-qf1-spec.md`` section 14).

Reads ``tests/secrets/qf1-vectors.json`` from the sibling integration-test-data
checkout, the same one CI checks out next to sdk-python, so no key material is
committed here. Every key in that file is a TEST KEY derived from a public
label.

Covered: the RFC 7748 and RFC 5869 sanity checks, every ``positive`` case with
its intermediates, every ``decryptOnly`` case, every ``negative`` case with its
exact error class (``mustReject`` ones included), and every reader-relevant
``keyEntryCases`` entry (``privateKeyEntry`` and ``envValue``). The writer
cases and ``publicKeyEntry`` cases are for writers and qfg-verify; the SDK has
no writer.

The env for each case is passed explicitly: these tests never read the
machine's real ``QUONFIG_KEYSET*`` variables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from quonfig import QuonfigDecryptionError
from quonfig import _qf1 as qf1

VECTORS_PATH = (
    Path(__file__).resolve().parents[3]
    / "integration-test-data"
    / "tests"
    / "secrets"
    / "qf1-vectors.json"
)


def _load() -> Dict[str, Any]:
    if not VECTORS_PATH.exists():
        pytest.fail(
            f"qf1 vectors not found at {VECTORS_PATH}: check out integration-test-data next to sdk-python"
        )
    return json.loads(VECTORS_PATH.read_text(encoding="ascii"))


V = _load()
POSITIVE: List[Dict[str, Any]] = V["positive"]
DECRYPT_ONLY: List[Dict[str, Any]] = V["decryptOnly"]
NEGATIVE: List[Dict[str, Any]] = V["negative"]
KEY_ENTRY = [c for c in V["keyEntryCases"] if c["kind"] in ("privateKeyEntry", "envValue")]


def _ids(cases: List[Dict[str, Any]]) -> List[str]:
    return [c["name"] for c in cases]


def test_vector_file_shape() -> None:
    # Guards against a silently truncated or renamed file making the
    # parametrized tests vacuous.
    assert V["format"] == "qf1"
    assert len(POSITIVE) >= 9
    assert len(DECRYPT_ONLY) >= 1
    assert len(NEGATIVE) >= 58
    assert sum(1 for n in NEGATIVE if n["mustReject"]) >= 34
    assert len(KEY_ENTRY) >= 18
    assert V["constants"]["hkdfInfoHex"] == qf1.HKDF_INFO.hex()
    assert V["constants"]["okmLength"] == qf1.OKM_LENGTH


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def test_rfc7748_x25519() -> None:
    r = V["rfcChecks"]["rfc7748_6_1"]
    alice = bytes.fromhex(r["alicePrivateHex"])
    bob = bytes.fromhex(r["bobPrivateHex"])
    assert qf1.public_key(alice).hex() == r["alicePublicHex"]
    assert qf1.public_key(bob).hex() == r["bobPublicHex"]
    assert qf1.x25519(alice, bytes.fromhex(r["bobPublicHex"])).hex() == r["sharedSecretHex"]
    assert qf1.x25519(bob, bytes.fromhex(r["alicePublicHex"])).hex() == r["sharedSecretHex"]


def test_rfc5869_hkdf() -> None:
    r = V["rfcChecks"]["rfc5869_A_1"]
    salt, ikm, info = (bytes.fromhex(r[k]) for k in ("saltHex", "ikmHex", "infoHex"))
    prk = qf1.hkdf_extract(salt, ikm)
    assert prk.hex() == r["prkHex"]
    okm = qf1.hkdf_expand(prk, info, r["length"])
    assert okm.hex() == r["okmHex"]


@pytest.mark.parametrize("length", [0, 1, 31, 32, 33, 44, 64, 65, 255 * 32])
def test_hkdf_matches_cryptography_hkdf(length: int) -> None:
    # Cross-check of the hand-written RFC 5869 against the library's HKDF
    # (the implementation itself must not use it).
    salt, ikm, info = b"s" * 64, b"k" * 32, qf1.HKDF_INFO
    ours = qf1.hkdf_expand(qf1.hkdf_extract(salt, ikm), info, length)
    if length == 0:
        assert ours == b""
        return
    lib = HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)
    assert ours == lib


def test_hkdf_expand_rejects_oversized_length() -> None:
    with pytest.raises(ValueError):
        qf1.hkdf_expand(b"\x00" * 32, b"", 255 * 32 + 1)


# --------------------------------------------------------------------------
# positive / decryptOnly
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", POSITIVE, ids=_ids(POSITIVE))
def test_positive_intermediates(case: Dict[str, Any]) -> None:
    priv_r = qf1.parse_private_key_entry(case["recipientPrivateKeyEntry"])
    pub_r = qf1.public_key(priv_r)
    assert pub_r.hex() == case["recipientPublicKeyHex"]
    assert qf1.fingerprint(pub_r) == case["fingerprint"]

    priv_e = bytes.fromhex(case["ephemeralPrivateKeyHex"])
    e = qf1.public_key(priv_e)
    assert e.hex() == case["ephemeralPublicKeyHex"]
    # Writer side and reader side agree on S.
    shared = qf1.x25519(priv_e, pub_r)
    assert shared.hex() == case["sharedSecretHex"]
    assert qf1.x25519(priv_r, e) == shared
    # And both agree with the library's own exchange.
    lib_shared = X25519PrivateKey.from_private_bytes(priv_r).exchange(
        X25519PublicKey.from_public_bytes(e)
    )
    assert lib_shared == shared

    d = qf1.derive(shared, e, pub_r)
    assert d.salt.hex() == case["hkdfSaltHex"]
    assert qf1.HKDF_INFO.hex() == case["hkdfInfoHex"]
    assert d.prk.hex() == case["prkHex"]
    assert d.okm.hex() == case["okmHex"]
    assert d.key.hex() == case["aesKeyHex"]
    assert d.nonce.hex() == case["nonceHex"]

    aad = qf1.aad(case["keyset"], case["fingerprint"])
    assert aad.decode("ascii") == case["aadAscii"]
    assert aad.hex() == case["aadHex"]

    plaintext = bytes.fromhex(case["plaintextHex"])
    assert plaintext == case["plaintext"].encode("utf-8")
    ct_and_tag = AESGCM(d.key).encrypt(d.nonce, plaintext, aad)
    assert ct_and_tag.hex() == case["ciphertextAndTagHex"]
    assert (e + ct_and_tag).hex() == case["blobHex"]

    parsed = qf1.parse(case["ciphertext"])
    assert parsed.keyset == case["keyset"]
    assert parsed.fingerprint == case["fingerprint"]
    assert parsed.ephemeral == e
    assert parsed.aad == aad
    assert parsed.ciphertext_and_tag == ct_and_tag


@pytest.mark.parametrize("case", POSITIVE, ids=_ids(POSITIVE))
def test_positive_decrypts(case: Dict[str, Any]) -> None:
    assert qf1.decrypt(case["ciphertext"], env=V["positiveEnv"]) == case["plaintext"]


@pytest.mark.parametrize("case", DECRYPT_ONLY, ids=_ids(DECRYPT_ONLY))
def test_decrypt_only(case: Dict[str, Any]) -> None:
    out = qf1.decrypt(case["ciphertext"], env=case["env"])
    assert out == case["plaintext"]
    assert out.encode("utf-8").hex() == case["plaintextHex"]


# --------------------------------------------------------------------------
# negative
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", NEGATIVE, ids=_ids(NEGATIVE))
def test_negative_fails_with_exact_class(case: Dict[str, Any]) -> None:
    with pytest.raises(QuonfigDecryptionError) as info:
        qf1.decrypt(case["ciphertext"], env=case["env"])
    err = info.value
    assert isinstance(err, qf1.Qf1Error)
    assert err.code == case["expectedError"], str(err)
    assert str(err).startswith(f"qf1 {case['expectedError']}: "), str(err)


def test_must_reject_cases_present() -> None:
    must = [n["name"] for n in NEGATIVE if n["mustReject"]]
    for name in (
        "trailing-colon",
        "ephemeral-top-bit-set",
        "keyset-name-HOME",
        "keyset-entry-trailing-vertical-tab",
        "keyset-entry-trailing-nbsp",
        "keyset-fingerprint-collision",
        "trial-decryption-unknown-fingerprint",
        "cross-keyset-lookup",
        "utf8-encoded-surrogate",
        "blob-non-canonical",
        "trailing-newline",
        "ephemeral-low-order-u-0-order-2",
    ):
        assert name in must


def test_invalid_keyset_name_never_reads_that_env_var() -> None:
    case = next(n for n in NEGATIVE if n["name"] == "keyset-name-HOME")
    reads: List[str] = []

    class Spy(dict):  # type: ignore[type-arg]
        def get(self, k: str, default: Any = None) -> Any:
            reads.append(k)
            return super().get(k, default)

        def __getitem__(self, k: str) -> Any:
            reads.append(k)
            return super().__getitem__(k)

    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=Spy(case["env"]))
    assert info.value.code == "invalid_keyset_name"
    assert reads == []


def test_reads_only_the_named_keyset_env_var() -> None:
    case = POSITIVE[0]
    reads: List[str] = []

    class Spy(dict):  # type: ignore[type-arg]
        def get(self, k: str, default: Any = None) -> Any:
            reads.append(k)
            return super().get(k, default)

    assert qf1.decrypt(case["ciphertext"], env=Spy(V["positiveEnv"])) == case["plaintext"]
    assert reads == [case["keyset"]]


# --------------------------------------------------------------------------
# keyEntryCases (reader side)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", KEY_ENTRY, ids=_ids(KEY_ENTRY))
def test_key_entry_cases(case: Dict[str, Any]) -> None:
    if case["kind"] == "privateKeyEntry":
        if "expectedError" in case:
            with pytest.raises(qf1.Qf1Error) as info:
                qf1.parse_private_key_entry(case["input"])
            assert info.value.code == case["expectedError"]
        else:
            assert qf1.parse_private_key_entry(case["input"]).hex() == case["expectedHex"]
        return

    assert case["kind"] == "envValue"
    if "expectedError" in case:
        with pytest.raises(qf1.Qf1Error) as info:
            qf1.parse_keyset_env(case["keyset"], case["input"])
        assert info.value.code == case["expectedError"]
    else:
        held = qf1.parse_keyset_env(case["keyset"], case["input"])
        assert sorted(held) == sorted(case["expectedFingerprints"])


def test_keyset_unset_is_missing() -> None:
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.parse_keyset_env("QUONFIG_KEYSET_TEST", None)
    assert info.value.code == "keyset_missing"


def test_keyset_invalid_names_the_position_among_non_empty_pieces() -> None:
    good = V["keysets"][0]["keys"][0]["privateKeyEntry"]
    # Empty pieces before the bad one are dropped and not counted.
    value = f" , {good},,\n, qf1-priv-not-a-key ,"
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.parse_keyset_env("QUONFIG_KEYSET_TEST", value)
    assert info.value.code == "keyset_invalid"
    assert "entry 2" in str(info.value)
    assert good not in str(info.value)
    assert "not-a-key" not in str(info.value)


def test_key_not_held_names_needed_and_held_fingerprints() -> None:
    case = next(n for n in NEGATIVE if n["name"] == "fingerprint-not-held")
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=case["env"])
    msg = str(info.value)
    parsed = qf1.parse(case["ciphertext"])
    held = qf1.parse_keyset_env(parsed.keyset, case["env"][parsed.keyset])
    assert parsed.keyset in msg
    assert parsed.fingerprint in msg
    for fp in held:
        assert fp in msg


def test_keyset_missing_names_keyset_and_fingerprint() -> None:
    case = next(n for n in NEGATIVE if n["name"] == "keyset-missing")
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=case["env"])
    parsed = qf1.parse(case["ciphertext"])
    assert parsed.keyset in str(info.value)
    assert parsed.fingerprint in str(info.value)


def test_invalid_keyset_name_is_quoted_truncated_and_escaped() -> None:
    long_name = "HOME\n" + "X" * 200
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.parse(f"qf1:{long_name}:00000000:" + "A" * 64)
    msg = str(info.value)
    assert info.value.code == "invalid_keyset_name"
    assert "\n" not in msg
    assert "X" * 65 not in msg


def test_non_string_value_is_malformed() -> None:
    for bad in (None, 12, b"qf1:QUONFIG_KEYSET:00000000:AAAA", ["qf1:"]):
        with pytest.raises(qf1.Qf1Error) as info:
            qf1.parse(bad)  # type: ignore[arg-type]
        assert info.value.code == "malformed"


# --------------------------------------------------------------------------
# Keyset cache (spec 6.2 / decision 33): keyed on the env var's value
# --------------------------------------------------------------------------


def test_keyset_cache_follows_the_env_value() -> None:
    case = POSITIVE[0]
    env: Dict[str, str] = {}
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=env)
    assert info.value.code == "keyset_missing"
    # A later read with the keyset present must not be poisoned by the
    # earlier missing result.
    env[case["keyset"]] = V["positiveEnv"][case["keyset"]]
    assert qf1.decrypt(case["ciphertext"], env=env) == case["plaintext"]
    # And a changed value is honoured, not a stale cached parse.
    env[case["keyset"]] = "qf1-priv-bad"
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=env)
    assert info.value.code == "keyset_invalid"
