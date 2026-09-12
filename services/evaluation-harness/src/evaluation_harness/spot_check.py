"""Semantic-correctness spot-check sampling (Section 11.2, Section 20.1
bullet 6): pulls a configurable-fraction random sample of "passed" runs
from `run_registry` and grades them with a `SemanticGrader`, producing a
`semantic_correctness_rate` that is reported SEPARATELY from the raw
test-pass rate a `RegistryService` read already gives for free.

Deliberately independent computations: `raw_test_pass_rate` comes only
from `Run.stage` (did the run reach `completed`?); `semantic_correctness_rate`
comes only from grading a sample's produced output against its
acceptance checklist. Nothing here blends the two into one figure --
see `tests/test_semantic_spot_check.py` for a fixture where they
legitimately differ.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from run_registry import RegistryService, stages
from run_registry.models import Run

from evaluation_harness.semantic_grader import SemanticGrader, SemanticGradeResult


@runtime_checkable
class RunArtifactSource(Protocol):
    """Supplies the two pieces of information the Registry Service does
    not carry: what a run actually produced, and what its acceptance
    checklist was. (F2's `Run`/`Attempt` schema stores workflow state and
    timing, not diff/output content -- that lives with whichever
    component produced it, e.g. D7's verification pipeline or D5's
    source-control integration; this is the pluggable seam a real
    deployment wires those in through.)
    """

    def produced_output(self, run_id: str) -> str: ...

    def acceptance_checklist(self, run_id: str) -> Sequence[str]: ...


@dataclass(frozen=True)
class SpotCheckReport:
    tenant_id: str
    total_runs: int
    completed_runs: int
    raw_test_pass_rate: float
    sample_size: int
    sampled_run_ids: tuple[str, ...]
    semantic_correctness_rate: float | None  # None when nothing was sampled
    grades: tuple[SemanticGradeResult, ...]


class SemanticSpotChecker:
    def __init__(
        self,
        *,
        registry: RegistryService,
        grader: SemanticGrader,
        artifact_source: RunArtifactSource,
        sample_fraction: float = 0.2,
        rng_seed: int | None = None,
    ):
        if not (0.0 < sample_fraction <= 1.0):
            raise ValueError("sample_fraction must be in (0, 1]")
        self._registry = registry
        self._grader = grader
        self._artifacts = artifact_source
        self._sample_fraction = sample_fraction
        self._rng = random.Random(rng_seed)

    def run(self, *, tenant_id: str) -> SpotCheckReport:
        result = self._registry.list_runs(tenant_id=tenant_id, limit=10_000)
        runs: list[Run] = result.data if result.is_ok else []
        total_runs = len(runs)

        completed = [r for r in runs if r.stage == stages.COMPLETED]
        completed_runs = len(completed)
        raw_test_pass_rate = (completed_runs / total_runs) if total_runs else 0.0

        sample_size = max(1, round(completed_runs * self._sample_fraction)) if completed_runs else 0
        sample_size = min(sample_size, completed_runs)
        sampled = self._rng.sample(completed, sample_size) if sample_size else []

        grades = tuple(
            self._grader.grade(
                run_id=run.id,
                produced_output=self._artifacts.produced_output(run.id),
                checklist=self._artifacts.acceptance_checklist(run.id),
            )
            for run in sampled
        )
        semantic_correctness_rate = (
            sum(1 for g in grades if g.semantically_correct) / len(grades) if grades else None
        )

        return SpotCheckReport(
            tenant_id=tenant_id,
            total_runs=total_runs,
            completed_runs=completed_runs,
            raw_test_pass_rate=raw_test_pass_rate,
            sample_size=len(sampled),
            sampled_run_ids=tuple(r.id for r in sampled),
            semantic_correctness_rate=semantic_correctness_rate,
            grades=grades,
        )
