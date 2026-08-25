"""Public `flush()` — synchronous telemetry drain (qfg-0xj3.3).

Mirrors sdk-node's `flush()` (src/quonfig.ts:925). On a serverless host the
telemetry reporter's 60s daemon timer does not fire while the environment is
frozen, so evaluation summaries recorded during a request can sit in the
collectors until the container is recycled — and are then lost. A handler
calls ``client.flush()`` before returning to deliver them synchronously.

Contract:
- ``TelemetryReporter.flush()`` is public and thread-safe: safe to call
  concurrently with the 60s timer loop, and the same drained events are never
  sent twice.
- ``Quonfig.flush()`` delegates, and is a silent no-op when telemetry is
  disabled / ``None`` (the keyless datadir path).
- ``flush()`` never raises into the caller — a failing POST is logged.

RED baseline: neither ``Quonfig.flush`` nor ``TelemetryReporter.flush``
exists, so these fail with ``AttributeError: 'Quonfig' object has no attribute
'flush'`` / ``'TelemetryReporter' object has no attribute 'flush'``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, List

from quonfig import Quonfig
from quonfig.telemetry import TelemetryReporter
from quonfig.types import EvalResult


def _make_minimal_datadir(tmp_path: Path) -> str:
    """A workspace with one trivial config so load_datadir succeeds."""
    datadir = tmp_path / "workspace"
    datadir.mkdir()
    (datadir / "quonfig.json").write_text(
        json.dumps({"environments": ["Production"]}), encoding="utf-8"
    )
    configs_dir = datadir / "configs"
    configs_dir.mkdir()
    rule = {
        "criteria": [{"operator": "ALWAYS_TRUE"}],
        "value": {"type": "string", "value": "ok"},
    }
    (configs_dir / "noop.json").write_text(
        json.dumps(
            {
                "id": "noop-id",
                "key": "noop",
                "type": "config",
                "valueType": "string",
                "sendToClientSdk": False,
                "default": {"rules": [rule]},
                "environments": [{"id": "Production", "rules": [rule]}],
            }
        ),
        encoding="utf-8",
    )
    return str(datadir)


class _TelemetryCapture:
    """A real HTTP server recording every telemetry POST it receives.

    Same pattern as ``test_telemetry_defaults.py`` — a mocked session would
    not prove the request actually left the SDK.
    """

    def __init__(self) -> None:
        outer = self
        self.posts: list[str] = []

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


def _eval_result(key: str = "k1") -> EvalResult:
    return EvalResult(
        value=None,
        raw_value=None,
        value_type="string",
        reason="RULE_MATCH",
        row_index=0,
        config_id="cfg-1",
        config_key=key,
        config_type="config",
        resolved_value="hello",
    )


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None


# ----------------------------------------------------------------------
# Quonfig.flush()
# ----------------------------------------------------------------------


def test_flush_delivers_pending_eval_summaries(tmp_path: Path, monkeypatch: Any) -> None:
    """The whole point: telemetry recorded during a request is delivered by an
    explicit flush(), WITHOUT waiting for the 60s timer or for close()."""
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    capture = _TelemetryCapture()
    datadir = _make_minimal_datadir(tmp_path)
    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=datadir,
        environment="Production",
        telemetry_url=capture.url,
    )
    try:
        client.init()
        assert client.get("noop") == "ok"
        assert capture.posts == [], "nothing should have been sent before flush()"

        client.flush()

        assert capture.posts, "flush() delivered no telemetry"
        assert any("noop" in body for body in capture.posts), (
            f"no flushed POST carried the evaluated key: {capture.posts}"
        )
    finally:
        client.close()
        capture.close()


def test_flush_is_a_noop_when_telemetry_disabled(tmp_path: Path, monkeypatch: Any) -> None:
    """Keyless datadir client has no reporter — flush() must be silent."""
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    capture = _TelemetryCapture()
    datadir = _make_minimal_datadir(tmp_path)
    client = Quonfig(datadir=datadir, environment="Production", telemetry_url=capture.url)
    try:
        client.init()
        assert client.get("noop") == "ok"
        assert client._telemetry is None
        client.flush()  # must not raise
        assert capture.posts == []
    finally:
        client.close()
        capture.close()


def test_flush_returns_none_and_never_raises_on_post_failure(monkeypatch: Any) -> None:
    """A telemetry endpoint that is down must not break the request path."""
    monkeypatch.setattr("quonfig.telemetry.reporter.time.sleep", lambda *_a, **_k: None)
    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        api_urls=["http://127.0.0.1:1"],
        telemetry_url="http://127.0.0.1:1",
        fallback_poll_enabled=False,
    )
    try:
        assert client._telemetry is not None
        client._telemetry.record_evaluation(_eval_result())

        def boom(*_a: Any, **_k: Any) -> None:
            raise ConnectionError("telemetry endpoint down")

        monkeypatch.setattr(client._telemetry._session, "post", boom)
        assert client.flush() is None  # must not raise
    finally:
        client.close()


# ----------------------------------------------------------------------
# TelemetryReporter.flush()
# ----------------------------------------------------------------------


def test_reporter_flush_posts_recorded_evaluations(monkeypatch: Any) -> None:
    reporter = TelemetryReporter(telemetry_url="http://127.0.0.1:1", sdk_key="k", interval=3600.0)
    posts: List[dict] = []

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        posts.append(kwargs["json"])
        return _FakeResponse()

    monkeypatch.setattr(reporter._session, "post", fake_post)

    reporter.record_evaluation(_eval_result())
    reporter.flush()

    assert len(posts) == 1, f"expected exactly one POST, saw {len(posts)}"
    assert "k1" in json.dumps(posts[0])


def test_reporter_flush_with_nothing_pending_does_not_post(monkeypatch: Any) -> None:
    reporter = TelemetryReporter(telemetry_url="http://127.0.0.1:1", sdk_key="k", interval=3600.0)
    posts: List[dict] = []
    monkeypatch.setattr(
        reporter._session,
        "post",
        lambda url, **kwargs: (posts.append(kwargs["json"]), _FakeResponse())[1],
    )
    reporter.flush()
    assert posts == []


def test_concurrent_flush_and_timer_flush_do_not_double_send(monkeypatch: Any) -> None:
    """A caller's flush() racing the 60s timer's flush must not deliver the
    same drained events twice — collectors are drained exactly once, so the
    losing racer finds nothing to send."""
    reporter = TelemetryReporter(telemetry_url="http://127.0.0.1:1", sdk_key="k", interval=3600.0)
    posts: List[dict] = []
    posts_lock = threading.Lock()
    gate = threading.Event()

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        with posts_lock:
            posts.append(kwargs["json"])
        return _FakeResponse()

    monkeypatch.setattr(reporter._session, "post", fake_post)

    for _ in range(3):
        reporter.record_evaluation(_eval_result())

    errors: List[BaseException] = []

    def public_flush() -> None:
        gate.wait(5.0)
        try:
            reporter.flush()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def timer_flush() -> None:
        gate.wait(5.0)
        try:
            reporter._flush()  # exactly what the 60s daemon loop calls
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=public_flush) for _ in range(4)]
    threads += [threading.Thread(target=timer_flush) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(timeout=5.0)

    assert errors == [], f"concurrent flush raised: {errors}"
    assert len(posts) == 1, f"the same drained events were sent {len(posts)} times"

    total = 0
    for summary in posts[0]["events"][0]["summaries"]["summaries"]:
        for counter in summary["counters"]:
            total += counter["count"]
    assert total == 3, f"expected all 3 recorded evaluations in one payload, got {total}"


def test_reporter_flush_swallows_post_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr("quonfig.telemetry.reporter.time.sleep", lambda *_a, **_k: None)
    reporter = TelemetryReporter(telemetry_url="http://127.0.0.1:1", sdk_key="k", interval=3600.0)

    def boom(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("down")

    monkeypatch.setattr(reporter._session, "post", boom)
    reporter.record_evaluation(_eval_result())
    assert reporter.flush() is None  # must not raise
