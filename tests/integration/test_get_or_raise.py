# AUTO-GENERATED from integration-test-data/tests/eval/get_or_raise.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import pytest

from quonfig import Quonfig
from quonfig.exceptions import (
    QuonfigDecryptionError,
    QuonfigEnvVarNotSetError,
    QuonfigInitTimeoutError,
    QuonfigKeyNotFoundError,
    QuonfigTypeMismatchError,
)

DATADIR = os.path.join(
    os.path.dirname(__file__),
    "../../../integration-test-data/data/integration-tests",
)


def _init_failure_client(
    on_init_failure: "str | None",
    api_urls: "list[str]",
    api_statuses: "list[int]",
    api_body: "str | None",
    init_timeout_ms: int,
) -> "tuple[Quonfig, BaseException | None, float, Callable[[], None]]":
    servers: "list[ThreadingHTTPServer]" = []
    if api_statuses:
        payload = (api_body or "").encode("utf-8")

        def _status_handler(status: int) -> "type[BaseHTTPRequestHandler]":
            class _StatusHandler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                    return

            return _StatusHandler

        fixed_urls = api_urls
        api_urls = []
        for i, status in enumerate(api_statuses):
            if status == 0:
                api_urls.append(fixed_urls[i])
                continue
            server = ThreadingHTTPServer(("127.0.0.1", 0), _status_handler(status))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
            api_urls.append(f"http://127.0.0.1:{server.server_address[1]}")
    kwargs: "dict[str, Any]" = {
        "sdk_key": "itd-init-failure",
        "api_urls": api_urls,
        "init_timeout_ms": init_timeout_ms,
        "enable_sse": False,
        "fallback_poll_enabled": False,
        "collect_evaluation_summaries": False,
        "context_upload_mode": "none",
        "on_no_default": "error",
    }
    if on_init_failure is not None:
        kwargs["on_init_failure"] = on_init_failure
    client = Quonfig(**kwargs)
    start = time.monotonic()
    err: "BaseException | None" = None
    try:
        client.init()
    except Exception as e:  # noqa: BLE001 - the case asserts the class
        err = e
    elapsed_ms = (time.monotonic() - start) * 1000

    def close() -> None:
        client.close()
        for s in servers:
            s.shutdown()

    return client, err, elapsed_ms, close


# get_or_raise can raise an error if value not found
def test_get_or_raise_can_raise_an_error_if_value_not_found() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_string("my-missing-key")


# get_or_raise returns a default value instead of raising
def test_get_or_raise_returns_a_default_value_instead_of_raising() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    result = c.get_string("my-missing-key", default="DEFAULT")
    assert result == "DEFAULT"


# init fails by default when every delivery leg refuses the connection
def test_init_fails_by_default_when_every_delivery_leg_refuses_the_connection() -> None:
    c, err, elapsed_ms, close = _init_failure_client(
        None, ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 0
    )
    try:
        assert err is not None, "init() must raise (on_init_failure default = fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 8000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init with on_init_failure fail raises the init timeout when every delivery leg hangs
def test_init_with_on_init_failure_fail_raises_the_init_timeout_when_every_delivery_leg_hangs() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "fail", ["http://192.0.2.1:8001", "http://192.0.2.1:8002"], [], None, 200
    )
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert isinstance(err, QuonfigInitTimeoutError), repr(err)
        assert getattr(err, "reason", None) == "timeout", f"reason {getattr(err, 'reason', None)!r}"
        assert elapsed_ms < 2000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init with on_init_failure fail raises at once when every delivery leg answers 401
def test_init_with_on_init_failure_fail_raises_at_once_when_every_delivery_leg_answers_401() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client("fail", [], [401, 401], None, 8000)
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unauthorized, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unauthorized", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 4000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# get_or_raise returns the default after init continues past refused delivery legs
def test_get_or_raise_returns_the_default_after_init_continues_past_refused_delivery_legs() -> None:
    c, err, elapsed_ms, close = _init_failure_client(
        "continue", ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 500
    )
    try:
        assert err is None, f"on_init_failure continue: init() must return, raised {err!r}"
        result = c.get_string("any-key", default="DEFAULT")
        assert result == "DEFAULT"
    finally:
        close()


