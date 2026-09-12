"""Orchestrates one story's verification attempts with a bounded retry
budget (Sec. 11, Sec. 9.3's "stuck checkpoint", the D7 brief's "bounded
retry budget: failures loop back to implementation with a retry counter
... escalating with full diagnostics" once exhausted).

This module does not itself decide *how* to fix a failing attempt (that's
the implementer's job, upstream of D7) -- it is the bookkeeping that makes
"never an infinite loop or silent resubmission" (Sec. 11) a property of
the code: each call to `submit_attempt` either (a) reports the change
eligible for human review, (b) consumes one retry and signals "loop back",
or (c) marks the report escalated with every attempt's full diagnostics
attached, once the budget is exhausted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .budgets import RetryBudget
from .layers.base import LayerResult
from .report import VerificationReport


@dataclass
class VerificationPipeline:
    story_id: str
    retry_budget: RetryBudget
    history: list[VerificationReport] = field(default_factory=list)

    def submit_attempt(self, layer_results: list[LayerResult]) -> VerificationReport:
        """Record one verification attempt's layer results and resolve it
        against the retry budget. Returns the VerificationReport for THIS
        attempt; `.eligible_for_human_review` / `.escalated` on it (plus
        `pipeline.history` for every prior attempt) is what a caller
        branches on.
        """
        attempt_number = len(self.history) + 1
        report = VerificationReport(
            story_id=self.story_id,
            attempt=attempt_number,
            layer_results=layer_results,
            retry_attempts_used=self.retry_budget.attempts_used,
            retry_attempts_max=self.retry_budget.max_attempts,
        )

        if report.eligible_for_human_review:
            self.history.append(report)
            return report

        if self.retry_budget.exhausted:
            report.escalated = True
            self.history.append(report)
            return report

        # Loop back to implementation: one retry consumed, never a silent
        # resubmission -- the caller must observe `report.escalated is
        # False` and `not report.eligible_for_human_review` to know a
        # retry (not success, not escalation) is what happened.
        self.retry_budget.consume()
        report.retry_attempts_used = self.retry_budget.attempts_used
        self.history.append(report)
        return report

    @property
    def full_diagnostics(self) -> list[dict]:
        """Every attempt's full report, in order -- what an escalation to
        a human carries (Sec. 9.3: "pause and hand back with full
        diagnostic context")."""
        return [r.to_dict() for r in self.history]
