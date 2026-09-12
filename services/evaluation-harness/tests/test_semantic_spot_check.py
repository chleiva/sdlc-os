"""Semantic-correctness spot-check sampling (Section 11.2, Section 20.1
bullet 6) -- the direct countermeasure to tests-pass-but-wrong.

Acceptance criterion under test: "A 'passed' run's semantic-correctness
spot-check sampling actually runs and produces a number distinct from
raw test-pass rate." The fixture below constructs exactly that: every
seeded run reaches `completed` (raw_test_pass_rate == 1.0), but roughly
half of them produced output missing an acceptance-checklist item, so
`semantic_correctness_rate` is real, sampled, and provably lower.
"""

from __future__ import annotations

from evaluation_harness.semantic_grader import ScriptedSemanticGrader
from evaluation_harness.spot_check import SemanticSpotChecker

from .conftest import create_run


class FixtureArtifactSource:
    """Synthetic `RunArtifactSource`: half the runs "actually" did the
    job (their produced output contains every checklist phrase), half
    only satisfied the letter of the tests (missing one required
    phrase) -- the semantic-vs-tests-pass gap Section 11.2 describes.
    """

    def __init__(self, semantically_broken_run_ids: set[str]):
        self._broken = semantically_broken_run_ids

    def acceptance_checklist(self, run_id: str) -> list[str]:
        return ["input validated", "edge case handled", "error path logged"]

    def produced_output(self, run_id: str) -> str:
        if run_id in self._broken:
            # Missing "error path logged" -- tests passed, but the task's
            # actual intent (per its own checklist) was not fully met.
            return "input validated. edge case handled. tests green."
        return "input validated. edge case handled. error path logged. tests green."


def _seed_completed_runs(registry, tenant_id, count: int):
    from run_registry import stages
    from run_registry.result import Outcome

    run_ids = []
    for _ in range(count):
        run = create_run(registry, tenant_id)
        for stage in (
            stages.RESEARCH,
            stages.PLAN_AUTHORING,
            stages.PLAN_APPROVAL_GATE,
            stages.IMPLEMENTATION,
            stages.VERIFICATION,
            stages.CHANGE_REVIEW_GATE,
            stages.PACKAGING,
            stages.RETROSPECTIVE,
            stages.COMPLETED,
        ):
            result = registry.transition_stage(
                tenant_id=tenant_id, run_id=run.id, expected_version=run.version, next_stage=stage
            )
            assert result.outcome == Outcome.OK, result
            run = result.data
        run_ids.append(run.id)
    return run_ids


def test_semantic_correctness_rate_is_distinct_from_raw_test_pass_rate(registry, tenant_id):
    run_ids = _seed_completed_runs(registry, tenant_id, count=10)
    # Half of the "passed" runs are semantically broken despite green tests.
    broken = set(run_ids[:5])

    checker = SemanticSpotChecker(
        registry=registry,
        grader=ScriptedSemanticGrader(),
        artifact_source=FixtureArtifactSource(broken),
        sample_fraction=1.0,  # sample everything, so the gap is deterministic to assert on
        rng_seed=42,
    )
    report = checker.run(tenant_id=tenant_id)

    assert report.raw_test_pass_rate == 1.0  # every seeded run reached `completed`
    assert report.semantic_correctness_rate is not None
    assert report.semantic_correctness_rate == 0.5  # exactly half the sample graded correct
    # The two numbers are computed independently and legitimately differ.
    assert report.semantic_correctness_rate != report.raw_test_pass_rate
    assert report.sample_size == 10


def test_sample_fraction_controls_how_many_passed_runs_are_graded(registry, tenant_id):
    run_ids = _seed_completed_runs(registry, tenant_id, count=20)
    checker = SemanticSpotChecker(
        registry=registry,
        grader=ScriptedSemanticGrader(),
        artifact_source=FixtureArtifactSource(set()),
        sample_fraction=0.25,
        rng_seed=7,
    )
    report = checker.run(tenant_id=tenant_id)
    assert report.sample_size == 5  # 25% of 20
    assert set(report.sampled_run_ids) <= set(run_ids)


def test_no_completed_runs_yields_no_semantic_sample(registry, tenant_id):
    checker = SemanticSpotChecker(
        registry=registry,
        grader=ScriptedSemanticGrader(),
        artifact_source=FixtureArtifactSource(set()),
        sample_fraction=0.5,
    )
    report = checker.run(tenant_id=tenant_id)
    assert report.total_runs == 0
    assert report.raw_test_pass_rate == 0.0
    assert report.semantic_correctness_rate is None


def test_scripted_grader_reports_missing_checklist_items():
    from evaluation_harness.semantic_grader import ScriptedSemanticGrader

    grader = ScriptedSemanticGrader()
    result = grader.grade(
        run_id="r1",
        produced_output="input validated. edge case handled.",
        checklist=["input validated", "edge case handled", "error path logged"],
    )
    assert result.semantically_correct is False
    assert result.missing == ("error path logged",)