# get_or_raise raises missing_default after init continues past refused delivery legs
def test_get_or_raise_raises_missing_default_after_init_continues_past_refused_delivery_legs() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "continue", ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 500
    )
    try:
        assert err is None, f"on_init_failure continue: init() must return, raised {err!r}"
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_string("any-key")
    finally:
        close()


# deprecated on_init_failure raise behaves as fail when every delivery leg refuses the connection
def test_deprecated_on_init_failure_raise_behaves_as_fail_when_every_delivery_leg_refuses_the_connection() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "raise", ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 0
    )
    try:
        assert err is not None, "init() must raise (on_init_failure raise)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 8000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# deprecated on_init_failure return behaves as continue when every delivery leg refuses the connection
def test_deprecated_on_init_failure_return_behaves_as_continue_when_every_delivery_leg_refuses_the_connection() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "return", ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 500
    )
    try:
        assert err is None, f"on_init_failure continue: init() must return, raised {err!r}"
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_string("any-key")
    finally:
        close()


# init with on_init_failure fail raises at once when every delivery leg answers 404
def test_init_with_on_init_failure_fail_raises_at_once_when_every_delivery_leg_answers_404() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client("fail", [], [404, 404], None, 8000)
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unauthorized, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unauthorized", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 4000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init with on_init_failure fail raises at once when every delivery leg answers 410
def test_init_with_on_init_failure_fail_raises_at_once_when_every_delivery_leg_answers_410() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client("fail", [], [410, 410], None, 8000)
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unauthorized, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unauthorized", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 4000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init with on_init_failure fail raises unavailable after one attempt when only the primary answers 401 and the secondary 503
def test_init_with_on_init_failure_fail_raises_unavailable_after_one_attempt_when_only_the_primary_answers_401_and_the_secondary_503() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client("fail", [], [401, 503], None, 0)
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 5000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init with on_init_failure fail raises unavailable at the deadline when the primary answers 401 and the secondary hangs
def test_init_with_on_init_failure_fail_raises_unavailable_at_the_deadline_when_the_primary_answers_401_and_the_secondary_hangs() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "fail", ["", "http://192.0.2.1:8002"], [401, 0], None, 9000
    )
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 12000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init with init_timeout_ms 0 and on_init_failure fail raises after one attempt when every delivery leg refuses the connection
def test_init_with_init_timeout_ms_0_and_on_init_failure_fail_raises_after_one_attempt_when_every_delivery_leg_refuses_the_connection() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "fail", ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 0
    )
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 8000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# get_or_raise returns the default after init with init_timeout_ms 0 continues past refused delivery legs
def test_get_or_raise_returns_the_default_after_init_with_init_timeout_ms_0_continues_past_refused_delivery_legs() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "continue", ["http://127.0.0.1:1", "http://127.0.0.1:2"], [], None, 0
    )
    try:
        assert err is None, f"on_init_failure continue: init() must return, raised {err!r}"
        result = c.get_string("any-key", default="DEFAULT")
        assert result == "DEFAULT"
    finally:
        close()


# init with on_init_failure fail raises the init timeout when every delivery leg hangs past its per-leg abort
def test_init_with_on_init_failure_fail_raises_the_init_timeout_when_every_delivery_leg_hangs_past_its_per_leg_abort() -> (
    None
):
    c, err, elapsed_ms, close = _init_failure_client(
        "fail", ["http://192.0.2.1:8001", "http://192.0.2.1:8002"], [], None, 10000
    )
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert isinstance(err, QuonfigInitTimeoutError), repr(err)
        assert getattr(err, "reason", None) == "timeout", f"reason {getattr(err, 'reason', None)!r}"
        assert elapsed_ms < 14000, f"init() raised after {elapsed_ms:.0f}ms"
    finally:
        close()


