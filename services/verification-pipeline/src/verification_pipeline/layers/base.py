"""Shared result type for every Sec. 11.1 layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["pass", "fail", "flagged-pass", "checkpoint", "skipped", "error"]
# "pass"          -- layer ran, no issues.
# "flagged-pass"  -- layer ran, did not block, but surfaced something the
#                    report must never silently drop (e.g. Layer 1's
#                    pre-existing-and-unrelated test failure).
# "fail"          -- layer ran and blocks Sec. 11's human-review gate.
# "checkpoint"    -- layer ran and trips a Sec. 9.3 risk checkpoint (pause
#                    for a human, distinct from an outright failure).
# "skipped"       -- layer legitimately not applicable (e.g. Layer 6 with
#                    no recorded baseline for this scope).
# "error"         -- the layer itself could not complete (infra problem),
#                    always blocking -- never treated as a silent pass.


@dataclass
class LayerResult:
    name: str
    status: Status
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_human_review(self) -> bool:
        """Sec. 11: a change is not eligible for the human change-review
        gate until it passes every applicable layer. "flagged-pass" and
        "skipped" do not block; everything else does."""
        return self.status in ("fail", "checkpoint", "error")
