"""Acceptance criterion: "A trace for a given run correlates directly
between an agent-level event and the infra-level trace for the same
timeframe, via shared keys" (master spec Section 16.1).

Real test shape, per the task brief: drive a run through the real
orchestrator far enough to reach a stage transition, separately drive
tenant-cell's real `InterruptionWatcher` for that same run_id, and
confirm both emitted events carry the same trace ID and can be joined by
it. Two independent `ObservabilityClient` instances are used -- one
standing in for "the orchestrator process", one for "the tenant-cell
process" -- each with its own real OTel `TracerProvider`, both wired to
the SAME `EventSink`, exactly mirroring how two independent real
services would each run their own SDK but ship to one centralized
off-node store.
"""

from __future__ import annotations

from run_registry import RegistryService

from observability import ObservabilityClient
from observability.tracing import trace_key_to_otel_hex
from orchestrator.core import Orchestrator
from orchestrator.model_backend import AcceptanceCriterion, PlanOutput, ScriptedAgentBackend, SubTask
from orchestrator.plan_artifact import PlanArtifactStore
from orchestrator.progress import RunProgressStore
from orchestrator.verification import ScriptedVerificationRunner
from tenant_cell.clock import FakeClock
from tenant_cell.interruption_watcher import InterruptionWatcher, Step
from tenant_cell.provisioning_client import FakeProvisioningClient


def _one_subtask_plan() -> PlanOutput:
    """A minimal, valid `PlanOutput` -- just enough to drive the
    orchestrator through intake -> research -> plan_authoring ->
    plan_approval_gate (a real stage transition), mirroring
    `services/orchestrator/tests/_factories.make_plan_output`'s shape
    without importing that test-only module across a package boundary.
    """
    return PlanOutput(
        outcomes="Add foo to the service.",
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="AC1", description="foo works", verification_tests=("tests/test_foo.py::test_foo",)),
        ),
        scope_in=("src/foo.py",),
        scope_out=(),
        subtasks=(SubTask(task_id="t1", description="implement foo", parallel_group=None, depends_on=()),),
        story_size="S",
        cross_cutting_or_high_risk=False,
        risk_tier="low",
        rollback_strategy="git revert the merge commit",
    )


def test_agent_level_and_infra_level_events_share_the_same_trace_id(tmp_path, tenant_id, sink):
    db_path = str(tmp_path / "registry.db")
    registry = RegistryService(db_path)

    orchestrator_observability = ObservabilityClient(sink=sink, service_name="orchestrator")
    tenant_cell_observability = ObservabilityClient(sink=sink, service_name="tenant-cell")

    # ---- Agent-level: drive the real orchestrator far enough to reach a
    # stage transition (start_run alone crosses intake -> research ->
    # plan_authoring -> plan_approval_gate). ----
    orch = Orchestrator(
        registry=registry,
        tenant_id=tenant_id,
        agent_backend=ScriptedAgentBackend(plans=[_one_subtask_plan()]),
        verification_runner=ScriptedVerificationRunner(),
        plan_store=PlanArtifactStore(tmp_path / "plan_artifacts"),
        progress_store=RunProgressStore(tmp_path / "progress"),
        observability=orchestrator_observability,
    )
    status = orch.start_run(jira_key="PROJ-100", repo="acme/app", branch="feature/trace-join", trace_id="shared-trace-xyz")
    run = registry.get_run(tenant_id=tenant_id, run_id=status.run_id).data
    assert run.trace_id == "shared-trace-xyz"

    # ---- Infra-level: separately drive tenant-cell's real
    # InterruptionWatcher for the SAME run_id -- a different "process",
    # different ObservabilityClient/TracerProvider, same EventSink. ----
    clock = FakeClock()
    client = FakeProvisioningClient(clock)
    node = client.request_node(tenant_id=tenant_id, base_environment="pilot-aws-g7e")
    watcher = InterruptionWatcher(client, registry, clock, observability=tenant_cell_observability)
    result = watcher.handle_interruption(
        node=node,
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://checkpoints/run/attempt-1/checkpoint.json",
    )
    assert result.checkpoint_landed

    # ---- Join by the shared trace_id key. ----
    joined = sink.by_trace_id("shared-trace-xyz")
    agent_level = [e for e in joined if e.name == "stage_transition"]
    infra_level = [e for e in joined if e.name in ("observability_flush", "checkpoint_write")]
    assert agent_level, "expected at least one agent-level stage_transition event under the shared trace_id"
    assert len(infra_level) == 2, "expected both the flush and checkpoint infra-level events"

    # Not just a string tag: the real OTel trace id (derived
    # deterministically from the shared trace_id key) is IDENTICAL across
    # both independently-built TracerProviders -- genuine distributed
    # trace correlation, not merely two events that happen to share a
    # sink-level label.
    expected_otel_hex = trace_key_to_otel_hex("shared-trace-xyz")
    for ev in agent_level + infra_level:
        assert ev.attributes["otel_trace_id_hex"] == expected_otel_hex

    registry.close()
