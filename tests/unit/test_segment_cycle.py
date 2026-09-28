"""Segment reference cycles (qfg-9dxb.7).

A segment that is, via any chain, IN_SEG itself used to recurse until
``RecursionError`` escaped ``get()``. Mirrors sdk-go (qfg-9dxb.4): a
reference back onto the current evaluation path is treated like a missing
segment (IN_SEG false, NOT_IN_SEG true). Diamonds still resolve.
"""

from __future__ import annotations

from quonfig import Quonfig
from quonfig.evaluator import Evaluator
from quonfig.types import (
    ConfigEnvelope,
    ConfigResponse,
    Criterion,
    Meta,
    Rule,
    RuleSet,
    Value,
)


def _seg_crit(op, seg_key):
    return Criterion(operator=op, value_to_match=Value(type="string", value=seg_key))


def _segment(key, criteria, value=True):
    return ConfigResponse(
        id=f"id-{key}",
        key=key,
        type="segment",
        value_type="bool",
        send_to_client_sdk=False,
        default=RuleSet(rules=[Rule(criteria=criteria, value=Value(type="bool", value=value))]),
    )


def _flag(key, op, seg_key):
    return ConfigResponse(
        id=f"id-{key}",
        key=key,
        type="feature_flag",
        value_type="string",
        send_to_client_sdk=True,
        default=RuleSet(
            rules=[
                Rule(criteria=[_seg_crit(op, seg_key)], value=Value(type="string", value="hit")),
                Rule(
                    criteria=[Criterion(operator="ALWAYS_TRUE")],
                    value=Value(type="string", value="miss"),
                ),
            ]
        ),
    )


def _client(configs):
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="return_zero_value",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    c._store.update(
        ConfigEnvelope(configs=configs, meta=Meta(version="test", environment="Production"))
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._initialized.set()
    return c


def _self_ref():
    return [_segment("seg.a", [_seg_crit("IN_SEG", "seg.a")])]


def _two_hop():
    return [
        _segment("seg.a", [_seg_crit("IN_SEG", "seg.b")]),
        _segment("seg.b", [_seg_crit("IN_SEG", "seg.a")]),
    ]


class TestSegmentCycle:
    def test_self_reference_in_seg_is_false(self):
        c = _client(_self_ref() + [_flag("flag", "IN_SEG", "seg.a")])
        assert c.get_string("flag") == "miss"

    def test_self_reference_not_in_seg_is_true(self):
        c = _client(_self_ref() + [_flag("flag", "NOT_IN_SEG", "seg.a")])
        assert c.get_string("flag") == "hit"

    def test_two_hop_cycle_in_seg_is_false(self):
        c = _client(_two_hop() + [_flag("flag", "IN_SEG", "seg.a")])
        assert c.get_string("flag") == "miss"

    def test_two_hop_cycle_not_in_seg_is_true(self):
        c = _client(_two_hop() + [_flag("flag", "NOT_IN_SEG", "seg.a")])
        assert c.get_string("flag") == "hit"

    def test_two_hop_cycle_via_evaluator_does_not_raise(self):
        c = _client(_two_hop())
        result = Evaluator(c._store).evaluate("seg.a", {})
        assert result.reason == "MISSING"

    def test_self_reference_evaluates_segment_once(self, monkeypatch):
        # The cycle must be cut on the first repeat, not after the
        # interpreter's recursion limit is hit and swallowed.
        c = _client(_self_ref() + [_flag("flag", "IN_SEG", "seg.a")])
        calls = []
        orig = Evaluator.evaluate

        def counting(self, key, *args, **kwargs):
            calls.append(key)
            return orig(self, key, *args, **kwargs)

        monkeypatch.setattr(Evaluator, "evaluate", counting)
        c.get_string("flag")
        assert calls.count("seg.a") == 1

    def test_mixed_polarity_cycle_is_deterministic(self):
        # flag IN_SEG a; a IN_SEG b; b NOT_IN_SEG a. With Go semantics the
        # back-reference b -> a is a missing segment, so NOT_IN_SEG is true,
        # b is true, a is true, and the flag hits -- at any caller stack depth.
        # Before the guard the answer flipped with the depth at which the
        # interpreter's recursion limit happened to trip.
        configs = [
            _segment("seg.a", [_seg_crit("IN_SEG", "seg.b")]),
            _segment("seg.b", [_seg_crit("NOT_IN_SEG", "seg.a")]),
            _flag("flag", "IN_SEG", "seg.a"),
        ]
        c = _client(configs)

        def at_depth(n):
            if n == 0:
                return c.get_string("flag")
            return at_depth(n - 1)

        outcomes = {at_depth(d) for d in range(0, 60)}
        assert outcomes == {"hit"}

    def test_diamond_still_resolves(self):
        # b and c both reference d; a references both. Not a cycle.
        configs = [
            _segment("seg.d", [Criterion(operator="ALWAYS_TRUE")]),
            _segment("seg.b", [_seg_crit("IN_SEG", "seg.d")]),
            _segment("seg.c", [_seg_crit("IN_SEG", "seg.d")]),
            _segment("seg.a", [_seg_crit("IN_SEG", "seg.b"), _seg_crit("IN_SEG", "seg.c")]),
            _flag("flag", "IN_SEG", "seg.a"),
        ]
        assert _client(configs).get_string("flag") == "hit"


class TestDecryptWithCycle:
    def test_decrypt_with_self_reference_does_not_loop(self):
        # A confidential value whose decryptWith points at its own key. The
        # resolver reads the key config's raw value without resolving it, so
        # this must fail as a decryption error rather than recurse.
        config = ConfigResponse(
            id="id-secret",
            key="secret",
            type="config",
            value_type="string",
            send_to_client_sdk=False,
            default=RuleSet(
                rules=[
                    Rule(
                        criteria=[Criterion(operator="ALWAYS_TRUE")],
                        value=Value(
                            type="string",
                            value="not-real-ciphertext--abcd",
                            confidential=True,
                            decrypt_with="secret",
                        ),
                    )
                ]
            ),
        )
        c = _client([config])
        # Must not raise RecursionError; any non-recursive outcome is fine.
        try:
            c.get_string("secret", default="fallback")
        except RecursionError:  # pragma: no cover - the failure we guard against
            raise
        except Exception:
            pass
