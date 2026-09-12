"""The off-node store stand-in (master spec Section 16.4): what a real
deployment's Prometheus/Grafana/Loki/Tempo stack (F1's
`infra/modules/observability`) is on the other side of.

There is no live Prometheus/Grafana/Loki/Tempo stack in this environment
(this deliverable's stated hard constraint), so `EventSink` is a real,
standalone stand-in for "the centralized, off-node place every trace
event/log line/metric ships to continuously as it is produced" (Section
16.4's "primary channel"). It is not a mock in the sense of returning
canned answers -- every event genuinely is written, genuinely is
queryable back, and genuinely is durable across a simulated process
crash (see the module docstring's "off-node-first" note below and
`tests/test_off_node_first_shipping.py`).

Real vs stand-in, stated plainly:
  * REAL: continuous (non-batched) per-event durability -- `emit()`
    writes+flushes+fsyncs one JSON line per call, before returning, and
    an independent reader (`read_events_from_file`) can reconstruct every
    event written so far from nothing but that file, exactly as a real
    off-node collector would let an operator query a node's shipped data
    after the node itself is gone.
  * STAND-IN: the wire protocol and query language. A real deployment
    swaps `EventSink` for genuine OTLP export to Tempo (spans), a Loki
    push-API client (logs), and a Prometheus remote-write/pushgateway
    client (metrics) -- see `tracing.SinkSpanExporter` for exactly where
    that swap happens for spans, and this module's own docstring note
    below for logs/metrics.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Every event this library ships is one of these kinds. "span" events
# come from the real OpenTelemetry pipeline (see tracing.py); "log",
# "metric", "audit", and "alert" are this library's own lightweight,
# sink-native event kinds (see module docstring in client.py for why
# logs/metrics are not routed through OTel's own Logs/Metrics SDK).
EventKind = str  # "span" | "log" | "metric" | "audit" | "alert"


@dataclass(frozen=True)
class Event:
    """One durable record in the off-node store. `sequence` is the
    sink's own monotonically-increasing arrival-order counter --
    independent of any wall-clock timestamp either this process or a
    caller supplied -- so ordering claims ("the flush event reached the
    store before the checkpoint event") can be proven from the store's
    own bookkeeping, not merely trusted from in-process code."""

    event_id: str
    kind: EventKind
    name: str
    trace_id: str | None
    span_id: str | None
    run_id: str | None
    tenant_id: str | None
    attributes: dict[str, Any]
    timestamp_ns: int
    sequence: int

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json_dict(d: dict[str, Any]) -> "Event":
        return Event(**d)


class EventSink:
    """The off-node store. Thread-safe; every `emit()` call is a
    complete, durable unit of work by the time it returns -- there is no
    internal queue a crash between two `emit()` calls could lose
    anything from.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._seq = itertools.count(1)
        self._path = Path(path) if path is not None else None
        self._fh = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Line-buffered append mode: opened once, kept open for the
            # sink's lifetime. Each emit() still does its own explicit
            # flush()+fsync() (see below) rather than relying on Python's
            # line buffering, because line buffering alone does not
            # guarantee the OS has the bytes durably on disk.
            self._fh = open(self._path, "a", buffering=1, encoding="utf-8")

    def emit(
        self,
        *,
        kind: EventKind,
        name: str,
        trace_id: str | None = None,
        span_id: str | None = None,
        run_id: str | None = None,
        tenant_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Event:
        """Ship one event to the off-node store *right now*. This is the
        one method every other module in this package (and every
        instrumented external service) ultimately calls -- the
        "off-node-first shipping" requirement is this method's contract:
        by the time it returns, the event is durably recorded (in the
        backing file, if one was configured; always in the in-process
        index), not queued for a later batch flush that might never
        happen.
        """
        with self._lock:
            ev = Event(
                event_id=uuid.uuid4().hex,
                kind=kind,
                name=name,
                trace_id=trace_id,
                span_id=span_id,
                run_id=run_id,
                tenant_id=tenant_id,
                attributes=dict(attributes or {}),
                timestamp_ns=time.time_ns(),
                sequence=next(self._seq),
            )
            self._events.append(ev)
            if self._fh is not None:
                self._fh.write(json.dumps(ev.to_json_dict()) + "\n")
                self._fh.flush()
                os.fsync(self._fh.fileno())
        return ev

    # ------------------------------------------------------------------
    # query surface -- the trace-viewer/dashboard integration point
    # ------------------------------------------------------------------
    def all_events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def by_trace_id(self, trace_id: str) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.trace_id == trace_id]

    def by_run_id(self, run_id: str) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.run_id == run_id]

    def by_kind(self, kind: EventKind) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.kind == kind]

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def read_events_from_file(path: str | Path) -> list[Event]:
    """Reconstruct every event durably shipped to `path`, from nothing
    but the file -- no reference to the `EventSink` instance (or process)
    that wrote them. This is the "confirm nothing was lost when the
    process crashed" side of the off-node-first-shipping proof: a test
    (or a real operator) opens a brand new reader against the file a
    (possibly now-dead) process was writing to, with no in-memory state
    carried over at all.
    """
    p = Path(path)
    if not p.exists():
        return []
    events: list[Event] = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(Event.from_json_dict(json.loads(line)))
    return events
