"""Real OpenTelemetry trace/span plumbing (master spec Section 16.1 /
16.4: "the trace stream and the infrastructure observability stack ...
share the same distributed-tracing keys").

Why the real `opentelemetry-sdk` package, not a hand-rolled equivalent:
it installs cleanly in this environment (see `pyproject.toml` and the
final agent report), and using it means the trace-id/span-id generation,
parent/child span relationships, and context-propagation semantics this
module relies on are the genuine, spec-compliant OTel ones -- not a
lookalike this codebase would have to independently get right.

The one real design problem it does not solve for free: this System's
own shared correlation key is `run_registry.models.Run.trace_id` -- an
opaque string already threaded through F2's Registry (`create_run`,
`append_attempt`) by every Wave-1 deliverable (job-dispatcher mints it,
orchestrator and tenant-cell both read it back off the `Run` row). That
string is not guaranteed to be a 32-hex-digit OTel trace id (existing
tests already use values like `"trace-20"`). `context_for_trace_key`
below deterministically derives a valid 128-bit OTel trace id from *any*
string via a SHA-256 truncation -- the same string always maps to the
same OTel trace id, which is all "correlate directly" (spec Section
16.1) requires -- while the ORIGINAL string is still carried on every
shipped event as its own `trace_id` field (see `sink.Event`), so a human
or dashboard can join on the exact same value already visible in the Run
Registry, with no lookup table in between.
"""

from __future__ import annotations

import hashlib
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from observability.sink import EventSink


def trace_id_int_from_key(trace_key: str) -> int:
    """Deterministic 128-bit OTel trace id derived from an arbitrary
    correlation-key string (see module docstring). Never zero (an
    all-zero trace id is invalid per the OTel spec)."""
    digest = hashlib.sha256(trace_key.encode("utf-8")).digest()[:16]
    value = int.from_bytes(digest, "big")
    return value or 1


def trace_key_to_otel_hex(trace_key: str) -> str:
    """The 32-hex-char OTel trace id a given correlation key maps to --
    exposed so a test can assert two independently-derived spans for the
    same `trace_key` really do share one real OTel trace id, not just a
    sink-level string tag."""
    return format(trace_id_int_from_key(trace_key), "032x")


def context_for_trace_key(trace_key: str) -> otel_context.Context:
    """Build an OTel `Context` carrying a (non-recording, `is_remote`)
    parent span whose trace id is deterministically derived from
    `trace_key`. This is exactly the mechanism real cross-process trace
    propagation uses (e.g. a W3C `traceparent` header carrying an
    upstream trace id): a span later started against this context shares
    the trace id but gets its own freshly-generated span id and records
    the synthesized id here as its parent."""
    id_gen = RandomIdGenerator()
    sc = SpanContext(
        trace_id=trace_id_int_from_key(trace_key),
        span_id=id_gen.generate_span_id(),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace_api.set_span_in_context(NonRecordingSpan(sc))


class SinkSpanExporter(SpanExporter):
    """A real `opentelemetry.sdk.trace.export.SpanExporter` whose
    "backend" is our off-node `EventSink`. Used with `SimpleSpanProcessor`
    (never `BatchSpanProcessor`): `SimpleSpanProcessor.on_end()` calls
    `export()` synchronously, on the same thread, the instant a span
    ends -- there is no batching window, which is exactly the
    off-node-first-shipping property Section 16.4 requires ("shipped
    continuously ... as it is produced, not batched")."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    def export(self, spans: Any) -> "SpanExportResult":
        for s in spans:
            assert isinstance(s, ReadableSpan)
            ctx = s.get_span_context()
            attrs: dict[str, Any] = dict(s.attributes or {})
            trace_key = attrs.pop("trace_key", None) or format(ctx.trace_id, "032x")
            self._sink.emit(
                kind="span",
                name=s.name,
                trace_id=trace_key,
                span_id=format(ctx.span_id, "016x"),
                run_id=attrs.pop("run_id", None),
                tenant_id=attrs.pop("tenant_id", None),
                attributes={
                    **attrs,
                    "otel_trace_id_hex": format(ctx.trace_id, "032x"),
                    "start_time_ns": s.start_time,
                    "end_time_ns": s.end_time,
                },
            )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - nothing to release
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def build_tracer(*, service_name: str, sink: EventSink) -> trace_api.Tracer:
    """One real `TracerProvider` (a `Resource`-tagged service identity,
    exactly as a real deployment would tag spans by originating service)
    wired to `sink` via `SimpleSpanProcessor` + `SinkSpanExporter`, and
    the `Tracer` obtained from it. Each call builds its own
    `TracerProvider` (not the process-global one) so tests can construct
    several independently-sinked tracers (e.g. one standing in for
    "orchestrator", one for "tenant-cell") in the same process without
    cross-talk -- mirroring two real services each running their own SDK.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(SinkSpanExporter(sink)))
    return provider.get_tracer(service_name)
