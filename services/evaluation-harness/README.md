# evaluation-harness — D13: Evaluation Harness

Wave 3 deliverable. The blended internal evaluation set and live-metrics
pipeline that would decide whether autonomy expands or contracts for a
given repository/task class — built to read skeptically, per the master
spec's own finding that a meaningful share of "passed" tasks are
semantically incorrect despite green tests.

Every live-production metric (task success rate, override/rejection
rate, rework rate, cycle time, cost per completed task, defect escape
rate) is computed from real `run_registry` Attempt history, seeded via
real `RegistryService` calls in tests — not a hand-built dict standing
in for a Run. Long-horizon and short-task success rates are always
reported as two distinct numbers, never blended into one headline
figure, and the semantic-correctness spot-check is proven to diverge
from raw test-pass rate on a constructed fixture where they genuinely
should.

Benchmark suites (SWE-bench/Terminal-Bench/Aider-Polyglot/GAIA-style, and
the internal historical-tickets suite) are real, pluggable, and
schedulable, but — since no live benchmark dataset or model endpoint
exists in this environment — backed by small deterministic fixture task
sets and a scripted `SemanticGrader`, documented as exactly where a real
dataset/human-or-strong-model grader plugs in later.

## Layout

```
services/evaluation-harness/
  src/evaluation_harness/
    benchmarks/
      base.py           BenchmarkSuite interface (Section 20.1's
                         "blend, not a single number" internal set)
      fixtures.py         deterministic, self-contained fixture suites
                         standing in for the named benchmark families
                         (SWE-bench/Terminal-Bench/Aider-Polyglot/GAIA)
                         -- none check out the real datasets
      historical.py        InternalHistoricalTicketsSuite: builds its
                         task set from real run_registry data
    metrics/
      aggregate.py         ProductionMetricsReport: assembles every
                         Section 20.2 live metric from real
                         run_registry data plus this package's own
                         pluggable cost/defect/gate-event sources
      success.py            task success rate, long-horizon tracked
                         separately from short-task (bullets 1, 7)
      overrides.py           human override/rejection rate per gate
                         (bullet 2)
      rework.py                rework rate: merged changes needing a
                         follow-up fix within a defined window (bullet 3)
      cycle_time.py              intake to PR-ready vs. a human
                         baseline (bullet 4)
      cost.py                      cost per successfully completed
                         task, including discarded/retried work
                         (bullet 5, Section 16.2)
      defect_escape.py               defect escape rate: production
                         issues verification should have caught
                         (bullet 6)
      human_time.py                    net human time vs. a stated
                         human-only baseline (bullet 8, Section 23.5's
                         "permanent humility metric")
    runner.py            BenchmarkRunner: real orchestration over every
                         registered BenchmarkSuite -- runs fixture
                         tasks, aggregates results
    comparison.py          quarterly self-hosted-vs-frontier-API
                         comparison on the same internal suite
                         (Section 13.1/13.4/20.1)
    spot_check.py            semantic-correctness spot-check sampling:
                         a random fraction of "passed" runs re-graded
                         by SemanticGrader, reported separately from
                         the raw test-pass rate (Section 11.2)
    semantic_grader.py         SemanticGrader: the direct
                         countermeasure to "tests pass but the change
                         is semantically wrong" -- scripted here, since
                         no live human reviewer or strong-model API
                         exists in this environment
    task_classification.py       short-task vs. long-horizon
                         classification from real Attempt history
    gate_events.py                  the one piece of state this
                         package tracks that isn't reconstructable
                         from a fresh read of F2's public API alone
    scheduling.py                     "is this due" scheduling against
                         a stored last-run timestamp
    timeutil.py                        shared ISO-8601 timestamp
                         helpers, matching run_registry's own format
```

## Install & run tests

```bash
cd services/evaluation-harness
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../run-registry
pip install -e . --no-deps && pip install pytest
python -m pytest -q
```

43 tests, no external services required.