# init error never carries the userinfo of a delivery URL
def test_init_error_never_carries_the_userinfo_of_a_delivery_url() -> None:
    c, err, elapsed_ms, close = _init_failure_client(
        "fail",
        ["http://qfguser:qfgsecret@127.0.0.1:1", "http://qfguser:qfgsecret@127.0.0.1:2"],
        [],
        None,
        0,
    )
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 8000, f"init() raised after {elapsed_ms:.0f}ms"
        chain: "list[BaseException]" = []
        e: "BaseException | None" = err
        while e is not None and e not in chain:
            chain.append(e)
            e = e.__cause__ or e.__context__
        for link in chain:
            assert "qfgsecret" not in str(link), repr(link)
            assert "qfguser" not in str(link), repr(link)
    finally:
        close()


# init error never carries an upstream response body
def test_init_error_never_carries_an_upstream_response_body() -> None:
    c, err, elapsed_ms, close = _init_failure_client(
        "fail", [], [503, 503], "upstream exploded: qfg-upstream-body-marker", 0
    )
    try:
        assert err is not None, "init() must raise (on_init_failure fail)"
        assert "QuonfigInitError" in [k.__name__ for k in type(err).__mro__], repr(err)
        assert not isinstance(err, QuonfigInitTimeoutError), (
            "reason unavailable, not the init timeout"
        )
        assert getattr(err, "reason", None) == "unavailable", (
            f"reason {getattr(err, 'reason', None)!r}"
        )
        assert elapsed_ms < 5000, f"init() raised after {elapsed_ms:.0f}ms"
        chain: "list[BaseException]" = []
        e: "BaseException | None" = err
        while e is not None and e not in chain:
            chain.append(e)
            e = e.__cause__ or e.__context__
        for link in chain:
            assert "qfg-upstream-body-marker" not in str(link), repr(link)
    finally:
        close()


# raises an error if a config is provided by a missing environment variable
def test_raises_an_error_if_a_config_is_provided_by_a_missing_environment_variable() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigEnvVarNotSetError):
        c.get_string("provided.by.missing.env.var")


# raises an error if an env-var-provided config cannot be coerced to configured type
def test_raises_an_error_if_an_env_var_provided_config_cannot_be_coerced_to_configured_type() -> (
    None
):
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_int("provided.not.a.number")


# coercion error for an int env var names the variable and never its value
def test_coercion_error_for_an_int_env_var_names_the_variable_and_never_its_value() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_SECRET_ENV"] = os.environ.get("QUONFIG_ITD_SECRET_ENV")
    os.environ["QUONFIG_ITD_SECRET_ENV"] = "postgres://admin:hunter2@db"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError) as excinfo:
            c.get_int("provided.secret.not.an.int")
        message = str(excinfo.value)
        assert (
            'environment variable "QUONFIG_ITD_SECRET_ENV" cannot be converted to int for config "provided.secret.not.an.int"'
            in message
        )
        assert "hunter2" not in message
        assert "postgres://" not in message
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# coercion error for a double env var names the variable and never its value
def test_coercion_error_for_a_double_env_var_names_the_variable_and_never_its_value() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_SECRET_ENV"] = os.environ.get("QUONFIG_ITD_SECRET_ENV")
    os.environ["QUONFIG_ITD_SECRET_ENV"] = "postgres://admin:hunter2@db"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError) as excinfo:
            c.get_float("provided.secret.not.a.double")
        message = str(excinfo.value)
        assert (
            'environment variable "QUONFIG_ITD_SECRET_ENV" cannot be converted to double for config "provided.secret.not.a.double"'
            in message
        )
        assert "hunter2" not in message
        assert "postgres://" not in message
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# raises an error for decryption failure
def test_raises_an_error_for_decryption_failure() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigDecryptionError):
        c.get_string("a.broken.secret.config")


