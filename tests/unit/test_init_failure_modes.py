"""qfg-9p4i.8 (2.0.0): on_init_failure fail | wait | continue, blocking init().

Spec: project/plans/2026-10-05-init-failure-design.md (its Decisions section
wins), sections A.3, B and C. ``init()`` blocks and retries until
``init_timeout_ms``; then ``fail`` (the default) raises ``QuonfigInitError``,
``wait`` keeps blocking, ``continue`` returns on code defaults. 401/403 from
every leg fails at once. Getters never block on init. A client that survives a
caught ``fail`` keeps retrying and heals. Old spellings are deprecated aliases.

Replaces the 1.x ``test_init_fast_failure.py`` (qfg-9p4i.1), whose lazy
getter-raise is superseded by the blocking init() raise.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import pytest

import quonfig
from quonfig import Quonfig
from quonfig.exceptions import (
    QuonfigInitError,
    QuonfigInitTimeoutError,
    QuonfigKeyNotFoundError,
)

SDK_KEY = "test-backend-key-SECRET-123"


def _envelope_json() -> bytes:
    return json.dumps(
        {
            "configs": [],
            "meta": {"version": "gen-1", "environment": "Production", "generation": 1},
        }
    ).encode()


class _Upstream:
    """Answers every GET with ``status`` (200 serves an empty envelope).
    ``status`` can be switched at runtime to model an outage healing."""

    def __init__(self, status: int, delay: float = 0.0) -> None:
        self.status = status
        self.delay = delay
        self.hits = 0
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if "/api/v2/configs" in self.path:
                    outer.hits += 1
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.status == 200:
                    body = _envelope_json()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                else:
                    body = b"nope"
                    self.send_response(outer.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()


class _Blackhole:
    """Accepts TCP connections (kernel backlog) and never answers: a hang."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}"

    def close(self) -> None:
        self._sock.close()


REFUSED = ["http://127.0.0.1:1", "http://127.0.0.1:2"]


def _client(urls: list, on_init_failure: Optional[str] = None, **kw: Any) -> Quonfig:
    opts: dict = dict(
        sdk_key=SDK_KEY,
        api_urls=urls,
        enable_sse=False,
        fallback_poll_enabled=False,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        init_timeout_ms=500,
    )
    if on_init_failure is not None:
        opts["on_init_failure"] = on_init_failure
    opts.update(kw)
    return Quonfig(**opts)


def _timed_init(client: Quonfig) -> "tuple[Optional[BaseException], float]":
    start = time.monotonic()
    try:
        client.init()
    except Exception as e:  # noqa: BLE001
        return e, time.monotonic() - start
    return None, time.monotonic() - start


@pytest.fixture(autouse=True)
def _reset_alias_warnings() -> None:
    from quonfig import client as client_mod

    getattr(client_mod, "_WARNED_INIT_ALIASES", set()).clear()


# ---------------------------------------------------------------------------
# Error types (B.4)
# ---------------------------------------------------------------------------


def test_init_error_types_are_exported_and_timeout_subclasses_init_error() -> None:
    assert quonfig.QuonfigInitError is QuonfigInitError
    assert issubclass(QuonfigInitTimeoutError, QuonfigInitError)
    assert issubclass(QuonfigInitError, quonfig.QuonfigError)


# ---------------------------------------------------------------------------
# fail (default)
# ---------------------------------------------------------------------------


def test_default_mode_is_fail_and_init_raises_after_retrying_refused_legs() -> None:
    # 650ms: attempt 2 starts by ~500ms and attempt 3 no sooner than 750ms, so
    # the deadline falls in a backoff gap, never just after an attempt starts
    # (an attempt cut before any leg answered is a timeout, Decision 10).
    client = _client(REFUSED, init_timeout_ms=650)
    try:
        err, elapsed = _timed_init(client)
        assert isinstance(err, QuonfigInitError), repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), repr(err)
        assert err.reason == "unavailable"
        # It retried through the window, then raised at the timeout.
        assert 0.4 <= elapsed < 3.0, elapsed
        assert "127.0.0.1" in str(err)
    finally:
        client.close()


