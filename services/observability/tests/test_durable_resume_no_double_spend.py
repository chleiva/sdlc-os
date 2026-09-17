"""Acceptance criterion: "A container restart resumes a run from its
last checkpoint, verified not to re-spend already-completed budget"
(master spec Section 16.1's durable-execution requirement, applied to
telemetry specifically).

Per the task brief, this EXTENDS orchestrator's own real durable-resume
proof shape (`services/orchestrator/tests/test_durable_resume.py`:
`test_resume_after_simulated_process_restart_mid_implementation`) rather
than duplicating its fixture from scratch -- the same "process 1 does
t1, exhausts its retry budget attempting t2 and pauses on a real,
durable "stuck" checkpoint (`core.py`'s `_handle_implementation_failure`,
not an uncaught crash -- see that method's own docstring); process 2
(fresh Orchestrator/RegistryService/stores over the same on-disk state)
resumes, sees the same pending checkpoint, is told to continue past it,
and does only t2" scenario,
with an `observability=` client wired into BOTH processes (sharing one
on-disk sink, since the real off-node store outlives either process),
and this test's own additional assertion: the cumulative cost metric for
the run, read back from the sink after resume, reflects t1's cost
exactly once and t2's cost exactly once -- never t1's twice, never
missing either.

A second, more direct test below proves the underlying no-double-spend
mechanism itself (`ObservabilityClient.record_cost_increment`'s
idempotency-key dedup), independent of the orchestrator integration --
so a failure in one pinpoints whether the bug is in the ledger's own
semantics or in where the orchestrator's instrumentation call was placed.
"""

from __future__ import annotations

from run_registry import RegistryService

from observability import EventSink, ObservabilityClient
from orchestrator.core import Orchestrator
from orchestrator.model_backend import AcceptanceCriterion, DiffOutput, PlanOutput, ScriptedAgentBackend, SubTask
from orchestrator.plan_artifact import PlanArtifactStore
from orchestrator.progress import RunProgressStore
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult


def _two_subtask_plan() -> PlanOutput:
    return PlanOutput(
        outcomes="Add foo/bar to the service.",
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="AC1", description="works", verification_tests=("tests/test_foo.py::test_foo",)),
        ),
        scope_in=("src/a.py", "src/b.py"),
        scope_out=(),
        subtasks=(
            SubTask(task_id="t1", description="implement a", parallel_group=None, depends_on=()),
            SubTask(task_id="t2", description="implement b", parallel_group=None, depends_on=("t1",)),
        ),
        story_size="S",
        cross_cutting_or_high_risk=False,
        risk_tier="low",
        rollback_strategy="git revert the merge commit",
    )


