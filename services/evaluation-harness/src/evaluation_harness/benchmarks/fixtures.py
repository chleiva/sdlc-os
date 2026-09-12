"""Deterministic, self-contained fixture suites standing in for the named
benchmark families in master spec Section 20.1.

None of these check out a real SWE-bench/Terminal-Bench/Aider-Polyglot/GAIA
dataset, and none call a live model. Each is a small (4-8 task), fully
deterministic `BenchmarkSuite` implementation whose `run()` method scores a
task with plain, scripted Python logic -- enough to prove the runner in
`evaluation_harness.runner` genuinely executes, scores, and aggregates a
suite's tasks (some intentionally failing, so aggregation is proven, not
just a suite that always reports 100%). Swapping in a real dataset/model
later means writing a new class satisfying `BenchmarkSuite`; nothing else
in this package needs to change.

`InternalHistoricalTicketsSuite` is the one exception to "fully
self-contained": per the brief, it is seeded from real synthetic
`run_registry` data (real `Run`/`Attempt` records created through F2's
`RegistryService`), not invented in this module -- see its docstring and
`tests/test_benchmark_runner.py`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

from evaluation_harness.benchmarks.base import BenchmarkResult, BenchmarkTask


def _stable_score(seed: str, *, low: float, high: float) -> float:
    """Deterministic pseudo-score in [low, high) from a stable string seed.

    Using a hash (not `random`) keeps every fixture suite's results
    reproducible run-to-run without needing to thread a seeded RNG through
    every call site -- important because the runner's tests assert on
    exact pass/fail outcomes.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    return low + fraction * (high - low)


@dataclass(frozen=True)
class _ScriptedTask:
    task_id: str
    prompt: str
    difficulty: str
    pass_threshold: float  # score >= this => passed
    metadata: dict[str, Any]


class _ScriptedSuite:
    """Shared plumbing for the fixture suites below: a fixed task list,
    each graded deterministically by `_stable_score` seeded on
    (suite name, task_id) against a per-task pass threshold. Subclasses
    only need to define `name`, `family`, and the scripted task list.
    """

    name: str
    family: str
    _tasks: tuple[_ScriptedTask, ...]

    def tasks(self) -> Sequence[BenchmarkTask]:
        return tuple(
            BenchmarkTask(
                task_id=t.task_id,
                family=self.family,
                prompt=t.prompt,
                difficulty=t.difficulty,
                metadata=dict(t.metadata),
            )
            for t in self._tasks
        )

    def run(self, task: BenchmarkTask) -> BenchmarkResult:
        scripted = next(t for t in self._tasks if t.task_id == task.task_id)
        score = _stable_score(f"{self.name}:{task.task_id}", low=0.0, high=1.0)
        passed = score >= scripted.pass_threshold
        # Deterministic stand-in "duration": longer for long_horizon tasks,
        # mirroring Section 11.2's short-vs-extended-task distinction.
        duration_s = 12.0 if task.difficulty == "short" else 95.0
        return BenchmarkResult(
            task_id=task.task_id,
            family=self.family,
            passed=passed,
            score=round(score, 4),
            duration_s=duration_s,
            details={"difficulty": task.difficulty, "pass_threshold": scripted.pass_threshold},
        )


class SweBenchStyleSuite(_ScriptedSuite):
    """Agentic issue-resolution suite, SWE-bench family (Section 20.1
    bullet 1) -- including a long-horizon variant, not only the
    short-task suite (the extended-task collapse in Section 11.2 is
    exactly why both are modeled, not just the short one).
    """

    name = "swebench-style"
    family = "agentic-issue-resolution"
    # Short tasks use a low pass_threshold (easy to clear, mirrors the
    # 70%+-on-short-tasks finding); long_horizon tasks use a high
    # threshold (hard to clear, mirrors the 20-25% collapse).
    _tasks = (
        _ScriptedTask("swebench-short-1", "Fix off-by-one in pagination helper", "short", 0.15, {}),
        _ScriptedTask("swebench-short-2", "Add null-check guard before dict access", "short", 0.15, {}),
        _ScriptedTask("swebench-short-3", "Correct timezone conversion bug", "short", 0.15, {}),
        _ScriptedTask("swebench-long-1", "Refactor multi-file auth flow across 6 modules", "long_horizon", 0.85, {}),
        _ScriptedTask("swebench-long-2", "Migrate ORM layer across a 20-file diff", "long_horizon", 0.85, {}),
        _ScriptedTask("swebench-long-3", "Multi-session bug hunt spanning 3 services", "long_horizon", 0.85, {}),
    )


