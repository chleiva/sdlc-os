"""The `BenchmarkSuite` interface (master spec Section 20.1).

Section 20.1 states the internal evaluation set is "a blend, not a single
number": agentic issue-resolution suites (SWE-bench family, including
long-horizon variants), terminal/tool-use suites (Terminal-Bench-style),
multi-language correctness suites (Aider Polyglot-style), broader
agentic-task suites (GAIA/OSWorld/tau-bench-style), and an internal set
modeled on the organization's own historical tickets.

There is no live SWE-bench/Terminal-Bench/Aider-Polyglot/GAIA dataset (nor
a live model to grade against) available in this environment. What is
real here is the *pipeline*: a `BenchmarkSuite` is a real, pluggable
interface (name, a task set, a deterministic `run(task) -> BenchmarkResult`
method), and `benchmarks/fixtures.py` provides small, self-contained,
deterministic fixture suites standing in for each named family -- enough
to prove the runner genuinely runs, scores, and aggregates tasks, not to
benchmark anything real. A future integration swaps a fixture suite's
`tasks()`/`run()` implementation for one that actually checks out a
SWE-bench-style repo and invokes a real model; the `BenchmarkSuite`
interface itself does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class BenchmarkTask:
    """One task instance within a suite.

    `difficulty` is deliberately a small, fixed vocabulary
    ("short" | "long_horizon") rather than free text, so suite runners and
    the Section 20.2 long-horizon-vs-short-task split can group on it
    without guessing at string variants.
    """

    task_id: str
    family: str
    prompt: str
    difficulty: str  # "short" | "long_horizon"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkResult:
    """The outcome of running one `BenchmarkTask` through a suite."""

    task_id: str
    family: str
    passed: bool
    score: float  # 0.0..1.0, partial credit where the family supports it
    duration_s: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuiteRunResult:
    """Aggregate pass/fail + timing for one suite's full task set."""

    suite_name: str
    results: tuple[BenchmarkResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.passed_count / self.total

    @property
    def total_duration_s(self) -> float:
        return sum(r.duration_s for r in self.results)

    def by_difficulty(self, difficulty: str) -> tuple[BenchmarkResult, ...]:
        return tuple(
            r for r in self.results if r.details.get("difficulty") == difficulty
        )


@runtime_checkable
class BenchmarkSuite(Protocol):
    """A named, pluggable benchmark family.

    `name` identifies the suite (used as the scheduling key -- see
    `evaluation_harness.scheduling`). `tasks()` returns this suite's
    fixed task set; `run(task)` executes exactly one task and returns a
    graded `BenchmarkResult`. A real (non-fixture) implementation is free
    to shell out, check out a repo, or call a model here -- the interface
    makes no assumption either way.
    """

    name: str

    def tasks(self) -> Sequence[BenchmarkTask]: ...

    def run(self, task: BenchmarkTask) -> BenchmarkResult: ...
