"""End-to-end integration: assembles all seven REAL layer implementations
(not synthetic LayerResults) into one VerificationPipeline attempt, for
both a fully-clean scenario and a scenario where layers 1-4 are clean but
layer 7 trips the risk checkpoint -- the same "necessary but not
sufficient" property as test_verdict_not_sufficient.py, proven here
against the real layer functions rather than hand-built LayerResults.
"""
from verification_pipeline.budgets import RetryBudget
from verification_pipeline.layers.acceptance_mapping import run_acceptance_criteria_mapping_layer
from verification_pipeline.layers.completion_check import TouchedSymbol, run_completion_check_layer
from verification_pipeline.layers.existing_tests import run_existing_test_suite_layer
from verification_pipeline.layers.isolated_review import run_isolated_review_layer
from verification_pipeline.layers.regression_check import run_regression_check_layer
from verification_pipeline.layers.security_scan import run_security_scan_layer
from verification_pipeline.layers.static_analysis import run_static_analysis_layer
from verification_pipeline.pipeline import VerificationPipeline

TENANT = "tenant-acme"
REPOSITORY = "fixture/multi-pkg-repo"
SYMBOL = TouchedSymbol(name="compute_total", origin_file="pkg_a/billing/util.py", origin_line=7, change_kind="renamed")
ALL_FIVE_REFERENCE_FILES = {
    "pkg_a/billing/service.py",
    "pkg_a/billing/tests/test_util.py",
    "pkg_a/billing/util.py",
    "pkg_b/reporting/report.py",
    "pkg_b/reporting/legacy.py",
}
OPENED_ONLY_PKG_A = {"pkg_a/billing/service.py", "pkg_a/billing/tests/test_util.py", "pkg_a/billing/util.py"}


def _run_layers_1_to_6(valid_plan, target_repo):
    return [
        run_existing_test_suite_layer(repo_root=target_repo, scope_paths=["tests/test_existing_clean.py"]),
        run_acceptance_criteria_mapping_layer(plan=valid_plan, repo_root=target_repo),
        run_static_analysis_layer(paths=["src/sample_pkg/billing.py"], cwd=target_repo),
        run_security_scan_layer(code_paths=["src/sample_pkg/billing.py"], dependency_files=[], cwd=target_repo),
        run_isolated_review_layer(diff_text="def compute_discount(...): ...", plan_summary="Add discount calc", risk_tier="low"),
        run_regression_check_layer(baseline_path=None, module_path=target_repo, attr="unused"),
    ]


def test_fully_clean_run_is_eligible_for_human_review(valid_plan, target_repo):
    layers = _run_layers_1_to_6(valid_plan, target_repo)
    layers.append(
        run_completion_check_layer(
            tenant_id=TENANT,
            repository=REPOSITORY,
            touched_symbols=[SYMBOL],
            opened_files=ALL_FIVE_REFERENCE_FILES,
            declared_in_scope=["pkg_a/**"],
        )
    )

    pipeline = VerificationPipeline(story_id="SDLC-4242", retry_budget=RetryBudget.for_story_size("S"))
    report = pipeline.submit_attempt(layers)

    assert report.eligible_for_human_review is True
    assert report.escalated is False


def test_clean_1_through_4_but_layer_7_checkpoint_is_not_eligible(valid_plan, target_repo):
    layers = _run_layers_1_to_6(valid_plan, target_repo)
    for layer in layers[:4]:
        assert layer.status in ("pass", "flagged-pass")

    layers.append(
        run_completion_check_layer(
            tenant_id=TENANT,
            repository=REPOSITORY,
            touched_symbols=[SYMBOL],
            opened_files=OPENED_ONLY_PKG_A,  # narrower -- misses the pkg_b callers
            declared_in_scope=["pkg_a/**"],
        )
    )

    pipeline = VerificationPipeline(story_id="SDLC-4242-b", retry_budget=RetryBudget.for_story_size("S"))
    report = pipeline.submit_attempt(layers)

    assert report.by_name("cross_codebase_completion_check").status == "checkpoint"
    assert report.eligible_for_human_review is False
    # Not escalated yet either -- one attempt, budget not exhausted.
    assert report.escalated is False
