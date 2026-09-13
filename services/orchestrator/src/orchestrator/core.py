"""The nine-stage workflow state machine (master spec Section 5), driven
against F2's real `RegistryService` -- this is the actual orchestrator.

Design note on where "paused, input required" durably lives (flagged for
human review, same discipline as `run_registry.stages`'s own flagged
assumptions): the two human *gates* (plan_approval_gate,
change_review_gate) are first-class stages in F2's fixed vocabulary, so a
gate pause is durable for free -- the paused state IS the Run's `stage`
column. Section 9.3's four *internal* checkpoints (size/risk/time-cost/
stuck) fire mid-stage (typically mid-`implementation`), where the stage
vocabulary has no dedicated "paused" token to move to; F2's schema does,
however, reserve exactly one field for this purpose per its own
migration comment ("Capacity class + checkpoint pointer (spec Section
14.12, 14.8, 9.3)"): `checkpoint_pointer`. This module uses it to hold a
JSON-encoded structured elicitation record (`Elicitation`) -- the Run's
`stage` does not change, but `checkpoint_pointer` durably carries "there
is a pending question, here is its full structured content" through a
process restart, exactly like the gate case. This is this
implementation's own reasonable reading of a field the schema names but
does not fully specify the payload shape of; a human should confirm it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from run_registry import RegistryService, Run
from run_registry import stages as rr_stages

from orchestrator.checkpoints import DEFAULT_BUDGETS, DiffStats, evaluate_checkpoints, resolve_budget
from orchestrator.model_backend import AgentBackend, PlanOutput
from orchestrator.plan_artifact import PlanArtifactStore, generate_plan_artifact
from orchestrator.progress import RunProgress, RunProgressStore
from orchestrator.skills import ToolInvoker
from orchestrator.verification import VerificationRunner

PENDING = "pending"
RESOLVED = "resolved"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Elicitation:
    """Section 9.3's "structured elicitation": an 'input required' result,
    not a lost chat interruption. Serialized into `Run.checkpoint_pointer`
    so it survives a process restart -- see module docstring."""

    checkpoint_id: str
    trigger: str  # "size" | "risk" | "time_cost" | "stuck"
    stage: str
    reason: str
    details: dict[str, Any]
    created_at: str
    status: str  # "pending" | "resolved"
    signature: str = ""
    resolution: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "checkpoint_id": self.checkpoint_id,
                "trigger": self.trigger,
                "stage": self.stage,
                "reason": self.reason,
                "details": self.details,
                "created_at": self.created_at,
                "status": self.status,
                "signature": self.signature,
                "resolution": self.resolution,
            }
        )

    @staticmethod
    def from_json(text: str) -> "Elicitation":
        d = json.loads(text)
        return Elicitation(**d)


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    stage: str
    version: int
    paused: bool
    pause_kind: str | None  # "gate" | "checkpoint" | None
    checkpoint: Elicitation | None = None


class OrchestratorError(Exception):
    pass


class Orchestrator:
    """Owns exactly one tenant's view of the world (Section 8.6: never
    spans two tenants). Stateless across process restarts by design --
    everything it needs to resume lives in `registry`, `plan_store`, and
    `progress_store`; see `tests/test_durable_resume.py`."""

    def __init__(
        self,
        *,
        registry: RegistryService,
        tenant_id: str,
        agent_backend: AgentBackend,
        verification_runner: VerificationRunner,
        plan_store: PlanArtifactStore,
        progress_store: RunProgressStore,
        tool_invoker: ToolInvoker | None = None,
        research_fn: Callable[["Orchestrator", Run, ToolInvoker | None], None] | None = None,
        packaging_fn: Callable[["Orchestrator", Run, ToolInvoker | None], None] | None = None,
        budgets: dict = DEFAULT_BUDGETS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        observability: Any | None = None,
    ) -> None:
        self.registry = registry
        self.tenant_id = tenant_id
        self.agent_backend = agent_backend
        self.verification_runner = verification_runner
        self.plan_store = plan_store
        self.progress_store = progress_store
        self.tool_invoker = tool_invoker
        self._research_fn = research_fn
        self._packaging_fn = packaging_fn
        self.budgets = budgets
        self._clock = clock
        # D11 instrumentation (purely additive, defaults to None): an
        # `observability.ObservabilityClient`-shaped object. When
        # supplied, `_transition` ships a real agent-level stage-transition
        # trace event tagged with the run's own F2 `trace_id` (Section
        # 16.1's shared distributed-tracing key), `_implementation_step`
        # ships an idempotent per-subtask cost metric (Section 16.1
        # durable-execution: never re-counted for an already-completed
        # subtask across a resume), and both `_implementation_step`/
        # `_verification_step` push every crossed Section 9.3 checkpoint
        # trigger to the alerting seam (Section 16.4: "alerting, not
        # babysitting"), not only the one that pauses the run. Every
        # existing call site that constructs an `Orchestrator` without
        # this argument is completely unaffected.
        self.observability = observability

    # ------------------------------------------------------------------
    # Registry helpers
    # ------------------------------------------------------------------
    def _get_run(self, run_id: str) -> Run:
        res = self.registry.get_run(tenant_id=self.tenant_id, run_id=run_id)
        if not res.is_ok:
            raise OrchestratorError(f"no such run {run_id!r} for tenant {self.tenant_id!r}")
        return res.data

    def _transition(self, run: Run, next_stage: str) -> Run:
        res = self.registry.transition_stage(
            tenant_id=self.tenant_id, run_id=run.id, expected_version=run.version, next_stage=next_stage
        )
        if not res.is_ok:
            raise OrchestratorError(f"stage transition {run.stage} -> {next_stage} failed: {res.error}")
        updated = res.data
        # D11 instrumentation: ship the agent-level stage-transition trace
        # event AFTER the write has actually landed in F2's Registry (never
        # before -- the shipped event's `to_stage` must reflect a real,
        # durable transition, not an attempted one). See __init__ docstring.
        if self.observability is not None:
            self.observability.record_stage_transition(
                trace_id=updated.trace_id,
                run_id=updated.id,
                tenant_id=self.tenant_id,
                from_stage=run.stage,
                to_stage=next_stage,
            )
        return updated

    def _write_checkpoint_pointer(self, run: Run, pointer: str) -> Run:
        res = self.registry.write_checkpoint(
            tenant_id=self.tenant_id, run_id=run.id, expected_version=run.version, checkpoint_pointer=pointer
        )
        if not res.is_ok:
            raise OrchestratorError(f"write_checkpoint failed: {res.error}")
        return res.data

    def _pending_checkpoint(self, run: Run) -> Elicitation | None:
        if not run.checkpoint_pointer:
            return None
        elicitation = Elicitation.from_json(run.checkpoint_pointer)
        return elicitation if elicitation.status == PENDING else None

    def _status(self, run: Run, *, paused: bool, pause_kind: str | None, checkpoint: Elicitation | None = None) -> RunStatus:
        return RunStatus(
            run_id=run.id, stage=run.stage, version=run.version, paused=paused, pause_kind=pause_kind, checkpoint=checkpoint
        )

    # ------------------------------------------------------------------
    # Plan artifact helpers
    # ------------------------------------------------------------------
    def _generate_and_store_plan(self, run: Run, plan_output: PlanOutput, *, human_plan_text: str) -> dict:
        budget = resolve_budget(
            story_size=plan_output.story_size,
            cross_cutting_or_high_risk=plan_output.cross_cutting_or_high_risk,
            budgets=self.budgets,
        )
        next_version = self.plan_store.latest_version_number(run.id) + 1
        artifact = generate_plan_artifact(
            run_id=run.id,
            plan_version=next_version,
            plan_output=plan_output,
            budget=budget,
            human_plan_text=human_plan_text,
        )
        self.plan_store.save(artifact)
        return artifact

    def plan_artifact(self, run_id: str) -> dict | None:
        return self.plan_store.load_latest(run_id)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def start_run(
        self, *, jira_key: str, repo: str, branch: str, trace_id: str, capacity_class: str = "on_demand"
    ) -> RunStatus:
        res = self.registry.create_run(
            tenant_id=self.tenant_id,
            jira_key=jira_key,
            repo=repo,
            branch=branch,
            capacity_class=capacity_class,
            trace_id=trace_id,
        )
        if not res.is_ok:
            raise OrchestratorError(f"create_run failed: {res.error}")
        return self._drive(res.data.id)

    def resume_run(self, run_id: str) -> RunStatus:
        """The durable-execution entry point: reconstructs everything
        needed to keep driving a run purely from what's on disk
        (`registry`, `plan_store`, `progress_store`) -- no in-memory
        state from a prior `Orchestrator` instance is consulted or
        required. See `tests/test_durable_resume.py`."""
        return self._drive(run_id)

    def approve_plan(self, run_id: str, *, decision: str, feedback: str | None = None) -> RunStatus:
        run = self._get_run(run_id)
        if run.stage != rr_stages.PLAN_APPROVAL_GATE:
            raise OrchestratorError(f"approve_plan called but run is at stage '{run.stage}', not the plan gate")

        if decision == "approve":
            run = self._transition(run, rr_stages.IMPLEMENTATION)
            self.progress_store.start_new(run_id=run.id, attempt_id=run.current_attempt_id or "")
            return self._drive(run.id)

        if decision == "request_changes":
            attempt_res = self.registry.append_attempt(
                tenant_id=self.tenant_id,
                run_id=run.id,
                expected_version=run.version,
                reason="re-plan",
                starting_stage=rr_stages.PLAN_AUTHORING,
                trace_id=run.trace_id,
            )
            if not attempt_res.is_ok:
                raise OrchestratorError(f"append_attempt (re-plan) failed: {attempt_res.error}")
            run = self._get_run(run.id)
            plan_output = self.agent_backend.re_plan(run_context=self._run_context(run), feedback=feedback or "")
            self._generate_and_store_plan(run, plan_output, human_plan_text=f"re-plan: {feedback or ''}")
            run = self._transition(run, rr_stages.PLAN_APPROVAL_GATE)
            return self._status(run, paused=True, pause_kind="gate")

        if decision == "reject":
            run = self._transition(run, rr_stages.ABANDONED)
            return self._status(run, paused=False, pause_kind=None)

        raise OrchestratorError(f"unknown plan-gate decision {decision!r}")

    def approve_change_review(self, run_id: str, *, decision: str, feedback: str | None = None) -> RunStatus:
        run = self._get_run(run_id)
        if run.stage != rr_stages.CHANGE_REVIEW_GATE:
            raise OrchestratorError(f"approve_change_review called but run is at stage '{run.stage}'")

        if decision == "approve":
            run = self._transition(run, rr_stages.PACKAGING)
            return self._drive(run.id)
        if decision == "request_changes":
            run = self._transition(run, rr_stages.IMPLEMENTATION)
            return self._drive(run.id)
        if decision == "reject":
            run = self._transition(run, rr_stages.ABANDONED)
            return self._status(run, paused=False, pause_kind=None)
        raise OrchestratorError(f"unknown change-review decision {decision!r}")

    def resolve_checkpoint(self, run_id: str, *, decision: str, note: str | None = None) -> RunStatus:
        """Answers a pending Section 9.3 elicitation. `decision` is one of
        "continue" (proceed as-is), "extend_scope" (widen the plan
        artifact's declared scope to cover a risk-checkpoint's
        out-of-scope files and proceed), or "stop" (abandon the run)."""
        run = self._get_run(run_id)
        pending = self._pending_checkpoint(run)
        if pending is None:
            raise OrchestratorError(f"run {run_id!r} has no pending checkpoint to resolve")

        resolved = Elicitation(
            checkpoint_id=pending.checkpoint_id,
            trigger=pending.trigger,
            stage=pending.stage,
            reason=pending.reason,
            details=pending.details,
            created_at=pending.created_at,
            status=RESOLVED,
            signature=pending.signature,
            resolution={"decision": decision, "note": note, "resolved_at": _now_iso()},
        )
        run = self._write_checkpoint_pointer(run, resolved.to_json())

        if decision == "stop":
            run = self._transition(run, rr_stages.ABANDONED)
            return self._status(run, paused=False, pause_kind=None)

        # Record this exact violation as acknowledged so the next drive
        # step doesn't immediately re-pause on the identical, already-
        # answered condition (see checkpoints.CheckpointTrigger.signature).
        progress = self.progress_store.load(run.id)
        if progress is not None and pending.signature:
            if pending.signature not in progress.acknowledged_checkpoint_signatures:
                progress.acknowledged_checkpoint_signatures.append(pending.signature)
            self.progress_store.save(progress)

        if decision == "extend_scope" and pending.trigger == "risk":
            self._extend_declared_scope(run, pending.details.get("out_of_scope_files", []))

        return self._drive(run.id)

    def _extend_declared_scope(self, run: Run, extra_files: list[str]) -> None:
        artifact = self.plan_store.load_latest(run.id)
        if artifact is None:
            raise OrchestratorError(f"no plan artifact for run {run.id!r} to extend scope on")
        new_in_scope = sorted(set(artifact["declared_scope"]["in_scope"]) | set(extra_files))
        next_version = artifact["plan_version"] + 1
        # Regenerated, not hand-patched (Section 9.5): rebuild the full
        # artifact via generate_plan_artifact-shaped data, bumping only
        # declared_scope -- everything else about the plan is unchanged,
        # which is the whole point of "extend scope" as a distinct,
        # logged action rather than a silent drift.
        artifact = dict(artifact)
        artifact["declared_scope"] = {"in_scope": new_in_scope, "out_of_scope": artifact["declared_scope"]["out_of_scope"]}
        artifact["plan_version"] = next_version
        artifact["generated_at"] = _now_iso()
        self.plan_store.save(artifact)

    def _run_context(self, run: Run) -> dict:
        # story_size added (additive -- every existing key/consumer is
        # unchanged) so an AgentBackend can size its own internal
        # resource budgets (e.g. BedrockToolUseAgentBackend's per-subtask
        # tool-calling turn cap) against the plan's own declared Sec. 9.4
        # budget instead of one flat constant for every size. None before
        # a plan exists yet (PLAN_AUTHORING) or if story_size is missing.
        artifact = self.plan_store.load_latest(run.id) or {}
        story_size = artifact.get("risk", {}).get("story_size")
        return {
            "run_id": run.id,
            "tenant_id": run.tenant_id,
            "jira_key": run.jira_key,
            "repo": run.repo,
            "stage": run.stage,
            "story_size": story_size,
        }

    # ------------------------------------------------------------------
    # The drive loop
    # ------------------------------------------------------------------
    def _drive(self, run_id: str) -> RunStatus:
        run = self._get_run(run_id)

        while True:
            pending = self._pending_checkpoint(run)
            if pending is not None:
                return self._status(run, paused=True, pause_kind="checkpoint", checkpoint=pending)

            stage = run.stage

            if stage == rr_stages.INTAKE:
                run = self._transition(run, rr_stages.RESEARCH)
                continue

            if stage == rr_stages.RESEARCH:
                if self._research_fn is not None:
                    self._research_fn(self, run, self.tool_invoker)
                run = self._transition(run, rr_stages.PLAN_AUTHORING)
                continue

            if stage == rr_stages.PLAN_AUTHORING:
                plan_output = self.agent_backend.author_plan(run_context=self._run_context(run))
                self._generate_and_store_plan(run, plan_output, human_plan_text=f"plan for {run.jira_key}")
                run = self._transition(run, rr_stages.PLAN_APPROVAL_GATE)
                return self._status(run, paused=True, pause_kind="gate")

            if stage == rr_stages.PLAN_APPROVAL_GATE:
                return self._status(run, paused=True, pause_kind="gate")

            if stage == rr_stages.IMPLEMENTATION:
                outcome = self._implementation_step(run)
                run = self._get_run(run.id)
                if outcome == "paused":
                    continue
                if outcome == "verify":
                    # Real bug this closes: found on a real live run
                    # driven by two concurrent `jira_poll_run.py`
                    # invocations racing to resume the same run (no
                    # process-level lock existed yet -- see
                    # `deploy/run-worker/_run_lib.py`'s new lock file
                    # for the other half of this fix). By the time this
                    # line ran, a concurrent process had already moved
                    # `run.stage` to VERIFICATION for the exact same
                    # subtask/fix-up outcome, and the unconditional
                    # `_transition` below raised a real
                    # `OrchestratorError` ("'verification' is not a
                    # legal next stage from 'verification'") --
                    # `run_registry.stages`'s transition graph correctly
                    # has no VERIFICATION -> VERIFICATION edge (a
                    # transition must always be a real move). Only
                    # transition if this run isn't already there.
                    if run.stage != rr_stages.VERIFICATION:
                        run = self._transition(run, rr_stages.VERIFICATION)
                continue

            if stage == rr_stages.VERIFICATION:
                outcome = self._verification_step(run)
                run = self._get_run(run.id)
                if outcome == "passed":
                    run = self._transition(run, rr_stages.CHANGE_REVIEW_GATE)
                    return self._status(run, paused=True, pause_kind="gate")
                if outcome == "retry":
                    run = self._transition(run, rr_stages.IMPLEMENTATION)
                    continue
                if outcome == "stuck":
                    continue

            if stage == rr_stages.CHANGE_REVIEW_GATE:
                return self._status(run, paused=True, pause_kind="gate")

            if stage == rr_stages.PACKAGING:
                if self._packaging_fn is not None:
                    self._packaging_fn(self, run, self.tool_invoker)
                run = self._transition(run, rr_stages.RETROSPECTIVE)
                continue

            if stage == rr_stages.RETROSPECTIVE:
                run = self._transition(run, rr_stages.COMPLETED)
                return self._status(run, paused=False, pause_kind=None)

            if stage in rr_stages.TERMINAL_STAGES:
                return self._status(run, paused=False, pause_kind=None)

            raise OrchestratorError(f"drive loop has no handler for stage {stage!r}")

    # ------------------------------------------------------------------
    # Implementation stage: one subtask per call, checkpoint-checked
    # ------------------------------------------------------------------
    def _next_subtask(self, artifact: dict, progress: RunProgress) -> dict | None:
        done = set(progress.completed_subtask_ids)
        for st in artifact["subtask_graph"]["subtasks"]:
            if st["task_id"] in done:
                continue
            if all(dep in done for dep in st["depends_on"]):
                return st
        return None

    def _implementation_step(self, run: Run) -> str:
        artifact = self.plan_store.load_latest(run.id)
        if artifact is None:
            raise OrchestratorError(f"no plan artifact for run {run.id!r}; cannot implement")
        progress = self.progress_store.load(run.id)
        if progress is None:
            progress = self.progress_store.start_new(run_id=run.id, attempt_id=run.current_attempt_id or "")

        from orchestrator.model_backend import SubTask  # local import: avoid cycle at module load

        subtask = self._next_subtask(artifact, progress)
        if subtask is None:
            if not progress.last_verification_failure_summary:
                return "verify"  # every subtask done, no pending failure to address
            # (New, Rev 9 real-live-run fix) Every plan subtask is complete,
            # but we're here because VERIFICATION failed and _drive routed
            # back to IMPLEMENTATION for a retry (Sec. 9.3's bounded retry
            # budget) -- with no unfinished subtask, this is the one real
            # chance for the agent to see *why* it failed and fix it, rather
            # than the run silently re-verifying the same unfixed code.
            # Ephemeral by design: not added to completed_subtask_ids (it has
            # no entry in the plan's own subtask_graph to be "done" against),
            # so it never confuses _next_subtask's real-subtask bookkeeping.
            fixup_subtask = SubTask(
                task_id=f"verification-fixup-{uuid4().hex[:8]}",
                description=(
                    "The previous implementation failed verification. Fix the "
                    f"issue(s) described below and ensure the code is correct:\n\n"
                    f"{progress.last_verification_failure_summary}"
                ),
                parallel_group=None,
            )
            diff = self.agent_backend.implement_subtask(run_context=self._run_context(run), subtask=fixup_subtask)
            progress.files_touched = sorted(set(progress.files_touched) | set(diff.files_touched))
            progress.lines_changed += diff.lines_changed
            progress.last_verification_failure_summary = ""
            self.progress_store.save(progress)
            return "verify"

        diff = self.agent_backend.implement_subtask(
            run_context=self._run_context(run),
            subtask=SubTask(
                task_id=subtask["task_id"],
                description=subtask["description"],
                parallel_group=subtask["parallel_group"],
                depends_on=tuple(subtask["depends_on"]),
                interface_contract=subtask["interface_contract"],
            ),
        )

        progress.completed_subtask_ids.append(subtask["task_id"])
        progress.files_touched = sorted(set(progress.files_touched) | set(diff.files_touched))
        progress.lines_changed += diff.lines_changed
        self.progress_store.save(progress)

        # D11 instrumentation: ship an idempotent per-subtask cost metric
        # (Section 16.1 durable execution, "no double-spend"). Keyed by
        # `unit_id=subtask["task_id"]` -- the exact same durable identity
        # `progress.completed_subtask_ids` already uses to make sure a
        # resumed run never redoes this subtask, so a resume can call this
        # again with the same subtask id (it never legitimately will, since
        # `_next_subtask` skips completed ids -- see below) and it would
        # still be a correctly-deduplicated no-op via
        # `CostMetricStore.apply`'s idempotency-key check, not merely "in
        # practice never re-called."
        if self.observability is not None:
            self.observability.record_cost_increment(
                trace_id=run.trace_id,
                run_id=run.id,
                tenant_id=self.tenant_id,
                unit_id=subtask["task_id"],
                amount_usd=round(diff.lines_changed * 0.01, 6),
            )

        budget = resolve_budget(
            story_size=artifact["risk"]["story_size"],
            cross_cutting_or_high_risk=artifact["risk"]["cross_cutting_or_high_risk"],
            budgets=self.budgets,
        )
        diff_stats = DiffStats(files_touched=tuple(progress.files_touched), lines_changed=progress.lines_changed)
        declared_in_scope = tuple(artifact["declared_scope"]["in_scope"])
        triggers = evaluate_checkpoints(
            diff_stats=diff_stats,
            declared_scope_in=declared_in_scope,
            budget=budget,
            elapsed_minutes=progress.elapsed_minutes(now=self._clock()),
            spend_usd=progress.spend_usd,
            consecutive_same_stage_failures=progress.consecutive_same_stage_failures,
        )
        triggers = [t for t in triggers if t.signature() not in progress.acknowledged_checkpoint_signatures]
        self._alert_on_triggers(run, triggers)
        if triggers:
            trigger = triggers[0]
            elicitation = Elicitation(
                checkpoint_id=str(uuid4()),
                trigger=trigger.kind,
                stage=run.stage,
                reason=trigger.reason,
                details=trigger.details,
                created_at=_now_iso(),
                status=PENDING,
                signature=trigger.signature(),
            )
            self._write_checkpoint_pointer(run, elicitation.to_json())
            return "paused"

        return "continue" if self._next_subtask(artifact, progress) is not None else "verify"

    def _alert_on_triggers(self, run: Run, triggers: list) -> None:
        """D11 instrumentation (Section 16.4 "alerting, not babysitting"):
        push every newly-crossed Section 9.3 checkpoint trigger to the
        alerting seam, not only whichever one becomes the pausing
        Elicitation (`triggers[0]`) -- a "budget threshold crossed" or
        "stuck" condition is alert-worthy even on a step where a
        different trigger kind happens to be the one presented to a
        human first. A no-op when no observability client is wired in."""
        if self.observability is None:
            return
        for trigger in triggers:
            self.observability.push_alert(
                kind=f"checkpoint_{trigger.kind}",
                message=trigger.reason,
                run_id=run.id,
                tenant_id=self.tenant_id,
                trace_id=run.trace_id,
                **{k: str(v) for k, v in trigger.details.items()},
            )

    def _verification_step(self, run: Run) -> str:
        result = self.verification_runner.run(run_context=self._run_context(run))
        progress = self.progress_store.load(run.id)
        if progress is None:
            progress = self.progress_store.start_new(run_id=run.id, attempt_id=run.current_attempt_id or "")

        if result.passed:
            progress.consecutive_same_stage_failures = 0
            progress.last_verification_failure_summary = ""
            self.progress_store.save(progress)
            return "passed"

        progress.consecutive_same_stage_failures += 1
        # (New, Rev 9 real-live-run fix) Recorded so _implementation_step can
        # give the agent a real chance to fix this specific failure on the
        # next pass through IMPLEMENTATION -- see that method's own comment.
        progress.last_verification_failure_summary = result.summary
        self.progress_store.save(progress)

        triggers = evaluate_checkpoints(
            diff_stats=DiffStats(files_touched=tuple(progress.files_touched), lines_changed=progress.lines_changed),
            declared_scope_in=tuple((self.plan_store.load_latest(run.id) or {}).get("declared_scope", {}).get("in_scope", [])),
            budget=resolve_budget(
                story_size=(self.plan_store.load_latest(run.id) or {})["risk"]["story_size"],
                cross_cutting_or_high_risk=(self.plan_store.load_latest(run.id) or {})["risk"]["cross_cutting_or_high_risk"],
                budgets=self.budgets,
            ),
            elapsed_minutes=progress.elapsed_minutes(now=self._clock()),
            spend_usd=progress.spend_usd,
            consecutive_same_stage_failures=progress.consecutive_same_stage_failures,
        )
        triggers = [t for t in triggers if t.signature() not in progress.acknowledged_checkpoint_signatures]
        self._alert_on_triggers(run, triggers)
        stuck = next((t for t in triggers if t.kind == "stuck"), None)
        if stuck is not None:
            elicitation = Elicitation(
                checkpoint_id=str(uuid4()),
                trigger="stuck",
                stage=run.stage,
                reason=stuck.reason,
                details=stuck.details,
                created_at=_now_iso(),
                status=PENDING,
                signature=stuck.signature(),
            )
            self._write_checkpoint_pointer(run, elicitation.to_json())
            return "stuck"

        return "retry"
