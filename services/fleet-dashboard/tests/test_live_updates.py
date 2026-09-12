"""Acceptance criterion: "A card's stage updates within a few seconds of
the underlying Registry row changing."

This is a REAL test, not a mock: it writes a Run via `RegistryService`,
polls the dashboard, then changes the Run's stage via `RegistryService`
again, waits `config.POLL_INTERVAL_SECONDS` (the actual interval the
brief requires, "a real interval, a few seconds"), polls again, and
asserts the served view reflects the change.
"""

from __future__ import annotations

import time

from fleet_dashboard import config
from fleet_dashboard.dashboard_service import Filters
from tests.conftest import advance, make_run


def _card_for(result, run_id):
    matches = [c for c in result["cards"] if c["run_id"] == run_id]
    assert len(matches) == 1, result["cards"]
    return matches[0]


def test_stage_change_is_reflected_within_one_poll_interval(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)

    first = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(first, run.id)
    assert card["stage"] == "intake"
    assert card["column"] == "Analysis"

    # Real Registry Service write -- the same op D1/D2 would call.
    run = advance(registry, run, "research")

    time.sleep(config.POLL_INTERVAL_SECONDS)

    second = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(second, run.id)
    assert card["stage"] == "research"
    assert card["column"] == "Analysis"  # still Analysis (Sec 5 stage 2), different sub-state


def test_transition_out_of_the_board_entirely_clears_the_card(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)
    dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())

    run = advance(registry, run, "abandoned")

    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    assert run.id not in {c["run_id"] for c in result["cards"]}


def test_elapsed_in_stage_resets_when_this_dashboard_observes_the_transition(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)
    dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())

    time.sleep(1.2)
    run = advance(registry, run, "research")

    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)
    # We observed the transition ourselves just now, so elapsed-in-stage
    # should be near zero, not ~1.2s+ (the age of the *previous* stage).
    assert card["elapsed_in_stage_seconds"] < 1.0
    assert card["elapsed_in_stage_is_exact"] is True
