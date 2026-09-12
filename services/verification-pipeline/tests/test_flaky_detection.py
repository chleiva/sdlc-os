"""Flaky-test detection: re-run on failure, distinguish flaky from
genuine regression. fixtures/flaky/test_flaky_sample.py fails on attempt
1 and passes on attempt 2 on a FIXED schedule (a counter file), so the
distinction is proven deterministically, not asserted."""
from verification_pipeline.flaky import rerun_failures_and_classify
from verification_pipeline.local_pytest import run_pytest

FLAKY_NODE_ID = "test_flaky_sample.py::test_intermittent_failure"
GENUINE_NODE_ID = "test_genuine_failure.py::test_always_fails"


def test_initial_run_shows_both_as_failing(flaky_dir, reset_flaky_counter):
    result = run_pytest(["."], cwd=flaky_dir)
    assert set(result.failing_node_ids) == {FLAKY_NODE_ID, GENUINE_NODE_ID}


def test_rerun_correctly_distinguishes_flaky_from_genuine(flaky_dir, reset_flaky_counter):
    initial = run_pytest(["."], cwd=flaky_dir)
    check = rerun_failures_and_classify(initial.failing_node_ids, cwd=flaky_dir)

    assert check.flaky == [FLAKY_NODE_ID]
    assert check.genuine_failures == [GENUINE_NODE_ID]
    assert check.any_genuine_failure is True


def test_a_purely_flaky_run_has_no_genuine_failures(flaky_dir, reset_flaky_counter):
    initial = run_pytest([FLAKY_NODE_ID], cwd=flaky_dir)
    assert initial.failing_node_ids == [FLAKY_NODE_ID]
    check = rerun_failures_and_classify(initial.failing_node_ids, cwd=flaky_dir)
    assert check.flaky == [FLAKY_NODE_ID]
    assert check.any_genuine_failure is False
