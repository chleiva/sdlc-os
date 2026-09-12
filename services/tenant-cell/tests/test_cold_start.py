"""Acceptance criterion: "Cold-start from launch to serving-ready meets
the Section 16.6 target on the pilot instance tier."

There is no real node to launch in this environment (hard constraint) --
this measures the control-logic overhead only: elapsed time between
`track_cold_start` issuing a (mocked) provisioning request and the first
successful (mocked) health check, driven entirely by `FakeClock`. See
`cold_start.py`'s module docstring for what this test can and cannot
claim about real cloud launch latency.
"""

from __future__ import annotations

from tenant_cell.cold_start import DEFAULT_COLD_START_BUDGET_SECONDS, track_cold_start
from tenant_cell.provisioning_client import FakeProvisioningClient


def test_cold_start_within_budget_on_the_pilot_tier(clock):
    # Simulate the node becoming healthy after 45 (simulated) seconds --
    # comfortably inside the default few-minutes budget.
    client = FakeProvisioningClient(clock, default_ready_after_seconds=45.0)

    result = track_cold_start(
        client,
        clock,
        tenant_id="acme",
        base_environment="pilot-aws-g7e",
        poll_interval_seconds=5.0,
    )

    assert not result.timed_out
    assert result.elapsed_seconds == 45.0
    assert result.within_budget
    assert result.budget_seconds == DEFAULT_COLD_START_BUDGET_SECONDS


def test_cold_start_exceeding_budget_is_reported_as_not_within_budget(clock):
    client = FakeProvisioningClient(clock, default_ready_after_seconds=600.0)  # 10 minutes

    result = track_cold_start(
        client,
        clock,
        tenant_id="acme",
        base_environment="pilot-aws-g7e",
        budget_seconds=DEFAULT_COLD_START_BUDGET_SECONDS,  # 5 minutes
        poll_interval_seconds=10.0,
        max_polls=100,
    )

    assert not result.timed_out
    assert result.elapsed_seconds == 600.0
    assert result.within_budget is False


def test_cold_start_times_out_when_health_check_never_passes(clock):
    client = FakeProvisioningClient(clock, default_ready_after_seconds=10_000.0)

    result = track_cold_start(
        client,
        clock,
        tenant_id="acme",
        base_environment="pilot-aws-g7e",
        poll_interval_seconds=5.0,
        max_polls=10,
    )

    assert result.timed_out
    assert result.elapsed_seconds is None
    assert result.within_budget is None
    assert result.poll_count == 10
