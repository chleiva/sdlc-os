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


def test_a_pytest_collection_crash_is_a_real_failure_not_a_vacuous_pass(tmp_path):
    """Real live-run bug this closes: a collection-time crash (pytest
    exit code 2, "Interrupted") produces a JUnit XML with no <testcase>
    elements at all -- nothing for a per-test failure/pass count to
    attach to. Before this fix, that meant `failing_node_ids` was empty
    and this layer reported a clean-looking "0/0 existing tests passed"
    PASS for a test suite that never actually ran a single test.

    Reproduces the exact real shape that happened on a live run: an
    implementation agent (with no tool to execute code) wrote its own
    "run the tests for me" helper file named to match pytest's own
    `test_*.py` discovery pattern, whose module-level code crashes on
    import -- aborting the whole pytest session before the real test
    file ever runs.
    """
    (tmp_path / "hello.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n")
    (tmp_path / "test_hello.py").write_text(
        "import hello\n\n\ndef test_greet():\n    assert hello.greet('World') == 'Hello, World!'\n"
    )
    # The bogus helper: matches pytest's test_*.py discovery pattern, and
    # crashes at *module import* time (not inside a test function) --
    # exactly what made the real live run's `test_runner.py` take down
    # collection entirely, rather than just failing its own (nonexistent)
    # test.
    (tmp_path / "test_runner.py").write_text(
        "import subprocess\n"
        "result = subprocess.run(['this-binary-does-not-exist'], capture_output=True)\n"
    )

    result = run_existing_test_suite_layer(
        repo_root=tmp_path,
        scope_paths=["hello.py", "test_hello.py", "test_runner.py"],
    )

    assert result.status == "fail"
    assert result.blocks_human_review
    assert "0/0" not in result.summary  # must not read as an innocuous empty pass
    assert result.details["returncode"] not in (0, 1)


def test_real_ci_contract_mcp_round_trip_happened(target_repo):
    """Proves this layer actually calls F3's real CI MCP stub (not a
    hand-copied canned dict) -- trigger-run followed by
    get-run-status-result, both real subprocess/MCP calls."""
    result = run_existing_test_suite_layer(repo_root=target_repo, scope_paths=["tests/test_existing_clean.py"])
    ci_audit = result.details["ci_audit"]
    assert ci_audit["trigger_run"]["outcome"] == "ok"
    assert "run_id" in ci_audit["trigger_run"]["data"]
    assert ci_audit["get_run_status_result"]["outcome"] == "ok"