def test_fail_raises_at_once_when_every_leg_answers_401() -> None:
    a, b = _Upstream(401), _Upstream(401)
    client = _client([a.url, b.url], on_init_failure="fail", init_timeout_ms=8000)
    try:
        err, elapsed = _timed_init(client)
        assert isinstance(err, QuonfigInitError), repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError)
        assert err.reason == "unauthorized"
        assert elapsed < 3.0, f"waited {elapsed:.2f}s on a rejected key"
    finally:
        client.close()
        a.close()
        b.close()


def test_fail_raises_the_timeout_error_when_every_leg_hangs() -> None:
    a, b = _Blackhole(), _Blackhole()
    client = _client([a.url, b.url], on_init_failure="fail", init_timeout_ms=300)
    try:
        err, elapsed = _timed_init(client)
        assert isinstance(err, QuonfigInitTimeoutError), repr(err)
        assert err.reason == "timeout"
        assert elapsed < 2.0, elapsed
    finally:
        client.close()
        a.close()
        b.close()


def test_getters_on_a_client_kept_after_a_caught_fail_return_defaults_without_blocking() -> None:
    client = _client(REFUSED)
    try:
        with pytest.raises(QuonfigInitError):
            client.init()
        start = time.monotonic()
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
        with pytest.raises(QuonfigKeyNotFoundError):
            client.get_string("some.key")
        details = client.get_string_details("some.key")
        assert details.reason == "ERROR"
        assert details.error_code == "PROVIDER_NOT_READY"
        assert time.monotonic() - start < 0.2
        assert client.is_ready() is False
    finally:
        client.close()


def test_client_kept_after_a_caught_fail_keeps_retrying_and_heals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    a, b = _Upstream(503), _Upstream(503)
    client = _client([a.url, b.url], fallback_poll_enabled=True, fallback_poll_interval_ms=200)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            with pytest.raises(QuonfigInitError):
                client.init()
            assert client.wait_for_ready(timeout_ms=100) is False
            a.status = 200
            b.status = 200
            assert client.wait_for_ready(timeout_ms=8000) is True, "never healed"
        assert client.is_ready()
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
        healed = [r for r in caplog.records if "[quonfig] init recovered" in r.getMessage()]
        assert healed and healed[0].levelno == logging.WARNING
        assert "Now serving live config" in healed[0].getMessage()
    finally:
        client.close()
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# continue / wait
# ---------------------------------------------------------------------------


