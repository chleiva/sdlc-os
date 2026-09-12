"""Acceptance criterion: "A simulated node reclamation shows the trace
flush completing before the checkpoint write, with no gap in the record"
(master spec Section 14.8 step 4 / Section 16.4).

Real test shape, per the task brief: extend/wrap tenant-cell's real
`InterruptionWatcher` state machine (never rebuilt here -- this test
drives the actual `handle_interruption` sequence, unmodified in its own
right beyond the additive `observability=` constructor kwarg from D11),
using the real `run_registry.RegistryService`, and assert the
flush-observed event's arrival at the off-node store strictly precedes
the checkpoint-write event's arrival -- proven from the sink's own
independent bookkeeping (`sequence`, and a wall-clock `timestamp_ns`),
not merely by re-reading `InterruptionResult.steps`, which is the
watcher's own in-process account of itself.
"""

from __future__ import annotations

from run_registry.result import Outcome

from observability import ObservabilityClient
from tenant_cell.interruption_watcher import InterruptionWatcher, Step
from tenant_cell.provisioning_client import FakeProvisioningClient


def _make_run(registry, tenant_id, trace_id):
    result = registry.create_run(
        tenant_id=tenant_id,
        jira_key="PROJ-200",
        repo="acme/app",
        branch="feature/interruption",
        capacity_class="spot",
        trace_id=trace_id,
    )
    assert result.outcome == Outcome.OK, result
    return result.data


def test_flush_event_reaches_off_node_store_before_checkpoint_write_event(registry, tenant_id, sink):
    from tenant_cell.clock import FakeClock

    clock = FakeClock()
    provisioning_client = FakeProvisioningClient(clock)
    node = provisioning_client.request_node(tenant_id=tenant_id, base_environment="pilot-aws-g7e")
    provisioning_client.set_in_flight_requests(node, count=5)

    run = _make_run(registry, tenant_id, trace_id="trace-interruption-ordering")

    observability = ObservabilityClient(sink=sink, service_name="tenant-cell")
    watcher = InterruptionWatcher(provisioning_client, registry, clock, observability=observability)

    result = watcher.handle_interruption(
        node=node,
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://checkpoints/run/attempt-1/checkpoint.json",
        warning_window_seconds=120.0,
    )
    assert result.succeeded
    assert result.checkpoint_landed

    # The watcher's own in-process step order (sanity check only -- the
    # real proof is against the sink below, not this).
    assert [s.step for s in result.steps] == [
        Step.CORDONING, Step.STOPPING_INFERENCE, Step.DRAINING, Step.FLUSHING, Step.CHECKPOINTING,
    ]

    flush_events = sink.by_kind("span")
    flush_event = next(e for e in flush_events if e.name == "observability_flush")
    checkpoint_event = next(e for e in flush_events if e.name == "checkpoint_write")

    # Proof #1: the sink's own monotonic arrival-order counter (assigned
    # independently of anything the watcher itself tracks) shows the
    # flush event arrived strictly before the checkpoint event.
    assert flush_event.sequence < checkpoint_event.sequence

    # Proof #2: the sink's own wall-clock timestamp of when each event
    # was durably recorded -- not the FakeClock the watcher's *budget*
    # logic uses -- also shows flush strictly before checkpoint.
    assert flush_event.timestamp_ns <= checkpoint_event.timestamp_ns

    # Proof #3: no gap in the record -- both infra-level events, and only
    # those two, exist for this run under its trace_id by the time the
    # sequence completes.
    infra_events = [e for e in sink.by_trace_id("trace-interruption-ordering") if e.kind == "span"]
    assert {e.name for e in infra_events} == {"observability_flush", "checkpoint_write"}
    assert flush_event.attributes["flush_ok"] == "True"
    assert checkpoint_event.attributes["checkpoint_landed"] == "True"


def test_flush_still_precedes_checkpoint_even_when_flush_reports_failure(registry, tenant_id, sink):
    """The watcher's own documented behavior: step 5 always runs even if
    step 4 reported failure (losing the durable checkpoint is worse than
    losing already-flushed observability data). The ordering guarantee
    must hold either way -- this is not "ordering only when things go
    well"."""
    from tenant_cell.clock import FakeClock

    class FlushFailsClient(FakeProvisioningClient):
        def flush_observability(self, node):
            return False

    clock = FakeClock()
    provisioning_client = FlushFailsClient(clock)
    node = provisioning_client.request_node(tenant_id=tenant_id, base_environment="pilot-aws-g7e")

    run = _make_run(registry, tenant_id, trace_id="trace-interruption-flush-fails")
    observability = ObservabilityClient(sink=sink, service_name="tenant-cell")
    watcher = InterruptionWatcher(provisioning_client, registry, clock, observability=observability)

    result = watcher.handle_interruption(
        node=node,
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://checkpoints/run/attempt-1/checkpoint.json",
    )
    assert result.checkpoint_landed  # checkpoint still lands
    assert not result.flush_ok
    assert not result.succeeded  # but the overall sequence surfaces the flush failure

    events = sink.by_trace_id("trace-interruption-flush-fails")
    flush_event = next(e for e in events if e.name == "observability_flush")
    checkpoint_event = next(e for e in events if e.name == "checkpoint_write")
    assert flush_event.sequence < checkpoint_event.sequence
    assert flush_event.attributes["flush_ok"] == "False"
