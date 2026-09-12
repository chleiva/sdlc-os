"""The `VerificationRunner` interface -- D2's seam onto D7's verification
pipeline (master spec Section 11).

Per the D2 brief's "Explicitly not in scope": "The verification pipeline's
actual test/lint/security-scan execution (-> D7) -- D2 invokes it and acts
on the result." This module is that invocation boundary: `Orchestrator`
calls `VerificationRunner.run(...)` at Stage 6 and branches on
`VerificationResult.passed`, exactly the way it will once D7 exists for
real. `ScriptedVerificationRunner` is the deterministic mock used by every
test here -- a fixed queue of canned pass/fail results, so the state
machine's "verification fails -> loop back to implementation with a
bounded retry budget" edge (Section 5, Stage 6) is exercised for real.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    summary: str
    tests_total: int = 0
    tests_failed: int = 0


class VerificationRunner(ABC):
    @abstractmethod
    def run(self, *, run_context: dict) -> VerificationResult: ...


class ScriptedVerificationRunner(VerificationRunner):
    def __init__(self, results: list[VerificationResult] | None = None) -> None:
        self._results = list(results or [VerificationResult(passed=True, summary="scripted default pass")])
        self.call_count = 0

    def run(self, *, run_context: dict) -> VerificationResult:
        self.call_count += 1
        if len(self._results) > 1:
            return self._results.pop(0)
        # Last scripted result repeats, so a test doesn't have to script
        # an exact call count for the passing tail of a retry loop.
        return self._results[0]