def test_continue_returns_at_the_timeout_on_code_defaults(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client(REFUSED, on_init_failure="continue")
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            err, elapsed = _timed_init(client)
        assert err is None, repr(err)
        assert 0.4 <= elapsed < 3.0, elapsed
        assert client.is_ready() is False
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
        lines = [r for r in caplog.records if "STARTING ON CODE DEFAULTS" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.ERROR
        assert "on_init_failure=continue" in lines[0].getMessage()
    finally:
        client.close()


def test_continue_returns_at_once_when_every_leg_answers_401() -> None:
    a, b = _Upstream(401), _Upstream(401)
    client = _client([a.url, b.url], on_init_failure="continue", init_timeout_ms=8000)
    try:
        err, elapsed = _timed_init(client)
        assert err is None
        assert elapsed < 3.0
    finally:
        client.close()
        a.close()
        b.close()


def test_wait_blocks_past_the_timeout_until_config_arrives() -> None:
    a, b = _Upstream(503), _Upstream(503)
    client = _client([a.url, b.url], on_init_failure="wait", init_timeout_ms=300)

    def _restore() -> None:
        time.sleep(1.5)
        a.status = 200
        b.status = 200

    threading.Thread(target=_restore, daemon=True).start()
    try:
        # A getter on another thread during the wait answers at once.
        seen: dict = {}

        def _probe() -> None:
            time.sleep(0.5)
            start = time.monotonic()
            seen["value"] = client.get_string("some.key", default="DEFAULT")
            seen["elapsed"] = time.monotonic() - start

        probe = threading.Thread(target=_probe, daemon=True)
        probe.start()
        err, elapsed = _timed_init(client)
        probe.join(2.0)
        assert err is None, repr(err)
        assert elapsed >= 1.4, f"wait returned after {elapsed:.2f}s, before config arrived"
        assert client.is_ready()
        assert seen == {"value": "DEFAULT", "elapsed": pytest.approx(0, abs=0.2)}
    finally:
        client.close()
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Aliases and validation (B.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["raise", ":raise", "RAISE"])
def test_raise_is_a_deprecated_alias_of_fail(spelling: str) -> None:
    client = _client(REFUSED, on_init_failure=spelling)
    try:
        with pytest.raises(QuonfigInitError):
            client.init()
    finally:
        client.close()


@pytest.mark.parametrize("spelling", ["return", "return_zero_value", ":return"])
def test_return_spellings_are_deprecated_aliases_of_continue(spelling: str) -> None:
    client = _client(REFUSED, on_init_failure=spelling)
    try:
        client.init()
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
    finally:
        client.close()


def test_deprecated_alias_warns_once_per_process(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        _client(REFUSED, on_init_failure="raise")
        _client(REFUSED, on_init_failure="raise")
    lines = [r.getMessage() for r in caplog.records if "is deprecated" in r.getMessage()]
    assert lines == ['[quonfig] on_init_failure="raise" is deprecated; use "fail"']


def test_unknown_on_init_failure_is_rejected() -> None:
    with pytest.raises(ValueError, match="on_init_failure"):
        _client(REFUSED, on_init_failure="riase")


# ---------------------------------------------------------------------------
# Readiness API (B.4)
# ---------------------------------------------------------------------------


def test_healthy_boot_is_ready_when_init_returns_and_logs_nothing_above_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    a = _Upstream(200)
    client = _client([a.url, a.url], init_timeout_ms=8000)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            client.init()
        assert client.is_ready() is True
        assert client.wait_for_ready(timeout_ms=0) is True
        init_lines = [r for r in caplog.records if "[quonfig] init" in r.getMessage()]
        assert init_lines, "init start is logged at DEBUG"
        assert all(r.levelno == logging.DEBUG for r in init_lines), [
            (r.levelname, r.getMessage()) for r in init_lines
        ]
        assert init_lines[0].getMessage() == (
            "[quonfig] init: fetching config (init_timeout_ms=8000, on_init_failure=fail)"
        )
    finally:
        client.close()
        a.close()


# ---------------------------------------------------------------------------
# Logging contract (C)
# ---------------------------------------------------------------------------


def test_fail_logs_the_retry_warn_and_the_failed_error_without_the_sdk_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client(REFUSED)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            with pytest.raises(QuonfigInitError):
                client.init()
        msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
        retry = [m for lvl, m in msgs if lvl == logging.WARNING and "config not loaded yet" in m]
        assert retry and retry[0].startswith(
            "[quonfig] init: config not loaded yet, retrying until init_timeout_ms (0.5s). "
            "primary=127.0.0.1:1: "
        ), retry
        failed = [m for lvl, m in msgs if lvl == logging.ERROR and "init FAILED" in m]
        assert len(failed) == 1, msgs
        assert failed[0].startswith("[quonfig] init FAILED: no config after 0.")
        assert "secondary=127.0.0.1:2: " in failed[0]
        assert "on_init_failure=fail: raising QuonfigInitError." in failed[0]
        assert 'on_init_failure="wait"' in failed[0]
        assert all(SDK_KEY not in m for _, m in msgs)
    finally:
        client.close()


def test_401_logs_the_key_rejected_error(caplog: pytest.LogCaptureFixture) -> None:
    a, b = _Upstream(401), _Upstream(401)
    client = _client([a.url, b.url], init_timeout_ms=8000)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            with pytest.raises(QuonfigInitError):
                client.init()
        failed = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert failed == [
            "[quonfig] init FAILED: SDK key rejected (401) by primary and secondary. "
            "Check the key and environment. on_init_failure=fail: raising now."
        ]
    finally:
        client.close()
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Cross-SDK conformance (qfg-9p4i.17, Decisions 6-9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [403, 404, 410])
def test_every_auth_class_status_from_every_leg_fails_at_once(status: int) -> None:
    # Decision 6: the init auth set is 401/403/404/410 (go's isAuthClassStatus).
    a, b = _Upstream(status), _Upstream(status)
    client = _client([a.url, b.url], init_timeout_ms=8000)
    try:
        err, elapsed = _timed_init(client)
        assert isinstance(err, QuonfigInitError), repr(err)
        assert err.reason == "unauthorized"
        assert f"HTTP {status}" in str(err)
        assert elapsed < 3.0, f"waited {elapsed:.2f}s on a rejected key"
    finally:
        client.close()
        a.close()
        b.close()


def test_auth_class_from_the_primary_alone_does_not_decide_unauthorized() -> None:
    a, b = _Upstream(404), _Upstream(503)
    # 200ms: before attempt 2 can start (>= 250ms backoff), so attempt 1, with
    # both legs answered, decides (Decision 10).
    client = _client([a.url, b.url], init_timeout_ms=200)
    try:
        err, _ = _timed_init(client)
        assert isinstance(err, QuonfigInitError), repr(err)
        assert err.reason == "unavailable"
    finally:
        client.close()
        a.close()
        b.close()


def test_unauthorized_line_reports_the_primary_status_not_the_first_to_arrive(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The secondary (403) answers before the slow primary (401).
    a, b = _Upstream(401, delay=0.6), _Upstream(403)
    client = _client([a.url, b.url], init_timeout_ms=8000, hedge_delay_ms=50)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            err, _ = _timed_init(client)
        assert isinstance(err, QuonfigInitError) and err.reason == "unauthorized"
        failed = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert failed and failed[0].startswith(
            "[quonfig] init FAILED: SDK key rejected (401) by primary and secondary."
        ), failed
    finally:
        client.close()
        a.close()
        b.close()


@pytest.mark.parametrize("timeout_ms", [0, -1])
def test_non_positive_init_timeout_runs_one_attempt_before_deciding(timeout_ms: int) -> None:
    # Decision 8: initTimeout <= 0 runs exactly one attempt, then applies the
    # policy. A slow-but-healthy upstream therefore boots.
    a = _Upstream(200, delay=0.3)
    client = _client([a.url], init_timeout_ms=timeout_ms)
    try:
        err, _ = _timed_init(client)
        assert err is None, repr(err)
        assert client.is_ready()
    finally:
        client.close()
        a.close()


def test_zero_init_timeout_fail_raises_after_exactly_one_attempt() -> None:
    a, b = _Upstream(503), _Upstream(503)
    client = _client([a.url, b.url], init_timeout_ms=0, hedge_delay_ms=50)
    try:
        err, _ = _timed_init(client)
        assert isinstance(err, QuonfigInitError), repr(err)
        # The attempt completed and answered 503: unavailable, not timeout.
        assert err.reason == "unavailable"
        assert not isinstance(err, QuonfigInitTimeoutError)
        assert (a.hits, b.hits) == (1, 1)
    finally:
        client.close()
        a.close()
        b.close()


def test_zero_init_timeout_continue_starts_on_defaults_after_one_attempt() -> None:
    a = _Upstream(503)
    client = _client([a.url], init_timeout_ms=0, on_init_failure="continue")
    try:
        err, _ = _timed_init(client)
        assert err is None, repr(err)
        assert a.hits == 1
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
    finally:
        client.close()
        a.close()


def _live_quonfig_threads() -> list:
    return sorted(
        t.name
        for t in threading.enumerate()
        if t.is_alive() and t.name in ("quonfig-sse", "quonfig-init", "quonfig-init-watch")
    )


def test_second_init_after_a_caught_fail_joins_the_retry_and_close_stops_everything() -> None:
    before = _live_quonfig_threads()
    assert before == [], before
    client = _client(REFUSED, enable_sse=True, init_timeout_ms=300)
    try:
        with pytest.raises(QuonfigInitError):
            client.init()
        first = _live_quonfig_threads()
        # The second init() joins the running background retry: it applies the
        # policy again but starts no second SSE stream or reporter.
        with pytest.raises(QuonfigInitError):
            client.init()
        second = _live_quonfig_threads()
        assert second.count("quonfig-sse") == 1, second
        assert second.count("quonfig-init-watch") <= 1, second
        assert len(second) <= len(first), (first, second)
    finally:
        client.close()
    deadline = time.monotonic() + 5.0
    while _live_quonfig_threads() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _live_quonfig_threads() == []


def test_second_init_after_a_healthy_boot_returns_without_refetching() -> None:
    a = _Upstream(200)
    client = _client([a.url], init_timeout_ms=8000)
    try:
        client.init()
        hits = a.hits
        start = time.monotonic()
        client.init()
        assert time.monotonic() - start < 0.2
        assert a.hits == hits
        assert client.is_ready()
    finally:
        client.close()
        a.close()


def test_heal_line_maps_an_unknown_leg_to_primary(caplog: pytest.LogCaptureFixture) -> None:
    from quonfig.types import ConfigEnvelope

    client = _client(REFUSED, on_init_failure="continue", init_timeout_ms=200)
    try:
        client.init()
        assert client._transport is not None
        client._transport.last_fetch_index = -1
        env = ConfigEnvelope.from_wire(json.loads(_envelope_json()))
        with caplog.at_level(logging.WARNING, logger="quonfig"):
            client._install_network_envelope(env, from_http=True, source_index=None)
        healed = [r.getMessage() for r in caplog.records if "init recovered" in r.getMessage()]
        assert healed and " via primary " in healed[0], healed
    finally:
        client.close()


def test_init_log_records_carry_the_event_field(caplog: pytest.LogCaptureFixture) -> None:
    client = _client(REFUSED)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            with pytest.raises(QuonfigInitError):
                client.init()
        events = [getattr(r, "event", None) for r in caplog.records]
        assert "quonfig.init.start" in events
        assert "quonfig.init.retrying" in events
        assert "quonfig.init.failed" in events
        assert all(not hasattr(r, "quonfig_event") for r in caplog.records)
    finally:
        client.close()


def test_init_lines_and_error_never_carry_url_userinfo(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quonfig.transport import LegResult

    client = _client(["http://user:hunter2@127.0.0.1:1"])
    assert client._transport is not None

    def _fake_hedged(*_a: Any, **_k: Any) -> Any:
        yield LegResult(
            source_index=0,
            error=OSError("connect failed for http://user:hunter2@127.0.0.1:1/api/v2/configs"),
        )

    monkeypatch.setattr(client._transport, "fetch_hedged", _fake_hedged)
    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig"):
            err, _ = _timed_init(client)
        assert isinstance(err, QuonfigInitError), repr(err)
        text = str(err) + "\n" + "\n".join(r.getMessage() for r in caplog.records)
        assert "hunter2" not in text, text
        assert "user:" not in text, text
        assert "127.0.0.1:1" in str(err)
    finally:
        client.close()


class _StallBody:
    """Sends a 200 with headers and part of the body, then stalls: a hang
    mid-body, surfaced by urllib3 as its own ReadTimeoutError."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b'{"configs": [')
                self.wfile.flush()
                outer._stop.wait(10)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._stop.set()
        self._server.shutdown()


@pytest.mark.parametrize("hang", ["headers", "body"])
def test_zero_init_timeout_reports_timeout_when_the_single_attempt_hung(hang: str) -> None:
    # Decision 2: reason is "timeout" iff the LAST attempt hung (every leg
    # ended on a timeout/abort). A body that stalls mid-read is a hang too.
    a, b = (_Blackhole(), _Blackhole()) if hang == "headers" else (_StallBody(), _StallBody())
    client = _client(
        [a.url, b.url],
        init_timeout_ms=0,
        hedge_delay_ms=50,
        config_fetch_hedge_abort_ms=300,
    )
    try:
        err, _ = _timed_init(client)
        assert isinstance(err, QuonfigInitTimeoutError), repr(err)
        assert err.reason == "timeout"
    finally:
        client.close()
        a.close()
        b.close()


@pytest.mark.parametrize(
    "text",
    [
        "boom http://user:hunter2@host:1/api",
        "boom https://user:hun@ter2@host:1/api?x=1",
        "boom http://hunter2@host",
    ],
)
def test_redact_userinfo_strips_credentials_including_an_at_in_the_password(text: str) -> None:
    from quonfig.client import _redact_userinfo

    out = _redact_userinfo(text)
    assert "hunter2" not in out and "ter2" not in out, out
    assert "host" in out, out


# ---------------------------------------------------------------------------
# qfg-9p4i.17.10 (Decisions 7 and 10): the reason at the init deadline.
# An attempt in flight at the deadline is the last attempt: timeout iff none of
# its legs has answered. In the backoff gap the last COMPLETED attempt decides:
# timeout iff every one of its legs timed out.
# ---------------------------------------------------------------------------


class _Scripted:
    """Answers the Nth config GET per ``script[N]`` (the last entry repeats):
    an HTTP status, or ``"hang"`` (never answers until closed)."""

    def __init__(self, script: list) -> None:
        self.script = script
        self.hits = 0
        self._stop = threading.Event()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                idx = outer.hits
                outer.hits += 1
                step = outer.script[min(idx, len(outer.script) - 1)]
                if step == "hang":
                    outer._stop.wait(30)
                    return
                body = b"nope"
                self.send_response(int(step))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._stop.set()
        self._server.shutdown()


def _fail_reason(urls: list, **kw: Any) -> "tuple[BaseException, Quonfig]":
    client = _client(urls, on_init_failure="fail", **kw)
    err, _ = _timed_init(client)
    assert isinstance(err, QuonfigInitError), repr(err)
    return err, client


def test_hung_retry_after_a_fast_503_reports_timeout() -> None:
    # Attempt 1 answers 503 on both legs fast; attempt 2 hangs on both legs
    # and is still in flight at the deadline: it is the last attempt, and none
    # of its legs answered.
    a, b = _Scripted([503, "hang"]), _Scripted([503, "hang"])
    try:
        err, client = _fail_reason(
            [a.url, b.url],
            init_timeout_ms=1500,
            hedge_delay_ms=100,
            config_fetch_hedge_abort_ms=5000,
        )
        try:
            assert a.hits >= 2, a.hits
            assert isinstance(err, QuonfigInitTimeoutError), repr(err)
            assert err.reason == "timeout"
            # The errors are the cut attempt's (nothing settled), not attempt
            # 1's 503s: a timeout must not blame a fast failure.
            assert "503" not in err.leg_errors, err.leg_errors
            assert "503" not in str(err), str(err)
        finally:
            client.close()
    finally:
        a.close()
        b.close()


def test_deadline_cutting_attempt_1_after_a_fast_primary_failure_reports_unavailable() -> None:
    # Attempt 1 is cut by the deadline: the primary failed fast (503), the
    # secondary still hangs. A leg answered, so this is not a hang.
    a, b = _Scripted([503]), _Scripted(["hang"])
    try:
        err, client = _fail_reason(
            [a.url, b.url],
            init_timeout_ms=500,
            hedge_delay_ms=100,
            config_fetch_hedge_abort_ms=5000,
        )
        try:
            assert a.hits == 1, a.hits
            assert not isinstance(err, QuonfigInitTimeoutError), repr(err)
            assert err.reason == "unavailable"
        finally:
            client.close()
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("status", [401, 503])
def test_deadline_in_backoff_after_a_partly_answered_attempt_reports_unavailable(
    status: int,
) -> None:
    # Attempt 1 completes: the primary answers fast, the secondary hangs until
    # the 150ms per-leg abort. The deadline (350ms) falls in the backoff gap
    # (>= 250ms after attempt 1), so attempt 1 decides: not every leg timed
    # out. Later attempts repeat the shape, so the result is the same either way.
    a, b = _Scripted([status]), _Scripted(["hang"])
    try:
        err, client = _fail_reason(
            [a.url, b.url],
            init_timeout_ms=350,
            hedge_delay_ms=50,
            config_fetch_hedge_abort_ms=150,
        )
        try:
            assert not isinstance(err, QuonfigInitTimeoutError), repr(err)
            assert err.reason == "unavailable"
        finally:
            client.close()
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("abort_ms", [150, 5000])
def test_every_leg_hung_throughout_reports_timeout(abort_ms: int) -> None:
    # 150ms abort: attempts complete with every leg timed out (deadline in an
    # attempt or in backoff). 5000ms: attempt 1 is cut with both legs pending.
    a, b = _Scripted(["hang"]), _Scripted(["hang"])
    try:
        err, client = _fail_reason(
            [a.url, b.url],
            init_timeout_ms=700,
            hedge_delay_ms=50,
            config_fetch_hedge_abort_ms=abort_ms,
        )
        try:
            assert isinstance(err, QuonfigInitTimeoutError), repr(err)
            assert err.reason == "timeout"
        finally:
            client.close()
    finally:
        a.close()
        b.close()
