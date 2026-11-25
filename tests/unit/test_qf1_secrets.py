"""Secrets end to end through the public client (qfg-8seuf.10).

Spec: ``project/plans/secrets-qf1-spec.md`` sections 8, 9.2, 9.3, 13.3 (R1),
13.5 and 13.7. Ciphertexts come from the shared vectors file
(``test_qf1_vectors.py`` reads the same file) or are sealed here at run time to
keys generated at run time, so no private key is ever written in this repo.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from quonfig import Quonfig, QuonfigDecryptionError
from quonfig import _qf1 as qf1
from quonfig.datadir import SUBDIRS, load_datadir
from quonfig.evaluator import Evaluator
from quonfig.types import ConfigEnvelope

from .test_qf1_vectors import NEGATIVE, POSITIVE, V

ENV = "Production"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_keysets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never read the machine's real keysets; start every test from none."""
    for name in list(os.environ):
        if name.startswith("QUONFIG_KEYSET"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    yield


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class RuntimeKey:
    """A keypair generated for one test run. Its private entry is built at run
    time and never written to disk."""

    def __init__(self) -> None:
        self.priv = X25519PrivateKey.generate().private_bytes_raw()
        self.pub = qf1.public_key(self.priv)
        self.fp = qf1.fingerprint(self.pub)
        self.entry = "qf1-priv-" + _b64(self.priv)

    def seal(self, keyset: str, plaintext: bytes) -> str:
        """Test-only writer (spec 7), built from the SDK's read-side pieces."""
        priv_e = X25519PrivateKey.generate().private_bytes_raw()
        e = qf1.public_key(priv_e)
        d = qf1.derive(qf1.x25519(priv_e, self.pub), e, self.pub)
        aad = qf1.aad(keyset, self.fp)
        return aad.decode("ascii") + _b64(e + AESGCM(d.key).encrypt(d.nonce, plaintext, aad))


def _doc(key: str, value: Dict[str, Any], doc_type: str = "config", value_type: str = "string"):
    rule = {"criteria": [{"operator": "ALWAYS_TRUE"}], "value": value}
    return {
        "id": f"{key}-id",
        "key": key,
        "type": doc_type,
        "valueType": value_type,
        "sendToClientSdk": False,
        "default": {"rules": [rule]},
        "environments": [{"id": ENV, "rules": [rule]}],
    }


def _secret(key: str, ciphertext: str, confidential: bool = True) -> Dict[str, Any]:
    value: Dict[str, Any] = {"type": "string", "value": ciphertext}
    if confidential:
        value["confidential"] = True
    return _doc(key, value, doc_type="secret")


def _workspace(tmp_path: Path, files: Dict[str, Dict[str, Any]]) -> str:
    """files: 'configs/x.json' -> document."""
    base = tmp_path / "ws"
    base.mkdir()
    (base / "quonfig.json").write_text(json.dumps({"environments": [ENV]}), encoding="utf-8")
    for rel, doc in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc), encoding="utf-8")
    return str(base)


def _client(datadir: str, on_no_default: str = "error", **kw: Any) -> Quonfig:
    kw.setdefault("collect_evaluation_summaries", False)
    kw.setdefault("context_upload_mode", "none")
    c = Quonfig(
        sdk_key="",
        datadir=datadir,
        environment=ENV,
        on_no_default=on_no_default,
        enable_quonfig_user_context=False,
        **kw,
    )
    c.init()
    return c


SHORT = POSITIVE[0]  # short-ascii on QUONFIG_KEYSET_TEST
BARE = next(p for p in POSITIVE if p["keyset"] == "QUONFIG_KEYSET")


# --------------------------------------------------------------------------
# datadir (13.7)
# --------------------------------------------------------------------------


def test_secrets_is_the_last_datadir_directory() -> None:
    assert SUBDIRS[-1] == "secrets"
    assert SUBDIRS[:-1] == ["configs", "feature-flags", "segments", "log-levels"]