# raises an error if an env-var-provided duration 30s cannot be coerced
def test_raises_an_error_if_an_env_var_provided_duration_30s_cannot_be_coerced() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_30S"] = os.environ.get("QUONFIG_ITD_DURATION_30S")
    os.environ["QUONFIG_ITD_DURATION_30S"] = "30s"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_duration("provided.duration.malformed.30s")
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# raises an error if an env-var-provided duration PT0.5H cannot be coerced
def test_raises_an_error_if_an_env_var_provided_duration_pt0_5h_cannot_be_coerced() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_PT0_5H"] = os.environ.get("QUONFIG_ITD_DURATION_PT0_5H")
    os.environ["QUONFIG_ITD_DURATION_PT0_5H"] = "PT0.5H"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_duration("provided.duration.malformed.PT0.5H")
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# raises an error if an env-var-provided duration P1DT cannot be coerced
def test_raises_an_error_if_an_env_var_provided_duration_p1dt_cannot_be_coerced() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_P1DT"] = os.environ.get("QUONFIG_ITD_DURATION_P1DT")
    os.environ["QUONFIG_ITD_DURATION_P1DT"] = "P1DT"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_duration("provided.duration.malformed.P1DT")
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# raises an error if an env-var-provided duration garbage cannot be coerced
def test_raises_an_error_if_an_env_var_provided_duration_garbage_cannot_be_coerced() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_GARBAGE"] = os.environ.get("QUONFIG_ITD_DURATION_GARBAGE")
    os.environ["QUONFIG_ITD_DURATION_GARBAGE"] = "garbage"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_duration("provided.duration.malformed.garbage")
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# raises an error if a stored duration 30s cannot be coerced
def test_raises_an_error_if_a_stored_duration_30s_cannot_be_coerced() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_duration("test.duration.malformed.30s")


# raises an error if a stored duration PT0.5H cannot be coerced
def test_raises_an_error_if_a_stored_duration_pt0_5h_cannot_be_coerced() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_duration("test.duration.malformed.PT0.5H")


# raises an error if a stored duration P1DT cannot be coerced
def test_raises_an_error_if_a_stored_duration_p1dt_cannot_be_coerced() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_duration("test.duration.malformed.P1DT")


# raises an error if a stored duration garbage cannot be coerced
def test_raises_an_error_if_a_stored_duration_garbage_cannot_be_coerced() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_duration("test.duration.malformed.garbage")


# raises an error if a stored duration empty cannot be coerced
def test_raises_an_error_if_a_stored_duration_empty_cannot_be_coerced() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_duration("test.duration.malformed.empty")


# STRING get_or_raise on stored int raises type_mismatch
def test_string_get_or_raise_on_stored_int_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string("getter.contract.int")


# STRING get_or_raise on stored double raises type_mismatch
def test_string_get_or_raise_on_stored_double_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string("getter.contract.double")


# STRING get_or_raise on stored bool raises type_mismatch
def test_string_get_or_raise_on_stored_bool_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string("getter.contract.bool")


# STRING get_or_raise on stored string_list raises type_mismatch
def test_string_get_or_raise_on_stored_string_list_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string("getter.contract.string_list")


# STRING get_or_raise on stored json raises type_mismatch
def test_string_get_or_raise_on_stored_json_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string("getter.contract.json")


# STRING get_or_raise on stored duration raises type_mismatch
def test_string_get_or_raise_on_stored_duration_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string("getter.contract.duration")


# INT get_or_raise on stored string raises type_mismatch
def test_int_get_or_raise_on_stored_string_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.string")


# INT get_or_raise on stored double 3.9 raises type_mismatch
def test_int_get_or_raise_on_stored_double_3_9_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.double")


# INT get_or_raise on stored bool raises type_mismatch
def test_int_get_or_raise_on_stored_bool_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.bool")


# INT get_or_raise on stored string_list raises type_mismatch
def test_int_get_or_raise_on_stored_string_list_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.string_list")


