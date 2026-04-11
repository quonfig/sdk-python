"""Tests for ConfigStore: thread-safety, get/update, concurrent reads."""
from __future__ import annotations

import threading
from typing import List

import pytest

from quonfig.store import ConfigStore
from quonfig.types import ConfigEnvelope, ConfigResponse, Meta, RuleSet


def make_config(key: str) -> ConfigResponse:
    return ConfigResponse(
        id=f"id-{key}",
        key=key,
        type="config",
        value_type="string",
        send_to_client_sdk=True,
        default=RuleSet(rules=[]),
    )


def make_envelope(keys: List[str], version: str = "v1") -> ConfigEnvelope:
    configs = [make_config(k) for k in keys]
    return ConfigEnvelope(
        configs=configs,
        meta=Meta(version=version, environment="test"),
    )


class TestConfigStoreBasic:
    def test_empty_store_get_returns_none(self):
        store = ConfigStore()
        assert store.get("any.key") is None

    def test_update_then_get(self):
        store = ConfigStore()
        store.update(make_envelope(["key.one", "key.two"]))
        assert store.get("key.one") is not None
        assert store.get("key.two") is not None

    def test_get_unknown_key_returns_none(self):
        store = ConfigStore()
        store.update(make_envelope(["key.one"]))
        assert store.get("key.unknown") is None

    def test_keys_returns_all(self):
        store = ConfigStore()
        store.update(make_envelope(["a", "b", "c"]))
        keys = store.keys()
        assert set(keys) == {"a", "b", "c"}

    def test_update_replaces_all(self):
        store = ConfigStore()
        store.update(make_envelope(["old.key"]))
        store.update(make_envelope(["new.key"]))
        # Old key gone
        assert store.get("old.key") is None
        # New key present
        assert store.get("new.key") is not None

    def test_etag_updated(self):
        store = ConfigStore()
        assert store.get_etag() is None
        store.update(make_envelope(["a"], version="v42"))
        assert store.get_etag() == "v42"

    def test_etag_changes_on_update(self):
        store = ConfigStore()
        store.update(make_envelope(["a"], version="v1"))
        store.update(make_envelope(["a"], version="v2"))
        assert store.get_etag() == "v2"


class TestConfigStoreConcurrency:
    def test_concurrent_reads_are_safe(self):
        """10 threads reading simultaneously should not error."""
        store = ConfigStore()
        store.update(make_envelope([f"key.{i}" for i in range(100)]))

        errors = []

        def read_loop():
            try:
                for i in range(50):
                    store.get(f"key.{i % 100}")
                    store.keys()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_loop) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent read errors: {errors}"

    def test_concurrent_reads_and_writes(self):
        """Multiple readers + one writer should not error or corrupt state."""
        store = ConfigStore()
        store.update(make_envelope(["initial.key"]))

        errors = []
        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                store.update(make_envelope([f"key.{i % 10}"], version=f"v{i}"))
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    keys = store.keys()
                    for k in keys:
                        store.get(k)
                except Exception as e:
                    errors.append(e)

        readers = [threading.Thread(target=reader) for _ in range(5)]
        writer_t = threading.Thread(target=writer)

        for t in readers:
            t.start()
        writer_t.start()

        # Let them run for a brief moment
        stop.wait(timeout=0.2)
        stop.set()

        writer_t.join(timeout=1.0)
        for t in readers:
            t.join(timeout=1.0)

        assert errors == [], f"Concurrent read/write errors: {errors}"