def test_directory_decides_the_secret_type(tmp_path: Path) -> None:
    doc = _secret("s.k", SHORT["ciphertext"])
    doc["type"] = "config"  # the file lies; the directory decides
    env = load_datadir(_workspace(tmp_path, {"secrets/s.k.json": doc}), ENV)
    assert [(c.key, c.type) for c in env.configs] == [("s.k", "secret")]


def test_secret_shadows_a_configs_twin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    twin = _doc(
        "twin",
        {"type": "string", "value": "old-ct", "confidential": True, "decryptWith": "old.key"},
    )
    ws = _workspace(
        tmp_path,
        {"configs/twin.json": twin, "secrets/twin.json": _secret("twin", SHORT["ciphertext"])},
    )
    c = _client(ws)
    try:
        assert c.get_string("twin") == SHORT["plaintext"]
    finally:
        c.close()


# --------------------------------------------------------------------------
# failure scope (9.3)
# --------------------------------------------------------------------------


def test_no_keyset_env_boots_and_fails_only_the_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _workspace(
        tmp_path,
        {
            "configs/plain.json": _doc("plain", {"type": "string", "value": "ok"}),
            "secrets/s.json": _secret("s", SHORT["ciphertext"]),
        },
    )
    c = _client(ws)
    try:
        assert c.get_string("plain") == "ok"
        assert c.get_string("s", default="fallback") == "fallback"
        with pytest.raises(QuonfigDecryptionError) as info:
            c.get_string("s")
        assert str(info.value).startswith("qf1 keyset_missing: ")
        assert SHORT["keyset"] in str(info.value)
        assert c.get_string_details("s").error_code == "GENERAL"
        # Still serving after the failure.
        assert c.get_string("plain") == "ok"
    finally:
        c.close()


def test_missing_keyset_fails_only_values_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BARE["keyset"], V["positiveEnv"][BARE["keyset"]])
    ws = _workspace(
        tmp_path,
        {
            "secrets/a.json": _secret("a", BARE["ciphertext"]),
            "secrets/b.json": _secret("b", SHORT["ciphertext"]),
        },
    )
    c = _client(ws)
    try:
        assert c.get_string("a") == BARE["plaintext"]
        with pytest.raises(QuonfigDecryptionError):
            c.get_string("b")
    finally:
        c.close()


# --------------------------------------------------------------------------
# R1: decryptWith is never read
# --------------------------------------------------------------------------


@pytest.mark.parametrize("confidential", [True, False])
def test_decrypt_with_value_fails(tmp_path: Path, confidential: bool) -> None:
    value: Dict[str, Any] = {"type": "string", "value": "ab--cd--ef", "decryptWith": "key.cfg"}
    if confidential:
        value["confidential"] = True
    ws = _workspace(
        tmp_path,
        {
            "configs/old.json": _doc("old", value),
            "configs/key.cfg.json": _doc("key.cfg", {"type": "string", "value": "00" * 32}),
        },
    )
    c = _client(ws)
    try:
        with pytest.raises(QuonfigDecryptionError) as info:
            c.get_string("old")
        assert "decryptWith" in str(info.value)
        assert c.get_string("old", default="d") == "d"
        assert c.get("old", default="d") == "d"
    finally:
        c.close()


# --------------------------------------------------------------------------
# wire (13.1/13.3): type "secret" entries
# --------------------------------------------------------------------------


def _wire_client(entries: List[Dict[str, Any]], on_no_default: str = "error") -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment=ENV,
        on_init_failure="continue",
        on_no_default=on_no_default,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        enable_quonfig_user_context=False,
    )
    c._store.update(
        ConfigEnvelope.from_dict(
            {"configs": entries, "meta": {"version": "v1", "environment": ENV}}
        )
    )
    c._evaluator = Evaluator(c._store, ENV)
    c._started = True
    return c