# INT get_or_raise on stored json raises type_mismatch
def test_int_get_or_raise_on_stored_json_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.json")


# INT get_or_raise on stored duration raises type_mismatch
def test_int_get_or_raise_on_stored_duration_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.duration")


# DOUBLE get_or_raise on stored string raises type_mismatch
def test_double_get_or_raise_on_stored_string_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_float("getter.contract.string")


# DOUBLE get_or_raise on stored bool raises type_mismatch
def test_double_get_or_raise_on_stored_bool_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_float("getter.contract.bool")


# DOUBLE get_or_raise on stored string_list raises type_mismatch
def test_double_get_or_raise_on_stored_string_list_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_float("getter.contract.string_list")


# DOUBLE get_or_raise on stored json raises type_mismatch
def test_double_get_or_raise_on_stored_json_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_float("getter.contract.json")


# DOUBLE get_or_raise on stored duration raises type_mismatch
def test_double_get_or_raise_on_stored_duration_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_float("getter.contract.duration")


# BOOLEAN get_or_raise on stored string raises type_mismatch
def test_boolean_get_or_raise_on_stored_string_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.string")


# BOOLEAN get_or_raise on stored int raises type_mismatch
def test_boolean_get_or_raise_on_stored_int_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.int")


# BOOLEAN get_or_raise on stored double raises type_mismatch
def test_boolean_get_or_raise_on_stored_double_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.double")


# BOOLEAN get_or_raise on stored string_list raises type_mismatch
def test_boolean_get_or_raise_on_stored_string_list_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.string_list")


# BOOLEAN get_or_raise on stored json raises type_mismatch
def test_boolean_get_or_raise_on_stored_json_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.json")


# BOOLEAN get_or_raise on stored duration raises type_mismatch
def test_boolean_get_or_raise_on_stored_duration_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.duration")


# STRING_LIST get_or_raise on stored string raises type_mismatch
def test_string_list_get_or_raise_on_stored_string_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string_list("getter.contract.string")


# STRING_LIST get_or_raise on stored int raises type_mismatch
def test_string_list_get_or_raise_on_stored_int_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string_list("getter.contract.int")


# STRING_LIST get_or_raise on stored double raises type_mismatch
def test_string_list_get_or_raise_on_stored_double_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string_list("getter.contract.double")


# STRING_LIST get_or_raise on stored bool raises type_mismatch
def test_string_list_get_or_raise_on_stored_bool_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string_list("getter.contract.bool")


# STRING_LIST get_or_raise on stored json raises type_mismatch
def test_string_list_get_or_raise_on_stored_json_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string_list("getter.contract.json")


# STRING_LIST get_or_raise on stored duration raises type_mismatch
def test_string_list_get_or_raise_on_stored_duration_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_string_list("getter.contract.duration")


# JSON get_or_raise on stored string raises type_mismatch
def test_json_get_or_raise_on_stored_string_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_json("getter.contract.string")


# JSON get_or_raise on stored int raises type_mismatch
def test_json_get_or_raise_on_stored_int_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_json("getter.contract.int")


# JSON get_or_raise on stored double raises type_mismatch
def test_json_get_or_raise_on_stored_double_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_json("getter.contract.double")


# JSON get_or_raise on stored bool raises type_mismatch
def test_json_get_or_raise_on_stored_bool_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_json("getter.contract.bool")


# JSON get_or_raise on stored string_list raises type_mismatch
def test_json_get_or_raise_on_stored_string_list_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_json("getter.contract.string_list")


# JSON get_or_raise on stored duration raises type_mismatch
def test_json_get_or_raise_on_stored_duration_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_json("getter.contract.duration")


# DURATION get_or_raise on stored string raises type_mismatch
def test_duration_get_or_raise_on_stored_string_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.string")


# DURATION get_or_raise on stored int raises type_mismatch
def test_duration_get_or_raise_on_stored_int_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.int")


# DURATION get_or_raise on stored double raises type_mismatch
def test_duration_get_or_raise_on_stored_double_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.double")


