"""A test that fails on a fixed schedule -- fails on attempt 1, passes on
attempt 2 -- via a counter file, so flaky-vs-genuine detection can be
proven deterministically rather than relying on real timing/race-based
flakiness (which wouldn't be reliably reproducible in a test suite).

The counter file must be reset (deleted) by the test harness before each
scenario; see tests/test_flaky_detection.py.
"""
from pathlib import Path

COUNTER_FILE = Path(__file__).parent / ".flaky_attempt_counter"


def test_intermittent_failure():
    count = 1
    if COUNTER_FILE.exists():
        count = int(COUNTER_FILE.read_text().strip()) + 1
    COUNTER_FILE.write_text(str(count))
    assert count >= 2, f"deliberately fails on attempt {count} (fixed schedule: fails on 1, passes from 2 onward)"