def test_wire_secret_entry_decrypts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    c = _wire_client([_secret("w", SHORT["ciphertext"])])
    assert c.get_string("w") == SHORT["plaintext"]


def test_wire_secret_that_is_not_qf1_fails_closed() -> None:
    c = _wire_client([_secret("w", "plain-text-not-ciphertext")])
    with pytest.raises(QuonfigDecryptionError) as info:
        c.get_string("w")
    assert str(info.value).startswith("qf1 malformed: ")
    assert "plain-text-not-ciphertext" not in str(info.value)


@pytest.mark.parametrize(
    "value",
    [
        {"type": "int", "value": 5, "confidential": True},
        {"type": "provided", "value": {"source": "ENV_VAR", "lookup": "HOME"}},
        {
            "type": "weighted_values",
            "value": {"weightedValues": [{"weight": 1, "value": {"type": "string", "value": "x"}}]},
        },
    ],
)
def test_secret_value_that_is_not_a_string_fails(value: Dict[str, Any]) -> None:
    c = _wire_client([_doc("w", value, doc_type="secret")])
    with pytest.raises(QuonfigDecryptionError) as info:
        c.get("w")
    assert str(info.value).startswith("qf1 malformed: ")


def test_qf1_looking_value_in_a_config_is_not_decrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    # The directory (type), not the text, marks a value as encrypted.
    monkeypatch.setenv(SHORT["keyset"], V["positiveEnv"][SHORT["keyset"]])
    c = _wire_client([_doc("c", {"type": "string", "value": SHORT["ciphertext"]})])
    assert c.get_string("c") == SHORT["ciphertext"]


# --------------------------------------------------------------------------
# telemetry (13.5)
# --------------------------------------------------------------------------


class _TelemetryCapture:
    def __init__(self) -> None:
        outer = self
        self.posts: List[str] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                outer.posts.append(self.rfile.read(length).decode())
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()


def _summaries(posts: List[str]) -> Dict[str, Tuple[str, List[Any]]]:
    out: Dict[str, Tuple[str, List[Any]]] = {}
    for body in posts:
        for event in json.loads(body).get("events", []):
            for s in (event.get("summaries") or {}).get("summaries", []):
                out[s["key"]] = (s["type"], [c["selectedValue"] for c in s["counters"]])
    return out


def _redacted(raw: str) -> str:
    return "*****" + hashlib.md5(raw.encode()).hexdigest()[:5]


def test_secret_telemetry_type_and_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = RuntimeKey()
    monkeypatch.setenv("QUONFIG_KEYSET_TELEM", key.entry)
    plaintext = "telemetry-plaintext-7Q"
    ct_flagged = key.seal("QUONFIG_KEYSET_TELEM", plaintext.encode())
    ct_unflagged = key.seal("QUONFIG_KEYSET_TELEM", plaintext.encode())
    ws = _workspace(
        tmp_path,
        {
            "secrets/flagged.json": _secret("flagged", ct_flagged),
            # No confidential flag: still redacted, because it is a secret.
            "secrets/unflagged.json": _secret("unflagged", ct_unflagged, confidential=False),
        },
    )
    capture = _TelemetryCapture()
    c = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=ws,
        environment=ENV,
        telemetry_url=capture.url,
        enable_quonfig_user_context=False,
    )
    try:
        c.init()
        assert c.get_string("flagged") == plaintext
        assert c.get_string("unflagged") == plaintext
        assert c.get_string_details("unflagged").value == plaintext
        c.flush()
        wire = "\n".join(capture.posts)
        assert capture.posts
        assert plaintext not in wire
        s = _summaries(capture.posts)
        assert s["flagged"] == ("secret", [{"string": _redacted(ct_flagged)}])
        assert s["unflagged"][0] == "secret"
        assert set(json.dumps(v) for v in s["unflagged"][1]) == {
            json.dumps({"string": _redacted(ct_unflagged)})
        }
    finally:
        c.close()
        capture.close()


# --------------------------------------------------------------------------
# typed getters on a secret: no plaintext in any error or log
# --------------------------------------------------------------------------

