from __future__ import annotations

import uuid

import pytest

from platform_release.canary import CanaryRouter, measure_split


def _synthetic_ids(n: int) -> list[str]:
    return [str(uuid.uuid4()) for _ in range(n)]


@pytest.mark.parametrize("fraction", [0.05, 0.1, 0.25, 0.5])
def test_realized_split_is_within_tight_tolerance_of_configured_fraction(fraction):
    router = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=fraction, salt="release-42")
    ids = _synthetic_ids(5000)

    summary = measure_split(router, ids)

    assert summary["total"] == 5000
    assert summary["new_version_count"] + summary["old_version_count"] == 5000
    # Tight tolerance: within 2 percentage points of the configured
    # fraction over 5000 distinct synthetic identifiers.
    assert abs(summary["realized_new_version_fraction"] - fraction) < 0.02


def test_same_identifier_always_routes_the_same_way_no_flapping():
    router = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=0.1, salt="release-42")
    ids = _synthetic_ids(500)

    first_pass = {i: router.route(i).version for i in ids}
    second_pass = {i: router.route(i).version for i in ids}
    assert first_pass == second_pass

    # A brand-new router instance with identical configuration must
    # reproduce the exact same routing decisions -- determinism is a
    # property of (salt, identifier, fraction), never of in-process state.
    fresh_router = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=0.1, salt="release-42")
    third_pass = {i: fresh_router.route(i).version for i in ids}
    assert first_pass == third_pass


def test_repeated_routing_of_the_same_identifier_is_idempotent():
    router = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=0.3, salt="s")
    identifier = "trigger-abc-123"
    decisions = {router.route(identifier).version for _ in range(50)}
    assert len(decisions) == 1  # never flaps between old/new across repeated calls


def test_different_salts_produce_different_but_still_deterministic_splits():
    ids = _synthetic_ids(2000)
    router_a = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=0.2, salt="release-A")
    router_b = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=0.2, salt="release-B")

    decisions_a = {i: router_a.route(i).version for i in ids}
    decisions_b = {i: router_b.route(i).version for i in ids}

    # Not required to be identical (different salts are different
    # releases' independent bucketing), but each is internally consistent.
    assert decisions_a == {i: router_a.route(i).version for i in ids}
    assert decisions_b == {i: router_b.route(i).version for i in ids}
    # And with enough identifiers, they aren't trivially the same mapping.
    assert decisions_a != decisions_b


def test_zero_fraction_routes_nobody_to_new_version():
    router = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=0.0, salt="s")
    ids = _synthetic_ids(1000)
    summary = measure_split(router, ids)
    assert summary["new_version_count"] == 0


def test_full_fraction_routes_everybody_to_new_version():
    router = CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=1.0, salt="s")
    ids = _synthetic_ids(1000)
    summary = measure_split(router, ids)
    assert summary["old_version_count"] == 0


def test_invalid_fraction_is_rejected():
    with pytest.raises(ValueError):
        CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=1.5)
    with pytest.raises(ValueError):
        CanaryRouter(old_version="v6", new_version="v7", new_version_fraction=-0.1)


def test_old_and_new_version_must_differ():
    with pytest.raises(ValueError):
        CanaryRouter(old_version="v6", new_version="v6", new_version_fraction=0.1)
