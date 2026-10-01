"""Nested ``scoped_context`` stacks REPLACE_NAMED (qfg-2agi.37).

The documented context-merge rule (qfg-2agi.24): a newer tier's named
context replaces the whole same-named context, and named contexts it does
not mention survive. An inner ``scoped_context`` must therefore keep the
outer scope's ``team`` while replacing its ``user`` wholesale (dropping
``email``), and the outer scope must be restored when the inner one exits.
"""

from __future__ import annotations

import json

import pytest

from quonfig import Quonfig
from quonfig.context import clear_thread_context, get_thread_context

OUTER = {"user": {"email": "a@prefab.cloud"}, "team": {"key": "t1"}}
INNER = {"user": {"plan": "pro"}}


def _flag(key: str, prop: str) -> dict:
    return {
        "id": "1",
        "projectId": "1",
        "key": key,
        "type": "feature_flag",
        "valueType": "bool",
        "sendToClientSdk": False,
        "environments": [
            {
                "id": "Production",
                "rules": [
                    {
                        "criteria": [
                            {
                                "propertyName": prop,
                                "operator": "PROP_IS_ONE_OF",
                                "valueToMatch": {
                                    "type": "string_list",
                                    "value": ["a@prefab.cloud", "pro", "t1"],
                                },
                            }
                        ],
                        "value": {"type": "bool", "value": True},
                    },
                    {
                        "criteria": [{"operator": "ALWAYS_TRUE"}],
                        "value": {"type": "bool", "value": False},
                    },
                ],
            }
        ],
        "variants": [
            {"value": {"type": "bool", "value": False}},
            {"value": {"type": "bool", "value": True}},
        ],
        "default": {
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {"type": "bool", "value": False},
                }
            ]
        },
    }


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "datadir"
    ff = root / "feature-flags"
    ff.mkdir(parents=True)
    (root / "quonfig.json").write_text(json.dumps({"environments": ["Production"]}))
    for name, prop in (
        ("has-email", "user.email"),
        ("has-plan", "user.plan"),
        ("has-team", "team.key"),
    ):
        (ff / f"probe.{name}.json").write_text(json.dumps(_flag(f"probe.{name}", prop)))
    clear_thread_context()
    c = Quonfig(
        datadir=str(root),
        environment="Production",
        enable_sse=False,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        enable_quonfig_user_context=False,
    ).init()
    yield c
    clear_thread_context()


def _seen(c: Quonfig) -> dict:
    return {k: c.is_feature_enabled(f"probe.has-{k}") for k in ("email", "plan", "team")}


def test_nested_scope_keeps_outer_named_contexts_and_replaces_same_named(client):
    with client.scoped_context(OUTER):
        with client.scoped_context(INNER):
            # team survives from the outer scope; user is replaced wholesale.
            assert _seen(client) == {"email": False, "plan": True, "team": True}


def test_inner_scope_exit_restores_outer_scope(client):
    with client.scoped_context(OUTER):
        with client.scoped_context(INNER):
            pass
        assert _seen(client) == {"email": True, "plan": False, "team": True}
        assert get_thread_context() == OUTER
    assert get_thread_context() is None


def test_nested_scope_does_not_mutate_outer_dict(client):
    outer = {"user": {"email": "a@prefab.cloud"}, "team": {"key": "t1"}}
    with client.scoped_context(outer):
        with client.scoped_context(INNER):
            pass
    assert outer == {"user": {"email": "a@prefab.cloud"}, "team": {"key": "t1"}}
