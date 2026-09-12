"""`ObservabilityClient`: the single object every instrumented service
(orchestrator, tenant-cell, job-dispatcher) is optionally handed, tying
together real OTel tracing (`tracing.py`), the off-node store
(`sink.py`), the idempotent cost/time ledger (`metrics.py`), alerting
(`alerting.py`), and the session broker (`session_broker.py`).

Design note on why logs/metrics are NOT routed through OTel's own
Logs/Metrics SDK, stated once here rather than repeated at each call
site: those SDKs are built around a periodic-export model (a
`PeriodicExportingMetricReader`, a batching `LogRecordProcessor`) that
would reintroduce exactly the "batched for end-of-run delivery" behavior
Section 16.4 rules out. `record_stage_transition`/`record_tool_call`/
`record_infra_event` below build *real* OTel spans (see `tracing.py`) --
one per event, exported synchronously via `SimpleSpanProcessor` the
instant the span ends -- which both is genuinely OTel and ships
per-event. `record_cost_increment` and `push_alert` ship straight to the
sink/alert-sink with no span at all, since a single scalar update or an
alert is not naturally span-shaped; they still carry the same
`trace_id` correlation key.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace as trace_api

from observability import tracing
from observability.alerting import Alert, AlertSink, make_alert
from observability.metrics import CostMetricStore
from observability.session_broker import SessionBrokerBackend, SessionBrokerClient, SessionHandle
from observability.sink import EventSink


class ObservabilityClient:
    def __init__(
        self,
        *,
        sink: EventSink,
        service_name: str,
        alert_sink: AlertSink | None = None,
        session_broker_backend: SessionBrokerBackend | None = None,
    ) -> None:
        self.sink = sink
        self.service_name = service_name
        self._tracer = tracing.build_tracer(service_name=service_name, sink=sink)
        self.metrics = CostMetricStore(sink)
        self.alert_sink = alert_sink
        if session_broker_backend is not None:
            self.session_broker = SessionBrokerClient(session_broker_backend, sink)
        else:
            self.session_broker = None

    # ------------------------------------------------------------------
    # agent-level trace events (orchestrator's choke points)
    # ------------------------------------------------------------------
    def record_stage_transition(
        self,
        *,
        trace_id: str,
        run_id: str,
        tenant_id: str | None,
        from_stage: str,
        to_stage: str,
        **attrs: Any,
    ) -> None:
        ctx = tracing.context_for_trace_key(trace_id)
        with trace_api.use_span(
            self._tracer.start_span(
                "stage_transition",
                context=ctx,
                attributes={
                    "trace_key": trace_id,
                    "run_id": run_id,
                    "tenant_id": tenant_id or "",
                    "from_stage": from_stage,
                    "to_stage": to_stage,
                    **{k: str(v) for k, v in attrs.items()},
                },
            ),
            end_on_exit=True,
        ):
            pass

    def record_tool_call(
        self,
        *,
        trace_id: str,
        run_id: str,
        tool_name: str,
        stage: str,
        blocked_by: str | None = None,
        error: str | None = None,
        **attrs: Any,
    ) -> None:
        ctx = tracing.context_for_trace_key(trace_id)
        with trace_api.use_span(
            self._tracer.start_span(
                "tool_call",
                context=ctx,
                attributes={
                    "trace_key": trace_id,
                    "run_id": run_id,
                    "tool_name": tool_name,
                    "stage": stage,
                    "blocked_by": blocked_by or "",
                    "error": error or "",
                    **{k: str(v) for k, v in attrs.items()},
                },
            ),
            end_on_exit=True,
        ):
            pass

    # ------------------------------------------------------------------
    # infra-level trace events (tenant-cell's interruption watcher)
    # ------------------------------------------------------------------
    def record_infra_event(
        self,
        *,
        trace_id: str,
        run_id: str,
        tenant_id: str | None,
        kind: str,
        **attrs: Any,
    ) -> None:
        """`kind` is a free label such as "observability_flush" or
        "checkpoint_write" -- see `tenant_cell.interruption_watcher`'s
        call sites. Same shared `trace_id` correlation key as
        `record_stage_transition`: this is precisely what makes an
        agent-level and an infra-level event for the same run join
        directly (Section 16.1)."""
        ctx = tracing.context_for_trace_key(trace_id)
        with trace_api.use_span(
            self._tracer.start_span(
                kind,
                context=ctx,
                attributes={
                    "trace_key": trace_id,
                    "run_id": run_id,
                    "tenant_id": tenant_id or "",
                    **{k: str(v) for k, v in attrs.items()},
                },
            ),
            end_on_exit=True,
        ):
            pass

    # ------------------------------------------------------------------
    # cumulative cost/time metrics -- no double-spend across a resume
    # ------------------------------------------------------------------
    def record_cost_increment(
        self,
        *,
        trace_id: str,
        run_id: str,
        tenant_id: str | None,
        unit_id: str,
        amount_usd: float,
        metric_name: str = "cost_usd",
    ) -> float:
        idempotency_key = f"{run_id}:{unit_id}:{metric_name}"
        return self.metrics.apply(
            run_id=run_id,
            metric_name=metric_name,
            amount=amount_usd,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            tenant_id=tenant_id,
            unit_id=unit_id,
        )

    def cumulative_cost(self, *, run_id: str, metric_name: str = "cost_usd") -> float:
        return self.metrics.cumulative(run_id=run_id, metric_name=metric_name)

    # ------------------------------------------------------------------
    # alerting (checkpoints / stuck-detection / budget thresholds)
    # ------------------------------------------------------------------
    def push_alert(
        self,
        *,
        kind: str,
        message: str,
        run_id: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
        **attrs: Any,
    ) -> Alert:
        alert = make_alert(
            kind=kind, message=message, run_id=run_id, tenant_id=tenant_id, trace_id=trace_id, **attrs
        )
        if self.alert_sink is not None:
            self.alert_sink.push(alert)
        return alert

    # ------------------------------------------------------------------
    # session broker passthrough (exceptional direct node access)
    # ------------------------------------------------------------------
    def open_direct_session(
        self, *, node_id: str, identity: str, reason: str, trace_id: str | None = None, tenant_id: str | None = None
    ) -> SessionHandle:
        if self.session_broker is None:
            raise RuntimeError("ObservabilityClient was constructed without a session_broker_backend")
        return self.session_broker.open_session(
            node_id=node_id, identity=identity, reason=reason, trace_id=trace_id, tenant_id=tenant_id
        )
