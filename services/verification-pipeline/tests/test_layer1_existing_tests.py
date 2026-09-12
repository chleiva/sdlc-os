"""Layer 1 -- Existing test suite (Sec. 11.1 acceptance criterion):
"a pre-existing failure unrelated to the change is flagged, not papered
over or silently skipped."

Also proves the real F3 CI-contract MCP round-trip actually happens
(not just the local pytest run).
"""
from verification_pipeline.layers.existing_tests import run_existing_test_suite_layer

SCOPE = ["tests/test_existing_clean.py", "tests/test_preexisting_failure.py"]
PREEXISTING_ID = "tests/test_preexisting_failure.py::test_known_preexisting_bug"


def test_clean_scope_passes(target_repo):
    result = run_existing_test_suite_layer(repo_root=target_repo, scope_paths=["tests/test_existing_clean.py"])
    assert result.status == "pass"
    assert not result.blocks_human_review


def test_known_preexisting_failure_is_flagged_not_papered_over(target_repo):
    result = run_existing_test_suite_layer(
        repo_root=target_repo, scope_paths=SCOPE, known_preexisting_failures={PREEXISTING_ID}
    )
    assert result.status == "flagged-pass"
    assert not result.blocks_human_review
    # Never silently dropped: it must be present, named, in the report.
    assert result.details["pre_existing_failures"] == [PREEXISTING_ID]
    assert result.details["new_failures"] == []


def test_same_failure_without_baseline_is_a_new_blocking_failure(target_repo):
    """The exact same failing test, with no known-pre-existing baseline
    recorded, is NOT silently treated as pre-existing -- it blocks."""
    result = run_existing_test_suite_layer(repo_root=target_repo, scope_paths=SCOPE, known_preexisting_failures=set())
    assert result.status == "fail"
    assert result.blocks_human_review
    assert result.details["new_failures"] == [PREEXISTING_ID]
    assert result.details["pre_existing_failures"] == []


def test_real_ci_contract_mcp_round_trip_happened(target_repo):
    """Proves this layer actually calls F3's real CI MCP stub (not a
    hand-copied canned dict) -- trigger-run followed by
    get-run-status-result, both real subprocess/MCP calls."""
    result = run_existing_test_suite_layer(repo_root=target_repo, scope_paths=["tests/test_existing_clean.py"])
    ci_audit = result.details["ci_audit"]
    assert ci_audit["trigger_run"]["outcome"] == "ok"
    assert "run_id" in ci_audit["trigger_run"]["data"]
    assert ci_audit["get_run_status_result"]["outcome"] == "ok"
