"""Cumulative cost/time metrics with idempotent (no-double-spend)
recording (master spec Section 16.1's durable-execution requirement,
applied to telemetry specifically: "a resumed run's cumulative cost/time
metric doesn't jump backward or double-count the pre-restart portion").

Why this is not just "sum every metric event in the sink": a container
restart that resumes a run must not re-emit telemetry for
already-completed budget even if the instrumented call site that ships a
metric event is, for whatever reason, invoked twice for the same unit of
work (e.g. a retried call after a transient failure in the shipping path
itself). `CostMetricStore` is keyed by an explicit idempotency key
(this System's own instrumentation always derives it as
`f"{run_id}:{unit_id}:{metric_name}"`, e.g. one key per completed
subtask) and a second `apply()` call with an already-seen key is a
provable no-op: the cumulative total does not move, and no second
"metric" event reaches the sink.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from observability.sink import EventSink


@dataclass
class _RunLedger:
    cumulative: dict[str, float] = field(default_factory=dict)  # metric_name -> total
    applied_keys: set[str] = field(default_factory=set)


class CostMetricStore:
    """Real, in-process (per-`CostMetricStore` instance) idempotent
    accumulator. A real deployment persists `applied_keys`/`cumulative`
    the same durable way the rest of this System does (e.g. a row per
    run in F2's Registry, or the Prometheus counter itself, which is
    monotonic by construction) -- this store's job here is to prove the
    *semantics* are correct (dedup by key, monotonic, resume-safe), which
    is independent of where the ledger physically lives.
    """

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._lock = threading.Lock()
        self._ledgers: dict[str, _RunLedger] = {}

    def apply(
        self,
        *,
        run_id: str,
        metric_name: str,
        amount: float,
        idempotency_key: str,
        trace_id: str | None = None,
        tenant_id: str | None = None,
        **extra_attributes,
    ) -> float:
        """Apply `amount` to `run_id`'s cumulative `metric_name` exactly
        once per distinct `idempotency_key`, shipping a "metric" event to
        the sink for every genuinely-new application (never for a
        duplicate). Returns the cumulative total after this call
        (unchanged from before, if this was a duplicate)."""
        with self._lock:
            ledger = self._ledgers.setdefault(run_id, _RunLedger())
            if idempotency_key in ledger.applied_keys:
                # Already counted -- this is the no-double-spend guarantee
                # itself. No sink event, no cumulative change.
                return ledger.cumulative.get(metric_name, 0.0)
            ledger.applied_keys.add(idempotency_key)
            new_total = ledger.cumulative.get(metric_name, 0.0) + amount
            ledger.cumulative[metric_name] = new_total

        self._sink.emit(
            kind="metric",
            name=metric_name,
            trace_id=trace_id,
            run_id=run_id,
            tenant_id=tenant_id,
            attributes={
                "amount": amount,
                "cumulative_total": new_total,
                "idempotency_key": idempotency_key,
                **extra_attributes,
            },
        )
        return new_total

    def cumulative(self, *, run_id: str, metric_name: str) -> float:
        with self._lock:
            ledger = self._ledgers.get(run_id)
            if ledger is None:
                return 0.0
            return ledger.cumulative.get(metric_name, 0.0)

    def applied_keys(self, run_id: str) -> frozenset[str]:
        """Test/introspection helper."""
        with self._lock:
            ledger = self._ledgers.get(run_id)
            return frozenset(ledger.applied_keys) if ledger else frozenset()