# DURATION get_or_raise on stored bool raises type_mismatch
def test_duration_get_or_raise_on_stored_bool_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.bool")


# DURATION get_or_raise on stored string_list raises type_mismatch
def test_duration_get_or_raise_on_stored_string_list_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.string_list")


# DURATION get_or_raise on stored json raises type_mismatch
def test_duration_get_or_raise_on_stored_json_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.json")


# BOOLEAN get_or_raise on stored string "false" raises type_mismatch
def test_boolean_get_or_raise_on_stored_string_false_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.string.false")


# BOOLEAN get_or_raise on stored string "true" raises type_mismatch
def test_boolean_get_or_raise_on_stored_string_true_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_bool("getter.contract.string.true")


# INT get_or_raise on stored string "3" raises type_mismatch
def test_int_get_or_raise_on_stored_string_3_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.string.number")


# DOUBLE get_or_raise on stored string "3" raises type_mismatch
def test_double_get_or_raise_on_stored_string_3_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_float("getter.contract.string.number")


# DURATION get_or_raise on stored string "PT1S" raises type_mismatch
def test_duration_get_or_raise_on_stored_string_pt1s_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_duration("getter.contract.string.duration")


# DOUBLE get_or_raise on stored int 30 returns 30.0
def test_double_get_or_raise_on_stored_int_30_returns_30_0() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    result = c.get_float("getter.contract.int")
    assert result == 30


# INT get_or_raise on stored whole double 3.0 returns 3
def test_int_get_or_raise_on_stored_whole_double_3_0_returns_3() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    result = c.get_int("getter.contract.double.whole")
    assert result == 3


# INT get_or_raise on stored double 1e300 raises type_mismatch
def test_int_get_or_raise_on_stored_double_1e300_raises_type_mismatch() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.double.huge")


# INT get_or_raise on stored double 2^53 (outside the safe int range) raises type_mismatch
def test_int_get_or_raise_on_stored_double_2_53_outside_the_safe_int_range_raises_type_mismatch() -> (
    None
):
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigTypeMismatchError):
        c.get_int("getter.contract.double.unsafe")


# INT get_or_raise on stored malformed int "twelve" raises unable_to_coerce_env_var
def test_int_get_or_raise_on_stored_malformed_int_twelve_raises_unable_to_coerce_env_var() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_int("getter.contract.int.malformed")


# DOUBLE get_or_raise on stored malformed double "abc" raises unable_to_coerce_env_var
def test_double_get_or_raise_on_stored_malformed_double_abc_raises_unable_to_coerce_env_var() -> (
    None
):
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_float("getter.contract.double.malformed")


# INT get_or_raise on env-var-provided malformed int "twelve" raises unable_to_coerce_env_var
def test_int_get_or_raise_on_env_var_provided_malformed_int_twelve_raises_unable_to_coerce_env_var() -> (
    None
):
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_INT"] = os.environ.get("QUONFIG_ITD_GETTER_INT")
    os.environ["QUONFIG_ITD_GETTER_INT"] = "twelve"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_int("getter.contract.provided.int.malformed")
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# DOUBLE get_or_raise on env-var-provided malformed double "abc" raises unable_to_coerce_env_var
def test_double_get_or_raise_on_env_var_provided_malformed_double_abc_raises_unable_to_coerce_env_var() -> (
    None
):
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_DOUBLE"] = os.environ.get("QUONFIG_ITD_GETTER_DOUBLE")
    os.environ["QUONFIG_ITD_GETTER_DOUBLE"] = "abc"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
        c.init()
        with pytest.raises(QuonfigKeyNotFoundError):
            c.get_float("getter.contract.provided.double.malformed")
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# BOOLEAN get_or_raise on a missing bool env var raises missing_env_var
def test_boolean_get_or_raise_on_a_missing_bool_env_var_raises_missing_env_var() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigEnvVarNotSetError):
        c.get_bool("getter.contract.provided.bool.missing")
