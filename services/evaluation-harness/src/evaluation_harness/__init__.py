"""Evaluation Harness (D13, Wave 3) -- SDLC Auto.

Public surface:

- `evaluation_harness.benchmarks` -- the `BenchmarkSuite` interface plus
  fixture suites standing in for Section 20.1's blended evaluation set.
- `evaluation_harness.runner.BenchmarkRunner` -- quarterly + on-demand
  suite orchestration.
- `evaluation_harness.scheduling.DueScheduler` -- the "is this due"
  primitive shared by the benchmark runner and the quarterly comparison.
- `evaluation_harness.semantic_grader` / `.spot_check` -- the
  semantic-correctness spot-check countermeasure to "tests pass but
  wrong" (Section 11.2).
- `evaluation_harness.comparison` -- the quarterly self-hosted-vs-frontier
  trend (Section 13.1/13.4).
- `evaluation_harness.metrics` -- live production metrics computed from
  F2's real Registry Service Attempt history (Section 20.2).
"""

from evaluation_harness.runner import BenchmarkRunner, SuiteRunOutcome
from evaluation_harness.scheduling import DueScheduler, InMemoryLastRunStore, JSONFileLastRunStore

__all__ = [
    "BenchmarkRunner",
    "SuiteRunOutcome",
    "DueScheduler",
    "InMemoryLastRunStore",
    "JSONFileLastRunStore",
]