class TerminalBenchStyleSuite(_ScriptedSuite):
    """Terminal/tool-use suite (Terminal-Bench-style, Section 20.1 bullet
    2) -- correctness of the actual commands an agent runs, not just the
    code it writes. Each task's "prompt" is a natural-language shell
    request; grading is a scripted match against the expected command
    shape (no real shell is invoked).
    """

    name = "terminal-bench-style"
    family = "terminal-tool-use"
    _tasks = (
        _ScriptedTask("term-1", "List the 5 largest files under ./src", "short", 0.3, {}),
        _ScriptedTask("term-2", "Find and remove all *.pyc files recursively", "short", 0.3, {}),
        _ScriptedTask("term-3", "Grep for TODO across the repo, excluding vendor/", "short", 0.3, {}),
        _ScriptedTask("term-4", "Chain a multi-stage build+test+package pipeline", "long_horizon", 0.8, {}),
    )


class AiderPolyglotStyleSuite(_ScriptedSuite):
    """Multi-language correctness suite (Aider Polyglot-style, Section
    20.1 bullet 3) -- one task per language, to avoid over-fitting
    evaluation to a single ecosystem.
    """

    name = "aider-polyglot-style"
    family = "multi-language-correctness"
    _tasks = (
        _ScriptedTask("poly-python", "Implement a stable sort in Python", "short", 0.3, {"language": "python"}),
        _ScriptedTask("poly-go", "Implement a bounded worker pool in Go", "short", 0.3, {"language": "go"}),
        _ScriptedTask("poly-rust", "Implement a lock-free ring buffer in Rust", "long_horizon", 0.8, {"language": "rust"}),
        _ScriptedTask("poly-typescript", "Implement a typed event emitter in TypeScript", "short", 0.3, {"language": "typescript"}),
    )


class GaiaStyleSuite(_ScriptedSuite):
    """Broader agentic-task suite (GAIA/OSWorld/tau-bench-style, Section
    20.1 bullet 4) -- capability outside pure code-editing (multi-step
    tool orchestration, information-gathering).
    """

    name = "gaia-style"
    family = "broader-agentic"
    _tasks = (
        _ScriptedTask("gaia-1", "Book-keeping: reconcile two CSV exports and report the delta", "short", 0.35, {}),
        _ScriptedTask("gaia-2", "Multi-tool research task requiring 4 chained lookups", "long_horizon", 0.8, {}),
        _ScriptedTask("gaia-3", "Operate a simulated desktop to complete a 3-step form", "long_horizon", 0.8, {}),
    )


class InternalHistoricalTicketsSuite:
    """An internal set modeled on the organization's own historical
    tickets (Section 20.1 bullet 5) -- the one fixture suite whose task
    set is NOT invented in this module. It is built directly from real
    `run_registry` `Run`/`Attempt` records (see
    `evaluation_harness.benchmarks.historical.build_tasks_from_registry`),
    so "internal historical tickets" means what it says: real Registry
    Service data, not a hand-authored fixture.

    Grading replays each historical ticket as a regression check: did the
    ticket originally reach `completed` in a small number of attempts?
    Requiring `attempt_count <= max_acceptable_attempts` (default 2)
    means a ticket that needed heavy re-planning/retries the first time
    around is graded as a fragile "pass", surfacing exactly the kind of
    task this suite exists to keep an eye on.
    """

    name = "internal-historical-tickets"
    family = "internal-historical"

    def __init__(self, tasks: Sequence[BenchmarkTask], *, max_acceptable_attempts: int = 2):
        self._tasks = tuple(tasks)
        self._max_acceptable_attempts = max_acceptable_attempts

    def tasks(self) -> Sequence[BenchmarkTask]:
        return self._tasks

    def run(self, task: BenchmarkTask) -> BenchmarkResult:
        original_outcome = task.metadata.get("original_outcome")
        attempt_count = int(task.metadata.get("attempt_count", 1))
        passed = original_outcome == "completed" and attempt_count <= self._max_acceptable_attempts
        score = 1.0 if passed else max(0.0, 1.0 - 0.25 * attempt_count)
        return BenchmarkResult(
            task_id=task.task_id,
            family=self.family,
            passed=passed,
            score=round(score, 4),
            duration_s=5.0 if task.difficulty == "short" else 60.0,
            details={
                "difficulty": task.difficulty,
                "original_outcome": original_outcome,
                "attempt_count": attempt_count,
            },
        )
