"""The interruption watcher sequence (spec Section 14.8).

On receiving an interruption warning, the watcher runs a fixed five-step
sequence, in order, before the reclaim actually lands:

  1. cordon the node (no new work scheduled to it)
  2. tell the model-serving endpoint to stop accepting new inference
     requests
  3. let in-flight requests complete, or fail back to the orchestrator's
     own retry logic, within what's left of the warning window
  4. flush buffered logs/metrics/traces to the off-node observability
     store
  5. force an immediate checkpoint write of run state, via F2's real
     `RegistryService`, before SIGTERM/reclaim lands

Steps 1-4 go through the `ProvisioningClient` boundary (see
`provisioning_client.py` for exactly what's real vs. mocked about that).
Step 5 goes through `run_registry.RegistryService` for real -- this
module imports and calls the actual F2 service, not a stand-in, per the
brief ("force checkpoint write via `run_registry`'s real
RegistryService").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from run_registry import RegistryService, Run

from tenant_cell.clock import Clock
from tenant_cell.provisioning_client import DrainResult, NodeHandle, ProvisioningClient


class Step(str, Enum):
    CORDONING = "cordoning"
    STOPPING_INFERENCE = "stopping_inference"
    DRAINING = "draining"
    FLUSHING = "flushing"
    CHECKPOINTING = "checkpointing"


@dataclass(frozen=True)
class StepTiming:
    step: Step
    started_at: float
    ended_at: float

    @property
    def duration_seconds(self) -> float:
        return self.ended_at - self.started_at


@dataclass(frozen=True)
class InterruptionResult:
    node: NodeHandle
    run_id: str
    steps: tuple[StepTiming, ...]
    drain_result: DrainResult
    flush_ok: bool
    checkpoint_landed: bool
    checkpoint_pointer: str | None
    failure_reason: str | None
    started_at: float
    ended_at: float

    @property
    def total_elapsed_seconds(self) -> float:
        return self.ended_at - self.started_at

    @property
    def succeeded(self) -> bool:
        """The full sequence ran to completion and the checkpoint landed.

        Note this is independent of `completed_within_window` below --
        the sequence can (and by construction of step 3 always does)
        finish deterministically, but whether it finished *inside* the
        warning window is a separate, explicitly-checked fact, not an
        assumption.
        """
        return self.checkpoint_landed and self.failure_reason is None

    def completed_within_window(self, warning_window_seconds: float) -> bool:
        return self.total_elapsed_seconds <= warning_window_seconds


class InterruptionWatcher:
    """Drives the five-step interruption sequence against a
    `ProvisioningClient` and F2's real `RegistryService`.
    """

    def __init__(
        self,
        client: ProvisioningClient,
        registry: RegistryService,
        clock: Clock,
        *,
        observability: Any | None = None,
    ):
        self._client = client
        self._registry = registry
        self._clock = clock
        # D11 instrumentation (purely additive, defaults to None): an
        # `observability.ObservabilityClient`-shaped object. When
        # supplied, step 4 (FLUSHING) and step 5 (CHECKPOINTING) below
        # each ship a real, off-node-first infra-level trace event tagged
        # with the run's F2 `trace_id` (the same shared key
        # `HookChain`/`Orchestrator`'s agent-level events use per Section
        # 16.1), letting a test (or a real trace viewer) prove the flush
        # event reached the off-node store strictly before the checkpoint
        # event did -- see `tests/test_interruption_flush_before_checkpoint_ordering.py`
        # in the observability package. Every existing call site that
        # constructs an `InterruptionWatcher` without this argument is
        # completely unaffected.
        self._observability = observability

    def handle_interruption(
        self,
        *,
        node: NodeHandle,
        tenant_id: str,
        run_id: str,
        expected_version: int,
        checkpoint_pointer: str,
        warning_window_seconds: float = 120.0,
    ) -> InterruptionResult:
        started_at = self._clock.now()
        steps: list[StepTiming] = []

        # D11 instrumentation: resolve the run's shared trace_id (F2's
        # Registry `Run.trace_id`, the same value the orchestrator's
        # agent-level events are tagged with) once, up front -- a plain
        # read through the real, tenant-scoped `RegistryService.get_run`,
        # same discipline as every other registry access in this module.
        # This lookup is deliberately outside `timed()`: it is metadata
        # resolution, not one of the five sequence steps being timed, and
        # a no-op (falls back to `run_id` as its own trace key) whenever
        # no observability client was supplied.
        trace_key = run_id
        if self._observability is not None:
            existing = self._registry.get_run(tenant_id=tenant_id, run_id=run_id)
            if existing.is_ok:
                trace_key = existing.data.trace_id

        def timed(step: Step, fn):
            s = self._clock.now()
            result = fn()
            e = self._clock.now()
            steps.append(StepTiming(step=step, started_at=s, ended_at=e))
            return result

        # Step 1: cordon.
        timed(Step.CORDONING, lambda: self._client.cordon(node))

        # Step 2: stop accepting new inference requests.
        timed(Step.STOPPING_INFERENCE, lambda: self._client.stop_accepting_inference(node))

        # Step 3: drain in-flight requests, or fail them back to retry,
        # within whatever remains of the warning window.
        remaining_before_drain = max(0.0, warning_window_seconds - (self._clock.now() - started_at))
        drain_result = timed(
            Step.DRAINING,
            lambda: self._client.drain(node, budget_seconds=remaining_before_drain),
        )

        # Step 4: flush observability off-node -- happens regardless of
        # whether step 3 fully drained or partially failed-to-retry; the
        # run's record must be complete off-node before anything else
        # (spec Section 14.8 step 4), even for the in-flight requests
        # that got converted to a retry.
        flush_ok = timed(Step.FLUSHING, lambda: self._client.flush_observability(node))

        # D11 instrumentation: ship the "observability flush happened"
        # infra-level event to the real off-node store THE INSTANT the
        # flush step completes -- strictly before step 5's checkpoint
        # write below is even attempted. This is the concrete mechanism
        # the interruption-safe-flushing acceptance criterion checks:
        # the sink's own arrival-order/timestamp bookkeeping (not this
        # process's in-memory state) proves this event landed first.
        if self._observability is not None:
            self._observability.record_infra_event(
                trace_id=trace_key,
                run_id=run_id,
                tenant_id=tenant_id,
                kind="observability_flush",
                node_id=node.node_id,
                flush_ok=flush_ok,
            )

        # Step 5: force the checkpoint write via the real RegistryService,
        # before the reclaim lands -- this always runs, even if step 4's
        # flush reported failure, because losing the run's durable
        # checkpoint is strictly worse than losing some already-flushed
        # observability data.
        checkpoint_result: dict[str, Run | None] = {}

        def do_checkpoint() -> Run | None:
            result = self._registry.write_checkpoint(
                tenant_id=tenant_id,
                run_id=run_id,
                expected_version=expected_version,
                checkpoint_pointer=checkpoint_pointer,
            )
            checkpoint_result["result"] = result
            return result.data if result.is_ok else None

        run_after_checkpoint = timed(Step.CHECKPOINTING, do_checkpoint)

        registry_result = checkpoint_result["result"]
        checkpoint_landed = registry_result is not None and registry_result.is_ok

        # D11 instrumentation: ship the "checkpoint write" infra-level
        # event immediately after the checkpoint attempt resolves --
        # always strictly after the flush event shipped above, since this
        # line cannot execute until `timed(Step.CHECKPOINTING, ...)` above
        # (which itself runs after the flush step) returns.
        if self._observability is not None:
            self._observability.record_infra_event(
                trace_id=trace_key,
                run_id=run_id,
                tenant_id=tenant_id,
                kind="checkpoint_write",
                node_id=node.node_id,
                checkpoint_landed=checkpoint_landed,
            )

        failure_reason = None
        if not checkpoint_landed:
            assert registry_result is not None
            code = registry_result.error.code if registry_result.error else None
            failure_reason = (
                f"checkpoint write failed: {code.value if code else 'unknown'} - "
                f"{registry_result.error.message if registry_result.error else ''}"
            )
        elif not flush_ok:
            failure_reason = "observability flush reported failure before checkpoint write"

        ended_at = self._clock.now()
        return InterruptionResult(
            node=node,
            run_id=run_id,
            steps=tuple(steps),
            drain_result=drain_result,
            flush_ok=flush_ok,
            checkpoint_landed=checkpoint_landed,
            checkpoint_pointer=checkpoint_pointer if checkpoint_landed else None,
            failure_reason=failure_reason,
            started_at=started_at,
            ended_at=ended_at,
        )
