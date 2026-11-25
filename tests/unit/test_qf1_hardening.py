"""qf1 hardening beyond the shared vectors (qfg-8seuf.10 validation items).

R1 for every decryptWith shape, the keyset cache (hits, env changes, bound),
version prefixes the vectors do not pin, the 3.5 order with two faults,
which library errors map to which class, and that the read path never
touches the deprecated ``quonfig.crypto`` module.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from typing import Any, Dict, Iterator, List

import pytest
from cryptography.exceptions import InvalidTag

from quonfig import Quonfig, QuonfigDecryptionError
from quonfig import _qf1 as qf1
from quonfig.evaluator import Evaluator
from quonfig.types import ConfigEnvelope

from .test_qf1_vectors import NEGATIVE, POSITIVE, V, _env_unchanged  # noqa: F401

SHORT = POSITIVE[0]
ENV = "Production"


@pytest.fixture(autouse=True)
def _cold_cache() -> Iterator[None]:
    qf1._clear_keyset_cache()
    yield
    qf1._clear_keyset_cache()


def _doc(key: str, value: Dict[str, Any], doc_type: str = "config") -> Dict[str, Any]:  # noqa: E501
    rule = {"criteria": [{"operator": "ALWAYS_TRUE"}], "value": value}
    return {
        "id": key,
        "key": key,
        "type": doc_type,
        "valueType": "string",
        "default": {"rules": [rule]},
        "environments": [{"id": ENV, "rules": [rule]}],
    }


def _client(entries: List[Dict[str, Any]]) -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment=ENV,
        on_init_failure="continue",
        on_no_default="error",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        enable_quonfig_user_context=False,
    )
    c._store.update(
        ConfigEnvelope.from_dict({"configs": entries, "meta": {"version": "v", "environment": ENV}})
    )
    c._evaluator = Evaluator(c._store, ENV)
    c._started = True
    return c


# --------------------------------------------------------------------------
# 1. R1 for every decryptWith shape
# --------------------------------------------------------------------------

FAILING_SHAPES = ["key.cfg", 5, 0, 1.5, [], ["key.cfg"], {}, {"a": 1}, True, False, " "]


@pytest.mark.parametrize("confidential", [True, False])
@pytest.mark.parametrize("shape", FAILING_SHAPES, ids=[repr(s) for s in FAILING_SHAPES])
def test_decrypt_with_of_any_shape_fails_and_reads_no_key_config(
    shape: Any, confidential: bool
) -> None:
    value: Dict[str, Any] = {"type": "string", "value": "stored-old-value", "decryptWith": shape}
    if confidential:
        value["confidential"] = True
    c = _client([_doc("old", value), _doc("key.cfg", {"type": "string", "value": "00" * 32})])
    reads: List[str] = []
    real_get = c._store.get

    def spy(key: str) -> Any:
        reads.append(key)
        return real_get(key)

    c._store.get = spy  # type: ignore[method-assign]
    with pytest.raises(QuonfigDecryptionError) as info:
        c.get("old")
    assert info.value.code == "decrypt_with_unsupported"
    assert "decryptWith" in str(info.value)
    assert "stored-old-value" not in str(info.value)
    assert "key.cfg" not in reads
    assert c.get("old", default="d") == "d"


@pytest.mark.parametrize("present, shape", [(False, None), (True, None), (True, "")])
def test_absent_null_or_empty_decrypt_with_is_a_plain_value(present: bool, shape: Any) -> None:
    value: Dict[str, Any] = {"type": "string", "value": "plain"}
    if present:
        value["decryptWith"] = shape
    assert _client([_doc("p", value)]).get("p") == "plain"


# --------------------------------------------------------------------------
# 4. keyset cache: hits, env changes, bound
# --------------------------------------------------------------------------


@pytest.fixture
def x25519_calls(monkeypatch: pytest.MonkeyPatch) -> List[int]:
    calls: List[int] = []
    real = qf1.x25519

    def counting(scalar: bytes, u: bytes) -> bytes:
        calls.append(1)
        return real(scalar, u)

    monkeypatch.setattr(qf1, "x25519", counting)
    return calls


def test_keyset_cache_hits(x25519_calls: List[int]) -> None:
    # The two-key keyset: a cold read derives 2 public keys + 1 ECDH; a warm
    # read of the same stored value does only the ECDH. Plaintext is not
    # cached, so that one X25519 per read is the floor.
    case = next(p for p in POSITIVE if p["keyset"] == "QUONFIG_KEYSET_TEST_PROD")
    env = dict(V["positiveEnv"])
    assert qf1.decrypt(case["ciphertext"], env=env) == case["plaintext"]
    cold = len(x25519_calls)
    assert cold == 3
    assert qf1.decrypt(case["ciphertext"], env=env) == case["plaintext"]
    assert len(x25519_calls) - cold == 1


def test_env_change_empty_and_restore_take_effect_on_next_read() -> None:
    case = SHORT
    good = V["positiveEnv"][case["keyset"]]
    other = V["positiveEnv"]["QUONFIG_KEYSET"]  # a valid key with another fingerprint
    env: Dict[str, str] = {case["keyset"]: good}
    assert qf1.decrypt(case["ciphertext"], env=env) == case["plaintext"]
    env[case["keyset"]] = other
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=env)
    assert info.value.code == "key_not_held"
    env[case["keyset"]] = ""
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=env)
    assert info.value.code == "keyset_missing"
    del env[case["keyset"]]
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(case["ciphertext"], env=env)
    assert info.value.code == "keyset_missing"
    assert case["keyset"] not in qf1._keyset_cache  # an unset name is never cached
    env[case["keyset"]] = good
    assert qf1.decrypt(case["ciphertext"], env=env) == case["plaintext"]


def test_keyset_cache_is_bounded() -> None:
    good = V["positiveEnv"][SHORT["keyset"]]
    head, _, payload = SHORT["ciphertext"].rpartition(":")
    fp = head.split(":")[2]
    assert qf1.KEYSET_CACHE_MAX == 64
    for i in range(84):
        name = f"QUONFIG_KEYSET_N{i}"
        # Genuine header mismatch -> decrypt_failed, but the keyset is parsed.
        with pytest.raises(qf1.Qf1Error):
            qf1.decrypt(f"qf1:{name}:{fp}:{payload}", env={name: good})
        assert len(qf1._keyset_cache) <= 64
    assert len(qf1._keyset_cache) == 64


# --------------------------------------------------------------------------
# 6. cases the vectors do not pin
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, code",
    [
        ("qf0:QUONFIG_KEYSET:00000000:" + "A" * 64, "malformed"),
        ("qf01:QUONFIG_KEYSET:00000000:" + "A" * 64, "malformed"),
        ("qf:QUONFIG_KEYSET:00000000:" + "A" * 64, "malformed"),
        ("QF1:QUONFIG_KEYSET:00000000:" + "A" * 64, "malformed"),
        ("qf2:anything", "unsupported_version"),
        ("qf10:QUONFIG_KEYSET:00000000:" + "A" * 64, "unsupported_version"),
        # Both the keyset name and the fingerprint are bad: 3.5 step 3 wins.
        ("qf1:HOME:XYZ:" + "A" * 64, "invalid_keyset_name"),
        ("qf1:QUONFIG_KEYSET_x:ABCDEF01:!!", "invalid_keyset_name"),
        # Bad fingerprint and bad payload: step 4 wins.
        ("qf1:QUONFIG_KEYSET:ABCDEF01:!!", "malformed"),
    ],
)
def test_parse_order_and_version_prefixes(value: str, code: str) -> None:
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.parse(value)
    assert info.value.code == code
    if code == "malformed" and "ABCDEF01" in value:
        assert "fingerprint" in str(info.value)


# --------------------------------------------------------------------------
# 7. only the expected library failures are mapped
# --------------------------------------------------------------------------


class _FakePriv:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def exchange(self, _pub: Any) -> bytes:
        raise self.exc


def _fake_private_cls(exc: BaseException) -> Any:
    class Fake:
        @staticmethod
        def from_private_bytes(_b: bytes) -> _FakePriv:
            return _FakePriv(exc)

    return Fake


def test_x25519_value_error_maps_to_all_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    held = qf1.parse_keyset_env(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    qf1._keyset_cache[SHORT["keyset"]] = (V["positiveEnv"][SHORT["keyset"]], held)
    monkeypatch.setattr(qf1, "X25519PrivateKey", _fake_private_cls(ValueError("small order")))
    assert qf1.x25519(b"\x01" * 32, b"\x09" + b"\x00" * 31) == bytes(32)
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(SHORT["ciphertext"], env=V["positiveEnv"])
    assert info.value.code == "invalid_ephemeral_key"


@pytest.mark.parametrize("exc", [RuntimeError("boom"), TypeError("bad"), MemoryError()])
def test_other_x25519_errors_propagate(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    monkeypatch.setattr(qf1, "X25519PrivateKey", _fake_private_cls(exc))
    with pytest.raises(type(exc)):
        qf1.x25519(b"\x01" * 32, b"\x09" + b"\x00" * 31)


def _fake_aesgcm(exc: BaseException) -> Any:
    class Fake:
        def __init__(self, _key: bytes) -> None:
            pass

        def decrypt(self, *_a: Any) -> bytes:
            raise exc

    return Fake


def test_invalid_tag_maps_to_decrypt_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qf1, "AESGCM", _fake_aesgcm(InvalidTag()))
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(SHORT["ciphertext"], env=V["positiveEnv"])
    assert info.value.code == "decrypt_failed"
    assert info.value.__context__ is None


@pytest.mark.parametrize("exc", [ValueError("nonce"), RuntimeError("boom")])
def test_other_aesgcm_errors_propagate(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    monkeypatch.setattr(qf1, "AESGCM", _fake_aesgcm(exc))
    with pytest.raises(type(exc)) as info:
        qf1.decrypt(SHORT["ciphertext"], env=V["positiveEnv"])
    assert not isinstance(info.value, QuonfigDecryptionError)


# --------------------------------------------------------------------------
# 9. machine-readable code
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", NEGATIVE[:5], ids=[n["name"] for n in NEGATIVE[:5]])
def test_code_is_on_the_public_exception_type(case: Dict[str, Any]) -> None:
    with pytest.raises(QuonfigDecryptionError) as info:
        qf1.decrypt(case["ciphertext"], env=case["env"])
    assert info.value.code == case["expectedError"]


def test_public_exception_code_defaults_to_none() -> None:
    assert QuonfigDecryptionError("x").code is None


# --------------------------------------------------------------------------
# deprecated quonfig.crypto is never on the read path
# --------------------------------------------------------------------------


def test_read_path_never_imports_quonfig_crypto(tmp_path: Any) -> None:
    script = textwrap.dedent(
        f"""
        import json, sys
        from quonfig import Quonfig
        from quonfig.evaluator import Evaluator
        from quonfig.types import ConfigEnvelope

        def doc(key, value, t="config"):
            rule = {{"criteria": [{{"operator": "ALWAYS_TRUE"}}], "value": value}}
            return {{"id": key, "key": key, "type": t, "valueType": "string",
                    "default": {{"rules": [rule]}}, "environments": [{{"id": "Production", "rules": [rule]}}]}}

        entries = [
            doc("plain", {{"type": "string", "value": "p"}}),
            doc("conf", {{"type": "string", "value": "c", "confidential": True}}),
            doc("old", {{"type": "string", "value": "a--b--c", "confidential": True, "decryptWith": "k"}}),
            doc("k", {{"type": "string", "value": "00"}}),
            doc("sec", {{"type": "string", "value": {SHORT["ciphertext"]!r}, "confidential": True}}, "secret"),
            doc("bad", {{"type": "string", "value": "nope", "confidential": True}}, "secret"),
        ]
        c = Quonfig(sdk_key="", datadir=None, environment="Production", on_init_failure="continue",
                    on_no_default="ignore", collect_evaluation_summaries=False,
                    context_upload_mode="none", enable_quonfig_user_context=False)
        c._store.update(ConfigEnvelope.from_dict({{"configs": entries, "meta": {{"version": "v", "environment": "Production"}}}}))
        c._evaluator = Evaluator(c._store, "Production")
        c._started = True
        for k in ["plain", "conf", "old", "k", "sec", "bad"]:
            c.get(k)
            c.get_string_details(k)
            c.evaluate_details(k)
        assert "quonfig.crypto" not in sys.modules, "read path imported quonfig.crypto"
        print("OK")
        """
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("QUONFIG_KEYSET")}
    env[SHORT["keyset"]] = V["positiveEnv"][SHORT["keyset"]]
    r = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("OK")


# --------------------------------------------------------------------------
# A. R1 beats the secret path
# --------------------------------------------------------------------------


@pytest.mark.parametrize("confidential", [True, False])
def test_decrypt_with_on_a_secret_fails_before_decryption(
    monkeypatch: pytest.MonkeyPatch, confidential: bool
) -> None:
    # A genuine qf1 value the runtime CAN decrypt, but carrying decryptWith.
    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    value: Dict[str, Any] = {"type": "string", "value": SHORT["ciphertext"], "decryptWith": "k"}
    if confidential:
        value["confidential"] = True
    calls: List[int] = []
    real = qf1.decrypt
    monkeypatch.setattr(qf1, "decrypt", lambda *a, **k: calls.append(1) or real(*a, **k))
    c = _client([_doc("s", value, doc_type="secret")])
    with pytest.raises(QuonfigDecryptionError) as info:
        c.get("s")
    assert info.value.code == "decrypt_with_unsupported"
    assert calls == []
    assert SHORT["plaintext"] not in str(info.value)


def test_decrypt_with_on_a_datadir_secret_fails(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    base = tmp_path / "ws"
    (base / "secrets").mkdir(parents=True)
    (base / "quonfig.json").write_text(json.dumps({"environments": [ENV]}))
    doc = _doc(
        "s",
        {"type": "string", "value": SHORT["ciphertext"], "confidential": True, "decryptWith": "k"},
        doc_type="secret",
    )
    (base / "secrets" / "s.json").write_text(json.dumps(doc))
    c = Quonfig(
        sdk_key="",
        datadir=str(base),
        environment=ENV,
        on_no_default="error",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        enable_quonfig_user_context=False,
    )
    c.init()
    try:
        with pytest.raises(QuonfigDecryptionError) as info:
            c.get_string("s")
        assert info.value.code == "decrypt_with_unsupported"
    finally:
        c.close()


# --------------------------------------------------------------------------
# B. authentication failure leaks no derived secret
# --------------------------------------------------------------------------


def _encodings(b: bytes) -> List[str]:
    import base64

    out = [b.hex(), b.hex().upper()]
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        t = enc(b).decode("ascii")
        out += [t, t.rstrip("=")]
    return out


def test_tampered_tag_leaks_no_shared_secret_prk_okm_key_or_nonce(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import base64
    import logging
    import traceback

    case = SHORT
    head, _, payload = case["ciphertext"].rpartition(":")
    blob = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    blob[-1] ^= 0x01
    tampered = head + ":" + base64.urlsafe_b64encode(bytes(blob)).rstrip(b"=").decode()
    monkeypatch.setenv(case["keyset"], V["positiveEnv"][case["keyset"]])
    # Every secret the failing decrypt derives.
    priv = qf1.parse_private_key_entry(case["recipientPrivateKeyEntry"])
    e = bytes(blob[:32])
    shared = qf1.x25519(priv, e)
    d = qf1.derive(shared, e, qf1.public_key(priv))
    forbidden: List[str] = []
    for secret in (shared, d.prk, d.okm, d.key, d.nonce, priv):
        forbidden += _encodings(secret)
    c = _client([_doc("t", {"type": "string", "value": tampered, "confidential": True}, "secret")])
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(QuonfigDecryptionError) as info:
            c.get_string("t")
        details = c.get_string_details("t")
    err = info.value
    assert err.code == "decrypt_failed"
    assert err.__cause__ is None and err.__context__ is None
    texts = [str(err), repr(err), repr(err.args), "".join(traceback.format_exception(err))]
    texts += [details.error_message or "", caplog.text]
    for t in texts:
        for f in forbidden:
            assert f not in t


# --------------------------------------------------------------------------
# C. the confidential flag survives the read
# --------------------------------------------------------------------------


def test_decrypted_read_keeps_the_stored_value_and_its_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SDK exposes no per-value "confidential" field in details or
    # metadata; what it exposes is config_type "secret". The stored Value
    # keeps its flag and its ciphertext (the plaintext is never written back).
    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    c = _client(
        [
            _doc(
                "s",
                {"type": "string", "value": SHORT["ciphertext"], "confidential": True},
                "secret",
            )
        ]
    )
    assert c.get_string("s") == SHORT["plaintext"]
    d = c.get_string_details("s")
    assert d.value == SHORT["plaintext"]
    assert d.flag_metadata.get("config_type") == "secret"
    assert c.evaluate_details("s").flag_metadata.get("config_type") == "secret"
    stored = c._store.get("s")
    assert stored is not None
    v = stored.default.rules[0].value
    assert v is not None and v.confidential is True and v.value == SHORT["ciphertext"]


# --------------------------------------------------------------------------
# D. junk in secrets/ never breaks startup and never logs content
# --------------------------------------------------------------------------


def test_secrets_dir_junk_is_skipped_with_path_only_warnings(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import json
    import logging

    from quonfig.datadir import load_datadir

    base = tmp_path / "ws"
    (base / "configs").mkdir(parents=True)
    (base / "secrets" / "nested").mkdir(parents=True)
    (base / "secrets" / "dir.json").mkdir()
    (base / "quonfig.json").write_text(json.dumps({"environments": [ENV]}))
    (base / "configs" / "ok.json").write_text(
        json.dumps(_doc("ok", {"type": "string", "value": "v"}))
    )
    (base / "secrets" / "notes.txt").write_text("JUNK-CONTENT-1")
    (base / "secrets" / "broken.json").write_text("{ JUNK-CONTENT-2 qf1-priv-")
    (base / "secrets" / "nokey.json").write_text(json.dumps({"value": "JUNK-CONTENT-3"}))
    (base / "secrets" / "list.json").write_text(json.dumps(["JUNK-CONTENT-4"]))
    (base / "secrets" / "nested" / "deep.json").write_text(
        json.dumps(_doc("deep", {"type": "string", "value": "x"}))
    )
    with caplog.at_level(logging.WARNING, logger="quonfig.datadir"):
        env = load_datadir(str(base), ENV)
    assert [c.key for c in env.configs] == ["ok"]
    warned = [r.getMessage() for r in caplog.records]
    for name in ("broken.json", "nokey.json", "list.json", "dir.json"):
        assert sum(name in m for m in warned) == 1, warned
    assert not any("notes.txt" in m or "deep.json" in m for m in warned)
    assert "JUNK-CONTENT" not in caplog.text and "qf1-priv-" not in caplog.text


# --------------------------------------------------------------------------
# E. a runtime that refuses X25519
# --------------------------------------------------------------------------


def test_runtime_refusing_x25519_fails_per_value_with_a_clear_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.exceptions import UnsupportedAlgorithm

    class Refusing:
        @staticmethod
        def from_private_bytes(_b: bytes) -> Any:
            raise UnsupportedAlgorithm("X25519 is not supported by this version of OpenSSL.")

    monkeypatch.setattr(qf1, "X25519PrivateKey", Refusing)
    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    c = _client(
        [
            _doc(
                "s",
                {"type": "string", "value": SHORT["ciphertext"], "confidential": True},
                "secret",
            ),
            _doc("p", {"type": "string", "value": "plain"}),
        ]
    )
    with pytest.raises(QuonfigDecryptionError) as info:
        c.get_string("s")
    msg = str(info.value)
    assert info.value.code == "unsupported_runtime"
    assert "X25519" in msg and "runtime" in msg
    assert "entry" not in msg and "not a qf1 private key" not in msg
    assert info.value.__context__ is None and info.value.__cause__ is None
    assert c.get_string("p") == "plain"


# --------------------------------------------------------------------------
# F. regexp and env traps
# --------------------------------------------------------------------------


class _AnswerEverything(dict):  # type: ignore[type-arg]
    """An env lookup that answers EVERY name with a valid keyset, recording
    each name asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: List[str] = []

    def get(self, k: str, default: Any = None) -> Any:
        self.reads.append(k)
        return V["positiveEnv"][SHORT["keyset"]]

    def __getitem__(self, k: str) -> Any:
        return self.get(k)

    def __contains__(self, k: object) -> bool:
        return True


