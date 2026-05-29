# AUTO-GENERATED from integration-test-data/tests/eval/delivery_environment.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from quonfig import Quonfig


def start_delivery_server(
    envelope_json: str,
) -> "tuple[ThreadingHTTPServer, int]":
    body = envelope_json.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/v2/configs"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("ETag", '"v1"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:  # silence test logs
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


# singular environment override wins over default when env not pinned
def test_singular_environment_override_wins_over_default_when_env_not_pinned() -> None:
    envelope_json = '{"meta":{"version":"v1","environment":"development"},"configs":[{"id":"c-env","key":"flag.env-scoped","type":"bool","valueType":"bool","sendToClientSdk":false,"default":{"rules":[{"criteria":[{"operator":"ALWAYS_TRUE"}],"value":{"type":"bool","value":true}}]},"environment":{"id":"development","rules":[{"criteria":[{"operator":"ALWAYS_TRUE"}],"value":{"type":"bool","value":false}}]}}]}'
    server, port = start_delivery_server(envelope_json)
    try:
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=[f"http://127.0.0.1:{port}"],
            fallback_poll_enabled=False,
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            init_timeout_ms=5000,
            on_init_failure="raise",
        )
        try:
            client.init()
            assert client.get_bool("flag.env-scoped", default=None) is False, (
                "delivery-wire env override: expected False for flag.env-scoped"
            )
        finally:
            client.close()
    finally:
        server.shutdown()


# explicit environment pin is IGNORED in delivery mode (meta.environment authoritative)
def test_explicit_environment_pin_ignored_in_delivery_mode() -> None:
    # qfg-pinh (Jeff 2026-05-29, Option A): in SDK-key delivery mode
    # meta.environment is authoritative; an explicit environment pin is
    # datadir-only and IGNORED here. meta.environment='staging' has no matching
    # env block (config block is 'development'), so eval falls to default=true.
    # The 'development' pin must NOT pull in the env block.
    envelope_json = '{"meta":{"version":"v1","environment":"staging"},"configs":[{"id":"c-env","key":"flag.env-scoped","type":"bool","valueType":"bool","sendToClientSdk":false,"default":{"rules":[{"criteria":[{"operator":"ALWAYS_TRUE"}],"value":{"type":"bool","value":true}}]},"environment":{"id":"development","rules":[{"criteria":[{"operator":"ALWAYS_TRUE"}],"value":{"type":"bool","value":false}}]}}]}'
    server, port = start_delivery_server(envelope_json)
    try:
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=[f"http://127.0.0.1:{port}"],
            fallback_poll_enabled=False,
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            init_timeout_ms=5000,
            on_init_failure="raise",
            environment="development",
        )
        try:
            client.init()
            assert client.get_bool("flag.env-scoped", default=None) is True, (
                "delivery-wire: pin ignored, meta.environment='staging' has no "
                "env block, so expected default True for flag.env-scoped"
            )
        finally:
            client.close()
    finally:
        server.shutdown()


# config without environment block falls back to default in delivery mode
def test_config_without_environment_block_falls_back_to_default_in_delivery_mode() -> None:
    envelope_json = '{"meta":{"version":"v1","environment":"development"},"configs":[{"id":"c-def","key":"flag.default-only","type":"bool","valueType":"bool","sendToClientSdk":false,"default":{"rules":[{"criteria":[{"operator":"ALWAYS_TRUE"}],"value":{"type":"bool","value":true}}]}}]}'
    server, port = start_delivery_server(envelope_json)
    try:
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=[f"http://127.0.0.1:{port}"],
            fallback_poll_enabled=False,
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            init_timeout_ms=5000,
            on_init_failure="raise",
        )
        try:
            client.init()
            assert client.get_bool("flag.default-only", default=None) is True, (
                "delivery-wire env override: expected True for flag.default-only"
            )
        finally:
            client.close()
    finally:
        server.shutdown()
