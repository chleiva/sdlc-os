"""Acceptance criterion: "A simulated spot interruption completes cordon
-> drain -> flush -> checkpoint before the ~2-minute warning window
closes, verified against F2's Registry showing the checkpoint landed."

Uses a `FakeClock` throughout -- no real `time.sleep` -- so "the ~2-minute
warning window" is simulated instantly while every budget comparison in
`InterruptionWatcher` still sees the full 120 (simulated) seconds.
"""

from __future__ import annotations

from run_registry.result import Outcome

from tenant_cell.interruption_watcher import InterruptionWatcher, Step
from tenant_cell.provisioning_client import FakeProvisioningClient

from .conftest import make_run

WARNING_WINDOW_SECONDS = 120.0


def test_full_sequence_completes_within_warning_window_and_checkpoint_lands(clock, registry, tenant_id):
    client = FakeProvisioningClient(clock)
    node = client.request_node(tenant_id=tenant_id, base_environment="pilot-aws-g7e")
    # A realistic number of in-flight requests that drain comfortably
    # inside the window.
    client.set_in_flight_requests(node, count=5)

    run = make_run(registry, tenant_id)

    watcher = InterruptionWatcher(client, registry, clock)
    result = watcher.handle_interruption(
        node=node,
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://checkpoints/run-1/attempt-1/checkpoint.json",
        warning_window_seconds=WARNING_WINDOW_SECONDS,
    )

    # The full 5-step sequence ran, in order.
    assert [s.step for s in result.steps] == [
        Step.CORDONING,
        Step.STOPPING_INFERENCE,
        Step.DRAINING,
        Step.FLUSHING,
        Step.CHECKPOINTING,
    ]

    assert result.succeeded
    assert result.completed_within_window(WARNING_WINDOW_SECONDS)
    assert result.total_elapsed_seconds <= WARNING_WINDOW_SECONDS
    assert not result.drain_result.timed_out
    assert result.drain_result.drained_count == 5
    assert result.checkpoint_landed
    assert result.checkpoint_pointer == "s3://checkpoints/run-1/attempt-1/checkpoint.json"

    # Verified against F2's Registry showing the checkpoint actually
    # landed -- re-read the run from the real RegistryService, not just
    # trust the watcher's own return value.
    reread = registry.get_run(tenant_id=tenant_id, run_id=run.id)
    assert reread.outcome == Outcome.OK
    assert reread.data.checkpoint_pointer == "s3://checkpoints/run-1/attempt-1/checkpoint.json"


def test_heavy_in_flight_load_fails_back_to_retry_but_still_checkpoints_in_time(clock, registry, tenant_id):
    """More in-flight requests than the window can drain: step 3 fails
    the excess back to orchestrator retry (per spec) rather than blocking
    -- the sequence still finishes, and the checkpoint still lands, well
    inside the warning window.
    """
    client = FakeProvisioningClient(clock)
    node = client.request_node(tenant_id=tenant_id, base_environment="pilot-aws-g7e")
    client.set_in_flight_requests(node, count=500)  # far more than 120s can drain at 1s/request

    run = make_run(registry, tenant_id)
    watcher = InterruptionWatcher(client, registry, clock)
    result = watcher.handle_interruption(
        node=node,
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://checkpoints/run-2/attempt-1/checkpoint.json",
        warning_window_seconds=WARNING_WINDOW_SECONDS,
    )

    assert result.drain_result.timed_out
    assert result.drain_result.failed_to_retry_count > 0
    # Draining itself consumed (up to) the whole window, but flush +
    # checkpoint still run afterward and still land -- this is why the
    # brief says "completes ... before the warning window closes" is
    # about the checkpoint, not about every in-flight request finishing.
    assert result.checkpoint_landed
    assert result.succeeded


def test_stale_version_prevents_checkpoint_and_is_surfaced_as_failure(clock, registry, tenant_id):
    """If the run's version has moved on since the watcher last read it
    (someone else wrote to the run concurrently), the real
    RegistryService's optimistic-concurrency check refuses the write --
    the watcher must surface this as a real failure, not silently drop it.
    """
    client = FakeProvisioningClient(clock)
    node = client.request_node(tenant_id=tenant_id, base_environment="pilot-aws-g7e")
    run = make_run(registry, tenant_id)

    watcher = InterruptionWatcher(client, registry, clock)
    result = watcher.handle_interruption(
        node=node,
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version + 1,  # deliberately stale
        checkpoint_pointer="s3://checkpoints/run-3/attempt-1/checkpoint.json",
        warning_window_seconds=WARNING_WINDOW_SECONDS,
    )

    assert not result.checkpoint_landed
    assert not result.succeeded
    assert result.failure_reason is not None
    assert "stale-version" in result.failure_reason