GETTERS = ["get_int", "get_float", "get_bool", "get_string_list", "get_json", "get_duration"]


@pytest.mark.parametrize("plaintext", ["31337", "true", "PT90S", '["a","b"]', "zz-plain-9"])
@pytest.mark.parametrize("getter", GETTERS)
def test_non_string_getter_on_secret_never_echoes_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    getter: str,
    plaintext: str,
) -> None:
    key = RuntimeKey()
    monkeypatch.setenv("QUONFIG_KEYSET_TYPED", key.entry)
    ws = _workspace(
        tmp_path,
        {"secrets/t.json": _secret("t", key.seal("QUONFIG_KEYSET_TYPED", plaintext.encode()))},
    )
    c = _client(ws)
    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(Exception) as info:
                getattr(c, getter)("t")
            assert getattr(c, getter)("t", default="d") == "d"
            details_getter = getattr(c, f"{getter}_details", None)
            details = details_getter("t") if details_getter else None
        texts = [str(info.value), repr(info.value), caplog.text]
        if details is not None:
            assert details.reason == "ERROR"
            assert details.value is None
            texts.append(details.error_message or "")
        texts.append("".join(traceback.format_exception(info.value)))
        for t in texts:
            assert plaintext not in t, t
            assert key.entry not in t
        assert c.get_string("t") == plaintext
    finally:
        c.close()


# --------------------------------------------------------------------------
# thread safety: concurrent first gets
# --------------------------------------------------------------------------


def test_concurrent_first_gets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [RuntimeKey(), RuntimeKey()]
    monkeypatch.setenv("QUONFIG_KEYSET_CONC", ",".join(k.entry for k in keys))
    files = {}
    expected = {}
    for i in range(8):
        pt = f"concurrent-{i}"
        files[f"secrets/c{i}.json"] = _secret(
            f"c{i}", keys[i % 2].seal("QUONFIG_KEYSET_CONC", pt.encode())
        )
        expected[f"c{i}"] = pt
    c = _client(_workspace(tmp_path, files))
    qf1._clear_keyset_cache()
    n = 32
    barrier = threading.Barrier(n)
    results: List[Optional[str]] = [None] * n
    errors: List[BaseException] = []

    def worker(i: int) -> None:
        try:
            barrier.wait()
            k = f"c{i % 8}"
            results[i] = c.get_string(k)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert errors == []
        assert results == [expected[f"c{i % 8}"] for i in range(n)]
    finally:
        c.close()


# --------------------------------------------------------------------------
# no leak over every failing path
# --------------------------------------------------------------------------


def _chain(e: Optional[BaseException]) -> Iterator[BaseException]:
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or e.__context__


def _env_secrets(env: Dict[str, str]) -> List[str]:
    out: List[str] = []
    for value in env.values():
        for piece in value.split(","):
            piece = piece.strip(" \t\r\n")
            if piece:
                out.append(piece)
                if piece.startswith("qf1-priv-"):
                    out.append(piece[len("qf1-priv-") :])
    return out


def _assert_no_leak(err: BaseException, forbidden: List[str]) -> None:
    assert isinstance(err, QuonfigDecryptionError)
    # Nothing chained: no library error (whose args may hold key or
    # plaintext bytes) rides along on __cause__ or __context__.
    assert err.__cause__ is None, repr(err.__cause__)
    assert err.__context__ is None, repr(err.__context__)
    texts = [str(err), repr(err), "".join(traceback.format_exception(err))]
    texts += [repr(a) for a in err.args]
    for t in texts:
        assert "qf1-priv-" not in t, t
        for f in forbidden:
            assert f not in t, (f, t)


