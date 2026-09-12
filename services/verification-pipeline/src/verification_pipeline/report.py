"""The verification report D9's human change-review flow presents to a
human (D7's brief: "D7 produces the verification report D9's flow
presents to a human.")
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .layers.base import LayerResult


@dataclass
class VerificationReport:
    story_id: str
    attempt: int
    layer_results: list[LayerResult] = field(default_factory=list)
    retry_attempts_used: int = 0
    retry_attempts_max: int = 0
    escalated: bool = False

    @property
    def all_layers_ran(self) -> bool:
        expected = {
            "existing_test_suite",
            "acceptance_criteria_mapping",
            "static_analysis",
            "security_scan",
            "isolated_context_review",
            "behavioral_regression_check",
            "cross_codebase_completion_check",
        }
        return expected.issubset({r.name for r in self.layer_results})

    @property
    def eligible_for_human_review(self) -> bool:
        """Sec. 11: "A change is not eligible for the human change-review
        gate until it passes every applicable layer below." Sec. 11.2:
        "Passing tests is necessary, not sufficient" -- layers 1-4 all
        passing never substitutes for layers 5-7 also completing clean.
        `skipped` layers (Sec. 11.1's "where applicable") do not block.
        """
        if not self.all_layers_ran:
            return False
        return not any(r.blocks_human_review for r in self.layer_results)

    def by_name(self, name: str) -> LayerResult | None:
        for r in self.layer_results:
            if r.name == name:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "story_id": self.story_id,
            "attempt": self.attempt,
            "eligible_for_human_review": self.eligible_for_human_review,
            "retry_attempts_used": self.retry_attempts_used,
            "retry_attempts_max": self.retry_attempts_max,
            "escalated": self.escalated,
            "layers": [
                {"name": r.name, "status": r.status, "summary": r.summary, "details": r.details}
                for r in self.layer_results
            ],
        }
