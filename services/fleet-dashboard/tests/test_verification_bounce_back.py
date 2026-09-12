"""Acceptance criterion: "A verification failure produces a visible edge
back to Development on the same card, not a new disconnected card."
"""

from __future__ import annotations

from fleet_dashboard.dashboard_service import Filters
from tests.conftest import advance, make_run


def _card_for(result, run_id):
    matches = [c for c in result["cards"] if c["run_id"] == run_id]
    assert len(matches) == 1, result["cards"]
    return matches[0]


def _drive_to_verification(registry, run):
    for stage in ("research", "plan_authoring", "plan_approval_gate", "implementation", "verification"):
        run = advance(registry, run, stage)
    return run


def test_verification_failure_shows_a_bounce_back_badge_on_the_same_run_id(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)
    run = _drive_to_verification(registry, run)

    before = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    testing_card = _card_for(before, run.id)
    assert testing_card["column"] == "Testing"
    assert testing_card["bounced_back_from_testing"] is False

    # The verification failure: loop back to implementation, same run_id.
    run = advance(registry, run, "implementation")

    after = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    # Same card (same run_id), not a new one, and not vanished.
    dev_card = _card_for(after, run.id)
    assert dev_card["column"] == "Development"
    assert dev_card["bounced_back_from_testing"] is True
    assert dev_card["bounced_back_at"] is not None

    # Exactly one card for this run_id in the whole board -- no
    # disconnected duplicate was created for the bounce-back.
    all_cards_for_run = [c for c in after["cards"] if c["run_id"] == run.id]
    assert len(all_cards_for_run) == 1


def test_bounce_back_badge_clears_once_the_run_moves_on_from_development(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)
    run = _drive_to_verification(registry, run)
    run = advance(registry, run, "implementation")
    dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())  # observe the bounce

    run = advance(registry, run, "verification")
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)
    assert card["column"] == "Testing"
    assert card["bounced_back_from_testing"] is False


def test_a_run_that_reaches_implementation_for_the_first_time_is_not_marked_bounced(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)
    dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    for stage in ("research", "plan_authoring", "plan_approval_gate", "implementation"):
        run = advance(registry, run, stage)

    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)
    assert card["column"] == "Development"
    assert card["bounced_back_from_testing"] is False
