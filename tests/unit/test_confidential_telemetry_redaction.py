"""Regression guard (qfg-2agi.16): confidential and ``decryptWith`` values must
reach the telemetry wire only in their redacted ``*****<md5[:5]>`` form.

Drives the PUBLIC client end to end: a datadir workspace, ``Quonfig.get`` for
the evaluation, ``Quonfig.flush`` for the drain, and a real HTTP server that
captures the POSTed telemetry body. If ``Quonfig._get`` ever stops attaching
the redacted ``reportable_value`` (client.py, the telemetry block), the
plaintext lands in ``selectedValue`` and this test turns red.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List

from quonfig import Quonfig
from quonfig.crypto import encrypt, generate_new_b64_key

PLAIN_SECRET = "hunter2-plaintext-secret"
DECRYPTED_SECRET = "decrypted-plaintext-secret"


def _config(key: str, value: Dict[str, Any]) -> Dict[str, Any]:
    rule = {"criteria": [{"operator": "ALWAYS_TRUE"}], "value": value}
    return {
        "id": f"{key}-id",
        "key": key,
        "type": "config",
        "valueType": "string",
        "sendToClientSdk": False,
        "default": {"rules": [rule]},
        "environments": [{"id": "Production", "rules": [rule]}],
    }


def _make_datadir(tmp_path: Path, ciphertext: str, enc_key: str) -> str:
    datadir = tmp_path / "workspace"
    (datadir / "configs").mkdir(parents=True)
    (datadir / "quonfig.json").write_text(
        json.dumps({"environments": ["Production"]}), encoding="utf-8"
    )
    configs = [
        _config("secret.plain", {"type": "string", "value": PLAIN_SECRET, "confidential": True}),
        _config(
            "secret.encrypted",
            {
                "type": "string",
                "value": ciphertext,
                "confidential": True,
                "decryptWith": "secret.enc-key",
            },
        ),
        _config("secret.enc-key", {"type": "string", "value": enc_key}),
    ]
    for cfg in configs:
        (datadir / "configs" / f"{cfg['key']}.json").write_text(json.dumps(cfg), encoding="utf-8")
    return str(datadir)


class _TelemetryCapture:
    """Real HTTP server recording every telemetry POST body."""

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
        _host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def _selected_values(posts: List[str]) -> Dict[str, List[Any]]:
    out: Dict[str, List[Any]] = {}
    for body in posts:
        for event in json.loads(body).get("events", []):
            for summary in (event.get("summaries") or {}).get("summaries", []):
                for counter in summary.get("counters", []):
                    out.setdefault(summary["key"], []).append(counter["selectedValue"])
    return out


def _redacted(raw: str) -> str:
    return "*****" + hashlib.md5(raw.encode()).hexdigest()[:5]


def test_confidential_and_decrypt_with_values_are_redacted_on_the_wire(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    enc_key = generate_new_b64_key()
    ciphertext = encrypt(DECRYPTED_SECRET, enc_key)
    capture = _TelemetryCapture()
    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=_make_datadir(tmp_path, ciphertext, enc_key),
        environment="Production",
        telemetry_url=capture.url,
    )
    try:
        client.init()
        # The caller still gets the plaintext.
        assert client.get("secret.plain") == PLAIN_SECRET
        assert client.get("secret.encrypted") == DECRYPTED_SECRET

        client.flush()

        assert capture.posts, "flush() delivered no telemetry"
        wire = "\n".join(capture.posts)
        assert PLAIN_SECRET not in wire, f"confidential plaintext leaked: {wire}"
        assert DECRYPTED_SECRET not in wire, f"decrypted plaintext leaked: {wire}"

        selected = _selected_values(capture.posts)
        assert selected.get("secret.plain") == [{"string": _redacted(PLAIN_SECRET)}], selected
        # decryptWith hashes the stored ciphertext, never the plaintext.
        assert selected.get("secret.encrypted") == [{"string": _redacted(ciphertext)}], selected
    finally:
        client.close()
        capture.close()
