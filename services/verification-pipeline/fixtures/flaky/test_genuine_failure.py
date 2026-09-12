"""Always fails, regardless of rerun -- the "genuine failure" counterpart
to test_flaky_sample.py's fixed-schedule flake, so the flaky-detection
test can prove BOTH classifications happen correctly in the same run,
not just that flaky detection exists."""


def test_always_fails():
    assert False, "genuine failure -- must never be reclassified as flaky"
