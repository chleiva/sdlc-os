"""Flaky-test detection: re-run on failure, distinguish flaky from genuine
(Sec. 11.3: "Flaky tests are detected (re-run on failure) and reported
separately from genuine regressions so the System does not misattribute
infrastructure flakiness to its own change, and does not quietly retry
past a real failure either.")
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .local_pytest import run_pytest


@dataclass(frozen=True)
class FlakyCheckResult:
    flaky: list[str]
    genuine_failures: list[str]

    @property
    def any_genuine_failure(self) -> bool:
        return bool(self.genuine_failures)


def rerun_failures_and_classify(
    failing_node_ids: list[str],
    cwd: Path,
    python_executable: str | None = None,
    max_reruns: int = 1,
) -> FlakyCheckResult:
    """For each initially-failing test, rerun it in isolation up to
    `max_reruns` times. A test that passes on any rerun is classified
    flaky (infrastructure/non-determinism, not attributable to the
    change); a test that still fails after every rerun is a genuine
    failure. This never silently drops a genuine failure into the flaky
    bucket: a test only counts as flaky if it is later observed to pass.
    """
    flaky: list[str] = []
    genuine: list[str] = []
    for node_id in failing_node_ids:
        passed_on_rerun = False
        for _ in range(max_reruns):
            result = run_pytest([node_id], cwd=cwd, python_executable=python_executable)
            if result.total > 0 and result.failed == 0 and result.errored == 0:
                passed_on_rerun = True
                break
        if passed_on_rerun:
            flaky.append(node_id)
        else:
            genuine.append(node_id)
    return FlakyCheckResult(flaky=flaky, genuine_failures=genuine)
