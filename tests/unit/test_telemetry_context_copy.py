"""Example-context telemetry copies caller context values at record time
(qfg-goi1.2.36 item A).

The collector used to keep the caller's own dicts and serialize them at flush
time on the telemetry thread, so (1) a caller mutation after the call changed
the recorded example and (2) one non-JSON value (datetime, UUID, Decimal, NaN)
made ``json.dumps`` fail and dropped the whole telemetry window. Reference:
sdk-go ``telemetryContextValue`` (qfg-goi1.2.44, qfg-goi1.2.46).
"""

from __future__ import annotations

import datetime
import decimal
import json
import uuid
from typing import Any, Dict

from quonfig.telemetry.collectors import ExampleContextCollector


def _drain(collector: ExampleContextCollector) -> Dict[str, Any]:
    event = collector.drain()
    assert event is not None and event.example_contexts is not None
    first = event.example_contexts.examples[0]
    return {c["type"]: c["values"] for c in first.context_set["contexts"]}


def test_nested_values_are_copied_at_record_time() -> None:
    collector = ExampleContextCollector()
    nested = {"plan": "pro", "tags": ["a", "b"]}
    collector.record({"user": {"key": "u1", "meta": nested}})
    nested["plan"] = "free"
    nested["tags"].append("c")
    assert _drain(collector)["user"]["meta"] == {"plan": "pro", "tags": ["a", "b"]}


def test_uuid_key_and_list_value_reach_the_example() -> None:
    collector = ExampleContextCollector()
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    collector.record({"user": {"key": uid, "roles": ["admin", "dev"]}})
    user = _drain(collector)["user"]
    assert user == {"key": str(uid), "roles": ["admin", "dev"]}


def test_non_json_scalars_render_to_strings_and_window_serializes() -> None:
    collector = ExampleContextCollector()
    collector.record(
        {
            "user": {
                "key": "u1",
                "signed_up": datetime.datetime(2026, 10, 12, 9, 30, 0),
                "birthday": datetime.date(2000, 1, 2),
                "balance": decimal.Decimal("10.50"),
                "score": float("nan"),
                "ratio": float("inf"),
                "obj": object(),
            }
        }
    )
    event = collector.drain()
    assert event is not None
    # The whole window must serialize the way the reporter does it.
    body = json.dumps(event.to_dict(), allow_nan=False)
    user = json.loads(body)["exampleContexts"]["examples"][0]["contextSet"]["contexts"][0]
    assert user["values"] == {
        "key": "u1",
        "signed_up": "2026-10-12T09:30:00",
        "birthday": "2000-01-02",
        "balance": "10.50",
    }


def test_self_referencing_value_does_not_recurse_forever() -> None:
    collector = ExampleContextCollector()
    loop: Dict[str, Any] = {"name": "x"}
    loop["self"] = loop
    shared = ["s"]
    collector.record({"user": {"key": "u1", "loop": loop, "a": shared, "b": shared}})
    user = _drain(collector)["user"]
    assert user["loop"] == {"name": "x"}
    # Reached twice without a cycle: copied both times.
    assert user["a"] == ["s"] and user["b"] == ["s"]
