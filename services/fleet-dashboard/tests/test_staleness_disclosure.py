"""Acceptance criterion: "Every card shows a last-updated timestamp; no
card implies live-ness it doesn't have."
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fleet_dashboard.dashboard_service import Filters
from tests.conftest import make_run


def test_every_card_carries_a_real_registry_and_poll_timestamp(dashboard, registry, tenant_a):
    make_run(registry, tenant_a)
    before = datetime.now(timezone.utc)
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    after = datetime.now(timezone.utc)

    assert result["cards"], "expected at least one card"
    top_level_polled_at = datetime.fromisoformat(result["polled_at"])
    assert before <= top_level_polled_at <= after

    for card in result["cards"]:
        assert card["registry_updated_at"], card
        assert card["polled_at"], card
        card_polled_at = datetime.fromisoformat(card["polled_at"])
        assert before <= card_polled_at <= after
        # Never implies live-ness: the response always names the actual
        # poll interval it operates on, so a client can compute
        # worst-case staleness rather than assume "now."
        assert result["poll_interval_seconds"] > 0


def test_polled_at_advances_on_a_genuinely_new_poll_not_a_cached_one(dashboard, registry, tenant_a):
    make_run(registry, tenant_a)
    first = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    time.sleep(0.05)
    second = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    assert first["polled_at"] != second["polled_at"], (
        "polled_at must reflect the actual poll time of each call, not a cached value "
        "-- otherwise the staleness disclosure itself would be a lie."
    )
