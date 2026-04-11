"""Tests for context handling: dotted-path lookup, merging, magic props, thread-local."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from quonfig.context import (
    clear_thread_context,
    get_context_value,
    get_thread_context,
    merge_contexts,
    set_thread_context,
)


class TestGetContextValue:
    def test_simple_dotted_path(self):
        ctx = {"user": {"email": "alice@example.com", "plan": "pro"}}
        value, found = get_context_value(ctx, "user.email")
        assert found is True
        assert value == "alice@example.com"

    def test_nested_key_missing(self):
        ctx = {"user": {"email": "alice@example.com"}}
        value, found = get_context_value(ctx, "user.nonexistent")
        assert found is False
        assert value is None

    def test_namespace_missing(self):
        ctx = {"user": {"email": "alice@example.com"}}
        value, found = get_context_value(ctx, "org.name")
        assert found is False
        assert value is None

    def test_empty_property_name(self):
        ctx = {"user": {"email": "alice@example.com"}}
        value, found = get_context_value(ctx, "")
        assert found is False
        assert value is None

    def test_no_dot_looks_in_empty_namespace(self):
        ctx = {"": {"name": "global-value"}}
        value, found = get_context_value(ctx, "name")
        assert found is True
        assert value == "global-value"

    def test_no_dot_no_empty_namespace(self):
        ctx = {"user": {"email": "alice@example.com"}}
        value, found = get_context_value(ctx, "name")
        assert found is False

    def test_magic_prefab_current_time(self):
        """prefab.current-time returns current time in ms."""
        fake_time = 1700000000.0
        with patch("quonfig.context.time") as mock_time:
            mock_time.time.return_value = fake_time
            value, found = get_context_value({}, "prefab.current-time")
        assert found is True
        assert value == int(fake_time * 1000)

    def test_magic_quonfig_current_time(self):
        fake_time = 1700000001.0
        with patch("quonfig.context.time") as mock_time:
            mock_time.time.return_value = fake_time
            value, found = get_context_value({}, "quonfig.current-time")
        assert found is True
        assert value == int(fake_time * 1000)

    def test_magic_reforge_current_time(self):
        fake_time = 1700000002.0
        with patch("quonfig.context.time") as mock_time:
            mock_time.time.return_value = fake_time
            value, found = get_context_value({}, "reforge.current-time")
        assert found is True
        assert value == int(fake_time * 1000)

    def test_value_can_be_none(self):
        ctx = {"user": {"active": None}}
        value, found = get_context_value(ctx, "user.active")
        assert found is True
        assert value is None

    def test_integer_value(self):
        ctx = {"user": {"age": 30}}
        value, found = get_context_value(ctx, "user.age")
        assert found is True
        assert value == 30


class TestMergeContexts:
    def test_empty_merge(self):
        result = merge_contexts()
        assert result == {}

    def test_single_context(self):
        ctx = {"user": {"email": "a@b.com"}}
        result = merge_contexts(ctx)
        assert result == ctx

    def test_later_wins_same_namespace(self):
        ctx1 = {"user": {"email": "first@b.com", "plan": "free"}}
        ctx2 = {"user": {"email": "second@b.com"}}
        result = merge_contexts(ctx1, ctx2)
        # ctx2 wins for "user" namespace entirely (shallow merge per namespace)
        assert result["user"]["email"] == "second@b.com"

    def test_different_namespaces_merged(self):
        ctx1 = {"user": {"email": "a@b.com"}}
        ctx2 = {"org": {"name": "Acme"}}
        result = merge_contexts(ctx1, ctx2)
        assert "user" in result
        assert "org" in result

    def test_none_contexts_ignored(self):
        ctx1 = {"user": {"email": "a@b.com"}}
        result = merge_contexts(None, ctx1, None)
        assert result == ctx1

    def test_three_way_merge_order(self):
        ctx1 = {"user": {"email": "first"}}
        ctx2 = {"user": {"email": "second"}}
        ctx3 = {"user": {"email": "third"}}
        result = merge_contexts(ctx1, ctx2, ctx3)
        assert result["user"]["email"] == "third"


class TestThreadLocalContext:
    def setup_method(self):
        clear_thread_context()

    def teardown_method(self):
        clear_thread_context()

    def test_initially_none(self):
        assert get_thread_context() is None

    def test_set_and_get(self):
        ctx = {"user": {"email": "test@example.com"}}
        set_thread_context(ctx)
        assert get_thread_context() == ctx

    def test_clear(self):
        ctx = {"user": {"email": "test@example.com"}}
        set_thread_context(ctx)
        clear_thread_context()
        assert get_thread_context() is None

    def test_overwrite(self):
        ctx1 = {"user": {"email": "first@example.com"}}
        ctx2 = {"user": {"email": "second@example.com"}}
        set_thread_context(ctx1)
        set_thread_context(ctx2)
        assert get_thread_context() == ctx2
