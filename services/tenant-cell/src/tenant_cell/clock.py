"""A tiny injectable-clock abstraction so timing-sensitive code (the
interruption watcher's warning-window budget, cold-start tracking) can be
driven by tests without a real `time.sleep`.

Every timing-sensitive function in this package takes a `Clock`, never
calls `time.monotonic()`/`time.sleep()` directly -- production code uses
`RealClock`, tests use `FakeClock` and advance it explicitly to represent
"this step took N (simulated) seconds" without the test actually waiting
that long. This is the "scaled-down fake clock/timeout in tests, not a
real 2-minute sleep" the brief asks for.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Monotonic seconds. Only differences between two calls are meaningful."""
        ...

    def sleep(self, seconds: float) -> None:
        """Advance time by `seconds` (real wait for RealClock, instant bookkeeping for FakeClock)."""
        ...


class RealClock:
    """Wraps `time.monotonic`/`time.sleep` for production use."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """A controllable clock for tests: `sleep()` advances an internal
    counter instantly rather than blocking, so a test that simulates a
    120-second warning window runs in milliseconds of real wall-clock
    time while every budget comparison in the code under test still sees
    the full 120 (simulated) seconds elapse.
    """

    def __init__(self, start: float = 0.0):
        self._t = start

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot sleep a negative duration")
        self._t += seconds

    def advance(self, seconds: float) -> None:
        """Alias for `sleep`, read more naturally at a test call site
        that isn't simulating a literal sleep (e.g. "the drain step took
        40 simulated seconds")."""
        self.sleep(seconds)
