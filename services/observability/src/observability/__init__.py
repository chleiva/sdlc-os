"""D11: Observability Wiring (SDLC Auto, Wave 2).

Public surface:

  * `EventSink` / `Event` / `read_events_from_file` (sink.py) -- the
    off-node store stand-in every other piece ships to continuously.
  * `ObservabilityClient` (client.py) -- the single object instrumented
    services (orchestrator, tenant-cell, job-dispatcher) optionally
    receive; ties together real OTel tracing, the sink, the idempotent
    cost ledger, alerting, and the session broker.
  * `AlertSink` / `InMemoryAlertSink` / `FileAlertSink` / `Alert`
    (alerting.py) -- checkpoint/stuck/budget-threshold alerts.
  * `SessionBrokerClient` / `MockSessionBrokerBackend` (session_broker.py)
    -- identity-gated, audited direct node access.
  * `CostMetricStore` (metrics.py) -- durable-execution-safe cumulative
    cost/time accounting (no double-spend across a resume).

See README.md for what's real vs. mocked and how to run the tests.
"""

from observability.alerting import Alert, AlertSink, FileAlertSink, InMemoryAlertSink, make_alert
from observability.client import ObservabilityClient
from observability.metrics import CostMetricStore
from observability.session_broker import MockSessionBrokerBackend, SessionBrokerClient, SessionHandle
from observability.sink import Event, EventSink, read_events_from_file
from observability.tracing import context_for_trace_key, trace_key_to_otel_hex

__all__ = [
    "ObservabilityClient",
    "EventSink",
    "Event",
    "read_events_from_file",
    "AlertSink",
    "InMemoryAlertSink",
    "FileAlertSink",
    "Alert",
    "make_alert",
    "SessionBrokerClient",
    "MockSessionBrokerBackend",
    "SessionHandle",
    "CostMetricStore",
    "context_for_trace_key",
    "trace_key_to_otel_hex",
]
