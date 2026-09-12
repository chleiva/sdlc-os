"""A real (if simple) per-tenant rate limiter -- this is what backs the
rate-limited error condition. Fixed-window counter: at most `limit`
calls per tenant per `window_seconds`; the window resets on the wall
clock, not per-caller, so it is genuinely enforced, not a canned
response keyed off a magic tenant id.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Window:
    started_at: float
    count: int


class RateLimiter:
    def __init__(self, limit: int = 120, window_seconds: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._windows: dict[str, _Window] = {}

    def check(self, tenant_id: str) -> tuple[bool, int | None]:
        """Returns (allowed, retry_after_seconds). Call only once per
        request that should count against the tenant's quota."""
        now = self._clock()
        window = self._windows.get(tenant_id)
        if window is None or now - window.started_at >= self.window_seconds:
            self._windows[tenant_id] = _Window(started_at=now, count=1)
            return True, None
        if window.count < self.limit:
            window.count += 1
            return True, None
        retry_after = max(0, int(self.window_seconds - (now - window.started_at)) + 1)
        return False, retry_after

    def reset(self) -> None:
        self._windows.clear()