@pytest.mark.parametrize(
    "name",
    ["QUONFIG_KEYSET_TEST\n", "HOME\nQUONFIG_KEYSET", "QUONFIG_KEYSET\n", "\nQUONFIG_KEYSET"],
)
def test_newline_keyset_names_are_refused_with_no_env_read(name: str) -> None:
    head, _, payload = SHORT["ciphertext"].rpartition(":")
    fp = head.split(":")[2]
    env = _AnswerEverything()
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.decrypt(f"qf1:{name}:{fp}:{payload}", env=env)
    assert info.value.code == "invalid_keyset_name"
    assert env.reads == []


TRAP_CHARS = ["\x0b", "\x0c", "\xa0", " ", " ", "﻿", "\x85", "　", "​", "\x00"]


@pytest.mark.parametrize("where", ["leading", "trailing"])
@pytest.mark.parametrize("ch", TRAP_CHARS, ids=[f"U+{ord(c):04X}" for c in TRAP_CHARS])
def test_non_stripped_characters_around_an_entry_make_the_keyset_invalid(
    ch: str, where: str
) -> None:
    good = V["positiveEnv"][SHORT["keyset"]]
    value = ch + good if where == "leading" else good + ch
    with pytest.raises(qf1.Qf1Error) as info:
        qf1.parse_keyset_env(SHORT["keyset"], value)
    assert info.value.code == "keyset_invalid"
    assert good not in str(info.value)
