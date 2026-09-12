"""Bounded retry budget (Sec. 9.3's "stuck checkpoint" / the D7 brief:
"failures loop back to implementation with a retry counter ... exhausting
it escalates with full diagnostics"). Both paths proven: "retry consumed,
loops back" and "budget exhausted, escalates"."""
from verification_pipeline.budgets import RetryBudget
from verification_pipeline.layers.base import LayerResult
from verification_pipeline.pipeline import VerificationPipeline

FAILING_LAYERS = [
    LayerResult("existing_test_suite", "fail", "1 new failure", {}),
    LayerResult("acceptance_criteria_mapping", "pass", "ok", {}),
    LayerResult("static_analysis", "pass", "ok", {}),
    LayerResult("security_scan", "pass", "ok", {}),
    LayerResult("isolated_context_review", "pass", "ok", {}),
    LayerResult("behavioral_regression_check", "skipped", "n/a", {}),
    LayerResult("cross_codebase_completion_check", "pass", "ok", {}),
]

PASSING_LAYERS = [
    LayerResult("existing_test_suite", "pass", "ok", {}),
    LayerResult("acceptance_criteria_mapping", "pass", "ok", {}),
    LayerResult("static_analysis", "pass", "ok", {}),
    LayerResult("security_scan", "pass", "ok", {}),
    LayerResult("isolated_context_review", "pass", "ok", {}),
    LayerResult("behavioral_regression_check", "skipped", "n/a", {}),
    LayerResult("cross_codebase_completion_check", "pass", "ok", {}),
]


def test_failure_with_budget_remaining_consumes_one_retry_and_loops_back():
    pipeline = VerificationPipeline(story_id="SDLC-1", retry_budget=RetryBudget.for_story_size("S"))
    report = pipeline.submit_attempt(FAILING_LAYERS)

    assert report.eligible_for_human_review is False
    assert report.escalated is False  # loop back, not escalation
    assert pipeline.retry_budget.attempts_used == 1
    assert pipeline.retry_budget.remaining == 1


def test_budget_exhaustion_escalates_with_full_diagnostics_not_silent_retry():
    pipeline = VerificationPipeline(story_id="SDLC-1", retry_budget=RetryBudget.for_story_size("S"))  # max_attempts=2

    report1 = pipeline.submit_attempt(FAILING_LAYERS)
    assert report1.escalated is False
    assert pipeline.retry_budget.attempts_used == 1

    report2 = pipeline.submit_attempt(FAILING_LAYERS)
    assert report2.escalated is False
    assert pipeline.retry_budget.attempts_used == 2
    assert pipeline.retry_budget.exhausted

    report3 = pipeline.submit_attempt(FAILING_LAYERS)
    assert report3.escalated is True
    assert report3.eligible_for_human_review is False
    # attempts_used must NOT increase past the max -- no silent extra retry.
    assert pipeline.retry_budget.attempts_used == 2

    diagnostics = pipeline.full_diagnostics
    assert len(diagnostics) == 3  # every attempt's full report is retained
    assert diagnostics[-1]["escalated"] is True


def test_success_never_consumes_a_retry():
    pipeline = VerificationPipeline(story_id="SDLC-2", retry_budget=RetryBudget.for_story_size("S"))
    report = pipeline.submit_attempt(PASSING_LAYERS)
    assert report.eligible_for_human_review is True
    assert report.escalated is False
    assert pipeline.retry_budget.attempts_used == 0
