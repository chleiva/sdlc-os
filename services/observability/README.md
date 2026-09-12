# D11 -- Observability Wiring

Wires the trace/log/metric stack across Wave 1's real services: the
off-node-first observability channel, trace correlation between
agent-level and infra-level events, durable-execution-safe (no
double-spend) cost metrics, the session-broker path for exceptional
direct node access, and alerting. See
`docs/deliverables/wave2-D11-observability.md` for the brief this
implements and the final agent report for the full acceptance-criteria
checklist.

## Hard constraint, and how this deliverable answers it

There is no live Prometheus/Grafana/Loki/Tempo stack, no real cloud
node, and no real distributed-tracing collector in this environment.
This package is:

  (a) a real, standalone library (`src/observability/`) implementing
      genuine OpenTelemetry trace/span/log correlation, importable by
      other services;
  (b) small, surgical, additive instrumentation calls into
      `services/orchestrator/`, `services/tenant-cell/`, and
      `services/job-dispatcher/` at their existing choke points --
      `HookChain.dispatch`, `Orchestrator._transition`/
      `_implementation_step`/`_verification_step`,
      `InterruptionWatcher.handle_interruption`,
      `JobDispatcher._create_run`/`_queue_and_notify` -- every one of
      them an optional, default-`None` constructor argument or call, so
      every existing call site and every existing test in those three
      services is completely unaffected;
  (c) a real local "off-node store" stand-in (`sink.EventSink`) that
      ships spans/logs/metrics/audit-events/alerts continuously, as
      they're produced, to a thread-safe in-process index and (when a
      path is given) a durably fsync'd JSONL file -- the integration
      point a real deployment replaces with genuine OTLP export to
      Tempo/Loki/Prometheus (F1's `infra/modules/observability`).

## What's real vs. what's a documented stand-in

| Piece | Real | Stand-in |
|---|---|---|
| Trace/span generation, parent/child relationships, context propagation | Yes -- the real `opentelemetry-sdk`/`opentelemetry-api` (pinned 1.44.0); it installs cleanly in this environment, so this deliverable uses it rather than a hand-rolled lookalike | n/a |
| Off-node-first shipping (spans) | Yes -- `SimpleSpanProcessor` + a custom `SpanExporter` (`tracing.SinkSpanExporter`) that ships synchronously, on span end, never batched | n/a |
| Logs / metrics correlation | A deliberately custom, non-OTel-SDK path (see `client.py`'s module docstring for why: OTel's own Logs/Metrics SDKs are periodic-export-oriented and would reintroduce batching) -- still tagged with the same real trace/span ids | n/a |
| The off-node store itself | Yes -- `sink.EventSink`: every `emit()` call is durable (flush + fsync) before it returns; `read_events_from_file` reconstructs everything from the file alone, with zero reference to the writing process | The wire protocol/query language (a real deployment swaps this for OTLP to Tempo, a Loki push-API client, Prometheus remote-write) |
| Idempotent (no-double-spend) cost/time ledger | Yes -- `metrics.CostMetricStore`, dedup by explicit idempotency key | Where the ledger physically lives in production (this pass keeps it in-process; a real deployment persists it the same durable way the rest of the System does, e.g. a Registry row, or lets a real Prometheus counter's own monotonicity carry this property) |
| Session broker (identity+reason-gated, audited) | Yes -- `session_broker.SessionBrokerClient`'s gating and audit-event-on-open/close | The actual broker backend: `MockSessionBrokerBackend` stands in for AWS SSM Session Manager (no real AWS SSM account/agent in this environment) |
| Alerting | Yes -- `alerting.AlertSink`/`FileAlertSink`/`InMemoryAlertSink` genuinely push and are genuinely readable back | The actual channel: a real deployment implements `AlertSink.push` against PagerDuty/Slack; nothing else changes |

## Shared trace keys (Section 16.1)

`run_registry.models.Run.trace_id` is already the System's shared
correlation key -- minted once (by job-dispatcher or the orchestrator's
`start_run`) and threaded through F2's Registry (`create_run`,
`append_attempt`) to every Wave-1 deliverable that reads the `Run` row
back. This deliverable does not invent a second key: every
instrumentation call site tags its event with that exact string, and
`tracing.context_for_trace_key` derives a real, deterministic 128-bit
OTel trace id from it (via SHA-256 truncation, so it works even for the
non-hex trace_id values already used elsewhere in this codebase's test
suites, e.g. `"trace-20"`), so an agent-level orchestrator event and an
infra-level tenant-cell event for the same run share one genuine OTel
trace id, not just a matching string label.

## Layout

```
services/observability/
  src/observability/
    sink.py             the off-node store stand-in (EventSink, Event, read_events_from_file)
    tracing.py          real OTel TracerProvider/SpanExporter wiring + trace-key derivation
    metrics.py           idempotent cumulative cost/time ledger (CostMetricStore)
    alerting.py           AlertSink / InMemoryAlertSink / FileAlertSink / Alert
    session_broker.py     SessionBrokerClient / MockSessionBrokerBackend (identity+reason-gated, audited)
    client.py              ObservabilityClient -- the one object instrumented services receive
  tests/                    one pytest module per acceptance criterion (see final agent report
                          for the criterion -> test-file mapping)
```

## Setup

```bash
cd services/observability
python3 -m venv .venv
.venv/bin/pip install -e ../run-registry -e ../issue-tracker -e ../orchestrator -e ../tenant-cell
.venv/bin/pip install --no-deps -e ../job-dispatcher   # its own file:../issue-tracker dependency
                                                        # is already satisfied by the editable
                                                        # install above; --no-deps avoids pip
                                                        # trying to re-resolve it non-editably
.venv/bin/pip install -e '.[dev]' --no-deps
.venv/bin/pip install pytest opentelemetry-api==1.44.0 opentelemetry-sdk==1.44.0 mcp==2.2.0 jsonschema anyio
```

(The multi-step install works around a real, observed pip quirk: a
plain `pip install -e '.[dev]'` in one shot tries to resolve
`job-dispatcher`'s own `issue-tracker @ file:../issue-tracker`
dependency a second, non-editable way and reports a spurious
`ResolutionImpossible` conflict against the editable install above --
installing job-dispatcher itself with `--no-deps` sidesteps it. Nothing
about the run-registry/orchestrator/tenant-cell packages themselves is
affected: this is purely a dependency-resolution ordering issue with
job-dispatcher's own `pyproject.toml`, pre-existing and unrelated to
D11's own changes.)

## Running the tests

```bash
cd services/observability
.venv/bin/python -m pytest tests/ -v
```

21 tests, all real (no live cloud/collector needed): several drive the
real `orchestrator.core.Orchestrator`, `tenant_cell.interruption_watcher.InterruptionWatcher`,
and `job_dispatcher.dispatcher.JobDispatcher` end-to-end against a real,
temp-file-backed `run_registry.RegistryService`.

After any change to a shared/consumed file, re-run that service's OWN
test suite too:

```bash
cd services/orchestrator  && .venv/bin/python -m pytest tests/ -q   # 60 passed, 1 skipped (unchanged)
cd services/tenant-cell   && .venv/bin/python -m pytest -q          # 25 passed (unchanged)
cd services/job-dispatcher && .venv/bin/python -m pytest -q         # 21 passed (unchanged)
```
