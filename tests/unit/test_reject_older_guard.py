"""Reject-older install guard for canonical ordering (qfg-7h5d.1.8).

Mirrors sdk-go's ordering_guard_test.go. The whole rule is: an install on a
network path advances the held config only if the incoming Meta.generation is
strictly greater than the held generation. A fresh client (nothing installed)
always seeds off whatever arrives first, even an older/gen-0 snapshot; an
established client never regresses; a same-generation snapshot is a no-op. A
gen<=0 snapshot installs only while the held generation is still 0 (the client
has never held a real generation) -- qfg-9dxb.9.

Datadir installs (``guard=False``, the default) bypass the rule — a local data
dir is the source of truth and always reports generation 0.
"""

from __future__ import annotations

from quonfig.store import ConfigStore
from quonfig.types import ConfigEnvelope, ConfigResponse, Meta, RuleSet


def _envelope(generation: int, version: str = "v") -> ConfigEnvelope:
    return ConfigEnvelope(
        configs=[],
        meta=Meta(
            version=f"{version}-{generation}", environment="production", generation=generation
        ),
    )


def test_fresh_client_seeds_off_first_snapshot_even_if_old() -> None:
    store = ConfigStore()
    # Nothing installed yet — a gen-0 (or any) snapshot seeds the fresh client.
    assert store.update(_envelope(0), guard=True) is True
    assert store.get_generation() == 0
    assert store.install_count() == 1


def test_established_client_never_regresses_to_older_generation() -> None:
    store = ConfigStore()
    assert store.update(_envelope(42), guard=True) is True
    # A failover to an OLDER secondary must be dropped — this is o02.
    installed = store.update(_envelope(41), guard=True)
    assert installed is False
    assert store.get_generation() == 42
    assert store.install_count() == 1


def _keyed_envelope(generation: int, key: str) -> ConfigEnvelope:
    return ConfigEnvelope(
        configs=[
            ConfigResponse(
                id=key,
                key=key,
                type="config",
                value_type="string",
                send_to_client_sdk=False,
                default=RuleSet(rules=[]),
            )
        ],
        meta=Meta(version=f"{key}-{generation}", environment="production", generation=generation),
    )


def test_gen0_never_overrides_a_held_real_generation() -> None:
    # qfg-9dxb.9: gen 0 now only comes from a server whose git object store is
    # damaged (rev-count failed). It must not move a client that holds a real
    # generation back to OLD content.
    store = ConfigStore()
    assert store.update(_keyed_envelope(42, "NEW"), guard=True) is True
    assert store.update(_keyed_envelope(0, "OLD"), guard=True) is False
    assert store.keys() == ["NEW"]
    assert store.get_generation() == 42
    # The healthy gen-N re-delivery is an equal-generation no-op; the client
    # still holds NEW (it never left it).
    assert store.update(_keyed_envelope(42, "NEW"), guard=True) is False
    assert store.keys() == ["NEW"]
    assert store.get_generation() == 42
    assert store.install_count() == 1


def test_client_that_only_ever_saw_gen0_keeps_installing_gen0() -> None:
    # A client that has never held a real generation (held == 0) has no
    # ordering to protect, so every gen-0 payload installs (e.g. ``qfg serve``).
    store = ConfigStore()
    assert store.update(_keyed_envelope(0, "first"), guard=True) is True
    assert store.update(_keyed_envelope(0, "second"), guard=True) is True
    assert store.keys() == ["second"]
    assert store.update(_keyed_envelope(0, "third"), guard=True) is True
    assert store.keys() == ["third"]
    assert store.get_generation() == 0
    assert store.install_count() == 3


def test_same_generation_is_a_noop() -> None:
    store = ConfigStore()
    assert store.update(_envelope(42), guard=True) is True
    # Equal second leg must not re-install or flap (o04).
    assert store.update(_envelope(42, version="other"), guard=True) is False
    assert store.get_generation() == 42
    assert store.install_count() == 1


def test_newer_generation_heals_forward() -> None:
    store = ConfigStore()
    assert store.update(_envelope(41), guard=True) is True
    # A later, newer primary win heals forward (o03).
    assert store.update(_envelope(42), guard=True) is True
    assert store.get_generation() == 42
    assert store.install_count() == 2


def test_datadir_install_bypasses_guard() -> None:
    store = ConfigStore()
    assert store.update(_envelope(42), guard=True) is True
    # Datadir reload (guard=False) is a local source of truth at gen 0 and must
    # still install despite the lower generation.
    assert store.update(_envelope(0)) is True
    assert store.get_generation() == 0
    assert store.install_count() == 2
