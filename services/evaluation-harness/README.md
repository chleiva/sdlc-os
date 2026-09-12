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

## Install & run tests

```bash
cd services/evaluation-harness
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../run-registry
pip install -e . --no-deps && pip install pytest
python -m pytest -q
```

43 tests, no external services required.