@pytest.mark.parametrize("case", NEGATIVE, ids=[n["name"] for n in NEGATIVE])
def test_no_leak_negative_vectors(case: Dict[str, Any]) -> None:
    with pytest.raises(QuonfigDecryptionError) as info:
        qf1.decrypt(case["ciphertext"], env=case["env"])
    forbidden = _env_secrets(case["env"])
    payload = case["ciphertext"].split(":")[-1]
    if len(payload) > 8:
        forbidden.append(payload)
    _assert_no_leak(info.value, forbidden)


def _runtime_failures() -> List[Tuple[str, bytes, Any]]:
    return [
        ("bad-utf8", b"leaky-\xff-plaintext", None),
        ("surrogate", b"leaky-\xed\xa0\x80-plaintext", None),
        ("tampered-tag", b"leaky-tag-plaintext", "tamper"),
    ]


@pytest.mark.parametrize("name, plaintext, mode", _runtime_failures())
def test_no_leak_runtime_sealed_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
    plaintext: bytes,
    mode: Any,
) -> None:
    key = RuntimeKey()
    monkeypatch.setenv("QUONFIG_KEYSET_LEAK", key.entry)
    ct = key.seal("QUONFIG_KEYSET_LEAK", plaintext)
    if mode == "tamper":
        head, _, payload = ct.rpartition(":")
        blob = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        blob[-1] ^= 1
        ct = head + ":" + _b64(bytes(blob))
    c = _client(_workspace(tmp_path, {"secrets/x.json": _secret("x", ct)}))
    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(QuonfigDecryptionError) as info:
                c.get_string("x")
            details = c.get_string_details("x")
        assert info.value.code == "decrypt_failed"  # type: ignore[attr-defined]
        forbidden = [key.entry, key.entry[len("qf1-priv-") :], ct.rpartition(":")[2]]
        forbidden.append("leaky")
        _assert_no_leak(info.value, forbidden)
        for t in (details.error_message or "", caplog.text):
            for f in forbidden:
                assert f not in t
    finally:
        c.close()


@pytest.mark.parametrize(
    "env_value, code",
    [
        (None, "keyset_missing"),
        ("", "keyset_missing"),
        ("qf1-priv-SHORTBAD", "keyset_invalid"),
        ("OTHER", "keyset_invalid"),
    ],
)
def test_no_leak_keyset_errors_through_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env_value: Optional[str],
    code: str,
) -> None:
    key = RuntimeKey()
    ct = key.seal("QUONFIG_KEYSET_KS", b"keyset-error-plaintext")
    if env_value == "OTHER":
        env_value = key.entry + ",\x0b" + RuntimeKey().entry
    if env_value is not None:
        monkeypatch.setenv("QUONFIG_KEYSET_KS", env_value)
    c = _client(_workspace(tmp_path, {"secrets/x.json": _secret("x", ct)}))
    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(QuonfigDecryptionError) as info:
                c.get_string("x")
        assert info.value.code == code  # type: ignore[attr-defined]
        forbidden = ["keyset-error-plaintext", key.entry[len("qf1-priv-") :], ct.rpartition(":")[2]]
        if env_value:
            forbidden += _env_secrets({"x": env_value})
        _assert_no_leak(info.value, forbidden)
        for f in forbidden:
            assert f not in caplog.text
    finally:
        c.close()


def test_key_not_held_through_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    held, other = RuntimeKey(), RuntimeKey()
    monkeypatch.setenv("QUONFIG_KEYSET_NH", held.entry)
    ct = other.seal("QUONFIG_KEYSET_NH", b"not-held-plaintext")
    c = _client(_workspace(tmp_path, {"secrets/x.json": _secret("x", ct)}))
    try:
        with pytest.raises(QuonfigDecryptionError) as info:
            c.get_string("x")
        msg = str(info.value)
        assert msg.startswith("qf1 key_not_held: ")
        assert other.fp in msg and held.fp in msg and "QUONFIG_KEYSET_NH" in msg
        assert "config 'x'" in msg  # names the config key
        _assert_no_leak(info.value, [held.entry[9:], other.entry[9:], "not-held-plaintext"])
    finally:
        c.close()
