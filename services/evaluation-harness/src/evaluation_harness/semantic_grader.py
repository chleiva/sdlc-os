"""`SemanticGrader`: the direct countermeasure to "tests pass but the
change is semantically wrong" (Section 11.2, Section 20.1 bullet 6).

There is no live human reviewer or strong-model API available in this
environment. `ScriptedSemanticGrader` is a real, deterministic,
rule-based implementation of the `SemanticGrader` interface: it checks a
run's produced output against that task's acceptance checklist (a plain
list of required substrings/phrases) and reports which checklist items
are missing. This is intentionally *not* a claim of real semantic
understanding -- it is exactly where a real implementation plugs in
later: swap `ScriptedSemanticGrader` for one whose `grade()` method
sends `produced_output` and `checklist` to a human reviewer queue, or to
a strong frontier model asked "does this output actually satisfy these
acceptance criteria", and returns the same `SemanticGradeResult` shape.
Nothing else in this package (in particular `spot_check.py`) needs to
change when that swap happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class SemanticGradeResult:
    run_id: str
    semantically_correct: bool
    rationale: str
    missing: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class SemanticGrader(Protocol):
    def grade(
        self, *, run_id: str, produced_output: str, checklist: Sequence[str]
    ) -> SemanticGradeResult: ...


class ScriptedSemanticGrader:
    """Deterministic, rule-based `SemanticGrader`.

    A run is graded `semantically_correct=True` iff every phrase in its
    acceptance checklist appears (case-insensitively) as a substring of
    its produced output -- a crude but fully deterministic stand-in for
    "did this change actually do what the acceptance criteria asked for",
    the exact question Section 11.2 says a green test suite alone cannot
    answer.
    """

    def grade(
        self, *, run_id: str, produced_output: str, checklist: Sequence[str]
    ) -> SemanticGradeResult:
        haystack = produced_output.lower()
        missing = tuple(item for item in checklist if item.lower() not in haystack)
        if not checklist:
            # No checklist at all is a data-quality problem, not a pass --
            # fail closed rather than reporting a hollow "correct".
            return SemanticGradeResult(
                run_id=run_id,
                semantically_correct=False,
                rationale="no acceptance checklist available to grade against",
                missing=(),
            )
        if missing:
            return SemanticGradeResult(
                run_id=run_id,
                semantically_correct=False,
                rationale=f"missing {len(missing)}/{len(checklist)} acceptance-checklist item(s)",
                missing=missing,
            )
        return SemanticGradeResult(
            run_id=run_id,
            semantically_correct=True,
            rationale="all acceptance-checklist items present in produced output",
            missing=(),
        )
