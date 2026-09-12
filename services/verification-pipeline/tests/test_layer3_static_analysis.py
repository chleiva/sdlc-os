"""Layer 3 -- Static analysis: real ruff + real mypy against real fixture
violations (D7 brief: "wire a real linter/type-checker ... against a real
fixture with a real lint violation, prove it's caught")."""
from verification_pipeline.layers.static_analysis import (
    MypyTypeChecker,
    RuffLinter,
    run_static_analysis_layer,
)


def test_clean_file_passes(target_repo):
    result = run_static_analysis_layer(paths=["src/sample_pkg/billing.py"], cwd=target_repo)
    assert result.status == "pass"


def test_real_ruff_catches_real_unused_import(target_repo):
    result = run_static_analysis_layer(
        paths=["src/sample_pkg/lint_violation.py"], cwd=target_repo, analyzers=[RuffLinter()]
    )
    assert result.status == "fail"
    codes = {f["code"] for f in result.details["findings"]}
    assert "F401" in codes


def test_real_mypy_catches_real_type_error(target_repo):
    result = run_static_analysis_layer(
        paths=["src/sample_pkg/type_violation.py"], cwd=target_repo, analyzers=[MypyTypeChecker()]
    )
    assert result.status == "fail"
    assert any(f["tool"] == "mypy" for f in result.details["findings"])


def test_combined_analyzers_catch_the_violation_that_applies(target_repo):
    result = run_static_analysis_layer(paths=["src/sample_pkg/lint_violation.py"], cwd=target_repo)
    assert result.status == "fail"
    assert result.blocks_human_review