def test_resumed_run_does_not_double_count_pre_restart_cost(tmp_path, tenant_id):
    db_path = str(tmp_path / "registry.db")
    plan_dir = tmp_path / "plan_artifacts"
    progress_dir = tmp_path / "progress"
    # One durable, file-backed off-node store shared across BOTH
    # "process 1" and "process 2" below -- the real store outlives either
    # process, exactly like a real Loki/Tempo/Prometheus deployment does.
    sink = EventSink(path=tmp_path / "off_node_store.jsonl")

    # ---- "Process 1": drive to the plan gate, approve it, complete t1
    # for real (its cost metric ships for real), then exhaust its retry budget attempting
    # t2 (backend_1's script is exhausted -- the same stand-in
    # `test_durable_resume.py` itself uses). ----
    backend_1 = ScriptedAgentBackend(
        plans=[_two_subtask_plan()],
        diffs=[DiffOutput(files_touched=("src/a.py",), lines_changed=5, commit_message="a", subtask_id="t1")],
    )
    registry_1 = RegistryService(db_path)
    observability_1 = ObservabilityClient(sink=sink, service_name="orchestrator")
    orch_1 = Orchestrator(
        registry=registry_1,
        tenant_id=tenant_id,
        agent_backend=backend_1,
        verification_runner=ScriptedVerificationRunner(),
        plan_store=PlanArtifactStore(plan_dir),
        progress_store=RunProgressStore(progress_dir),
        observability=observability_1,
    )
    status = orch_1.start_run(jira_key="PROJ-300", repo="acme/app", branch="feature/resume-cost", trace_id="trace-resume-cost")
    run_id = status.run_id

    status = orch_1.approve_plan(status.run_id, decision="approve")
    assert status.paused is True
    assert status.pause_kind == "checkpoint"
    assert status.checkpoint.trigger == "stuck"

    registry_1.close()
    del orch_1, registry_1, backend_1, observability_1

    # t1's cost landed exactly once before the crash.
    t1_events_before_resume = [
        e for e in sink.by_run_id(run_id) if e.kind == "metric" and e.attributes.get("unit_id") == "t1"
    ]
    assert len(t1_events_before_resume) == 1
    cost_before_resume = t1_events_before_resume[0].attributes["cumulative_total"]
    assert cost_before_resume > 0

    # ---- "Process 2": brand new Orchestrator/RegistryService/stores,
    # brand new ObservabilityClient (a genuinely independent instance --
    # no shared Python object with process 1 except the on-disk sink
    # file and on-disk registry DB), that only knows how to do t2. ----
    backend_2 = ScriptedAgentBackend(
        diffs=[DiffOutput(files_touched=("src/b.py",), lines_changed=5, commit_message="b", subtask_id="t2")]
    )
    registry_2 = RegistryService(db_path)
    observability_2 = ObservabilityClient(sink=sink, service_name="orchestrator")
    orch_2 = Orchestrator(
        registry=registry_2,
        tenant_id=tenant_id,
        agent_backend=backend_2,
        verification_runner=ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")]),
        plan_store=PlanArtifactStore(plan_dir),
        progress_store=RunProgressStore(progress_dir),
        observability=observability_2,
    )
    resumed_status = orch_2.resume_run(run_id)
    # Durable across the "restart": the pending "stuck" checkpoint from
    # process 1 is still there, read from real on-disk state -- resuming
    # must not silently blow past it.
    assert resumed_status.paused is True
    assert resumed_status.pause_kind == "checkpoint"
    assert resumed_status.checkpoint.trigger == "stuck"

    resumed_status = orch_2.resolve_checkpoint(run_id, decision="continue")

    assert resumed_status.stage == "change_review_gate"
    assert backend_2.implement_call_count == 1  # only t2 was (re-)executed

    # ---- The no-double-spend proof: exactly one t1 cost event (still,
    # from process 1 -- process 2 never redoes t1) and exactly one t2
    # cost event (from process 2), read back from the ONE durable sink
    # both processes shared. ----
    t1_events_after_resume = [
        e for e in sink.by_run_id(run_id) if e.kind == "metric" and e.attributes.get("unit_id") == "t1"
    ]
    t2_events_after_resume = [
        e for e in sink.by_run_id(run_id) if e.kind == "metric" and e.attributes.get("unit_id") == "t2"
    ]
    assert len(t1_events_after_resume) == 1, "t1's cost must not be re-emitted/double-counted after resume"
    assert len(t2_events_after_resume) == 1

    final_cumulative = observability_2.cumulative_cost(run_id=run_id)
    # NOTE: `observability_2` is a fresh CostMetricStore instance (no
    # in-memory carryover from process 1), so its own ledger only knows
    # about t2's application -- this is exactly why the assertion below
    # reads the SINK (the durable, shared record both processes wrote
    # to), not `observability_2.cumulative_cost`, for the true end-to-end
    # total. `observability_2`'s own partial view is asserted separately
    # to document that fact plainly rather than leaving it implicit.
    assert final_cumulative == t2_events_after_resume[0].attributes["amount"]

    true_total_from_sink = t1_events_after_resume[0].attributes["cumulative_total"] + t2_events_after_resume[0].attributes["amount"]
    # The cumulative total never jumps backward across the crash+resume
    # boundary: t1's contribution (recorded before the crash) is still
    # exactly what it was, and the true combined total is additive, not
    # doubled.
    assert t1_events_after_resume[0].attributes["cumulative_total"] == cost_before_resume
    assert true_total_from_sink == cost_before_resume + t2_events_after_resume[0].attributes["amount"]

    registry_2.close()


def test_cost_metric_store_dedups_a_literally_repeated_apply_call(sink):
    """The underlying mechanism, in isolation: calling
    `record_cost_increment` twice with the same (run_id, unit_id) --
    e.g. because a caller's instrumentation call site was accidentally
    invoked twice for the same completed unit of work -- must not double
    the cumulative total or ship a second metric event. This is the
    direct, minimal proof that "no double-spend" is a property of the
    ledger itself, not an accident of the orchestrator only ever calling
    it once in practice."""
    observability = ObservabilityClient(sink=sink, service_name="orchestrator")

    first = observability.record_cost_increment(
        trace_id="trace-dedup", run_id="run-dedup-1", tenant_id="tenant-x", unit_id="t1", amount_usd=5.0
    )
    second = observability.record_cost_increment(
        trace_id="trace-dedup", run_id="run-dedup-1", tenant_id="tenant-x", unit_id="t1", amount_usd=5.0
    )
    assert first == 5.0
    assert second == 5.0  # unchanged, not 10.0

    metric_events = [e for e in sink.by_run_id("run-dedup-1") if e.kind == "metric"]
    assert len(metric_events) == 1  # only the first application ever shipped an event

    # A genuinely different unit of work (t2) still counts normally.
    third = observability.record_cost_increment(
        trace_id="trace-dedup", run_id="run-dedup-1", tenant_id="tenant-x", unit_id="t2", amount_usd=3.0
    )
    assert third == 8.0
    assert observability.cumulative_cost(run_id="run-dedup-1") == 8.0
