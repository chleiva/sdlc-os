"""Canary rollout routing (master spec §14.15):

"A platform release is versioned and rolled out as a canary: a new
orchestrator/dispatcher/Registry-Service version is first routed a small
fraction of new triggers ... before a full rollout, so a platform-level
regression is caught against a bounded blast radius rather than every
tenant's next run."

`CanaryRouter` is deterministic hash-based bucketing: the same trigger
identifier (a job-dispatcher trigger id, a tenant id, a run id -- whatever
the caller uses to key its own idempotency) always routes to the same
version for a given router configuration, and across a large population
of distinct identifiers the fraction routed to the new version converges
tightly on the configured `new_version_fraction`. This is what makes a
canary a real, bounded blast radius rather than a coin flip re-decided on
every retry (which would let an unlucky trigger flap between old and new
code paths across retries).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

_HASH_SPACE = 2**256  # sha256 output space


def _unit_interval_hash(salted_identifier: str) -> float:
    """Map an arbitrary string onto [0, 1) uniformly and deterministically."""
    digest = hashlib.sha256(salted_identifier.encode("utf-8")).digest()
    as_int = int.from_bytes(digest, byteorder="big")
    return as_int / _HASH_SPACE


@dataclass(frozen=True)
class CanaryDecision:
    identifier: str
    version: str
    bucket_value: float  # the identifier's position in [0, 1) -- exposed for auditing/debugging


class CanaryRouter:
    """Routes a trigger/tenant/run identifier to either `old_version` or
    `new_version`, with `new_version_fraction` of the identifier space
    (in expectation, over many distinct identifiers) landing on
    `new_version`.

    Bucketing is `sha256(salt:identifier)`-based: deterministic (same
    inputs -> same output, forever -- no per-call randomness, no
    flapping across retries of the same trigger) and, because sha256 is
    a good hash function, uniform enough that the realized fraction over
    a few thousand distinct identifiers lands within a tight tolerance of
    `new_version_fraction` (see tests/test_canary.py).
    """

    def __init__(
        self,
        *,
        old_version: str,
        new_version: str,
        new_version_fraction: float,
        salt: str = "",
    ) -> None:
        if not (0.0 <= new_version_fraction <= 1.0):
            raise ValueError(f"new_version_fraction must be in [0, 1], got {new_version_fraction!r}")
        if old_version == new_version:
            raise ValueError("old_version and new_version must differ -- a canary with no distinct new version routes nothing")
        self.old_version = old_version
        self.new_version = new_version
        self.new_version_fraction = new_version_fraction
        self.salt = salt

    def bucket_value(self, identifier: str) -> float:
        return _unit_interval_hash(f"{self.salt}:{identifier}")

    def route(self, identifier: str) -> CanaryDecision:
        value = self.bucket_value(identifier)
        version = self.new_version if value < self.new_version_fraction else self.old_version
        return CanaryDecision(identifier=identifier, version=version, bucket_value=value)

    def is_canary(self, identifier: str) -> bool:
        return self.route(identifier).version == self.new_version


def measure_split(router: CanaryRouter, identifiers: list[str]) -> dict:
    """Route every identifier and summarize the realized split -- the
    "measurably limits blast radius" half of the acceptance criterion.
    Pure/side-effect-free: callers that also want an audit trail record
    one themselves (see release_manager.py)."""
    decisions = [router.route(i) for i in identifiers]
    new_count = sum(1 for d in decisions if d.version == router.new_version)
    total = len(decisions)
    return {
        "total": total,
        "new_version_count": new_count,
        "old_version_count": total - new_count,
        "realized_new_version_fraction": (new_count / total) if total else 0.0,
        "configured_new_version_fraction": router.new_version_fraction,
    }
