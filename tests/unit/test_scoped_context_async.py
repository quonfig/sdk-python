"""``scoped_context`` is per asyncio task, not per thread (qfg-goi1.2.13).

The scope used to live in ``threading.local()``. Concurrent asyncio tasks
share one thread, so two request handlers whose scopes interleave across an
``await`` evaluated each other's context, and restoring the "old" value on
exit left another task's context installed on the event-loop thread for
every later evaluation. The scope now lives in a ``contextvars.ContextVar``:
each task sees only its own scope, a task spawned inside a scope inherits it,
and nothing is left behind once the scopes exit.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from quonfig import Quonfig
from quonfig.context import clear_thread_context, get_thread_context


def _flag(key: str, user_key: str) -> dict:
    rules = [
        {
            "criteria": [
                {
                    "propertyName": "user.key",
                    "operator": "PROP_IS_ONE_OF",
                    "valueToMatch": {"type": "string_list", "value": [user_key]},
                }
            ],
            "value": {"type": "bool", "value": True},
        },
        {
            "criteria": [{"operator": "ALWAYS_TRUE"}],
            "value": {"type": "bool", "value": False},
        },
    ]
    return {
        "id": "1",
        "projectId": "1",
        "key": key,
        "type": "feature_flag",
        "valueType": "bool",
        "sendToClientSdk": False,
        "environments": [{"id": "Production", "rules": rules}],
    }


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "datadir"
    ff = root / "feature-flags"
    ff.mkdir(parents=True)
    (root / "quonfig.json").write_text(json.dumps({"environments": ["Production"]}))
    for who in ("alice", "bob"):
        (ff / f"is.{who}.json").write_text(json.dumps(_flag(f"is.{who}", who)))
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


def _who(c: Quonfig) -> list:
    return [w for w in ("alice", "bob") if c.is_feature_enabled(f"is.{w}")]


def test_interleaved_tasks_each_see_their_own_scope(client):
    seen: dict = {}

    async def handler(user: str, delay: float) -> None:
        with client.scoped_context({"user": {"key": user}}):
            await asyncio.sleep(delay)
            seen[user] = _who(client)

    async def main() -> None:
        # alice enters first and exits first: non-LIFO across the two scopes.
        await asyncio.gather(handler("alice", 0.05), handler("bob", 0.1))

    asyncio.run(main())
    assert seen == {"alice": ["alice"], "bob": ["bob"]}
    # Nothing leaks onto the thread once both requests finished.
    assert get_thread_context() is None
    assert _who(client) == []


def test_task_spawned_inside_scope_inherits_it(client):
    async def child() -> list:
        return _who(client)

    async def main() -> list:
        with client.scoped_context({"user": {"key": "alice"}}):
            return await asyncio.create_task(child())

    assert asyncio.run(main()) == ["alice"]


def test_new_thread_starts_without_the_scope(client):
    out: list = []
    with client.scoped_context({"user": {"key": "alice"}}):
        t = threading.Thread(target=lambda: out.append(_who(client)))
        t.start()
        t.join()
        assert _who(client) == ["alice"]
    assert out == [[]]
