"""Model-version-bump governance (spec Section 13.5).

"A deployed model version is pinned by weight checksum ... a version bump
-- including a switch between the BF16 and NVFP4 builds of the same
model -- is a reviewed change, re-run against the full evaluation suite
(Section 20.1) before promotion, not a background update."

`promote_model_version` is the gate: it refuses to promote a candidate
model artifact to serving unless a recorded evaluation-suite result is
attached, that result was actually run *against this exact candidate*
(checksum match -- an evaluation result for a different artifact does not
authorize promoting this one), and that result passed. This is a pure
function over caller-supplied records; it does not itself run Section
20.1's evaluation suite (out of D6's scope) or persist the promotion
decision anywhere -- a real deployment wires its result into whatever
change-management/audit trail Section 16.3 specifies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PromotionDenialReason(str, Enum):
    NO_EVALUATION_RESULT = "no-evaluation-result"
    CHECKSUM_MISMATCH = "checksum-mismatch"
    EVALUATION_FAILED = "evaluation-failed"
    NOT_REVIEWED = "not-reviewed"


@dataclass(frozen=True)
class ModelArtifact:
    """A checksum-pinned model artifact (spec Section 13.5)."""

    name: str
    checksum: str
    build: str  # e.g. "bf16" | "nvfp4"


@dataclass(frozen=True)
class EvaluationSuiteResult:
    """A recorded Section 20.1 evaluation-suite run against one specific artifact."""

    artifact_checksum: str
    suite_version: str
    passed: bool
    reviewed_by: str | None = None  # the human who reviewed/approved this promotion


@dataclass(frozen=True)
class PromotionResult:
    approved: bool
    artifact: ModelArtifact
    reason: str
    denial_reason: PromotionDenialReason | None = None


def promote_model_version(
    candidate: ModelArtifact,
    evaluation: EvaluationSuiteResult | None,
    *,
    require_human_review: bool = True,
) -> PromotionResult:
    if evaluation is None:
        return PromotionResult(
            approved=False,
            artifact=candidate,
            reason=(
                f"no evaluation-suite result attached for candidate artifact "
                f"'{candidate.name}' (checksum {candidate.checksum}) -- a model-version bump "
                "is a reviewed change, re-validated against the evaluation suite before "
                "promotion, not a background update (Section 13.5)"
            ),
            denial_reason=PromotionDenialReason.NO_EVALUATION_RESULT,
        )

    if evaluation.artifact_checksum != candidate.checksum:
        return PromotionResult(
            approved=False,
            artifact=candidate,
            reason=(
                f"attached evaluation result was run against checksum "
                f"'{evaluation.artifact_checksum}', not the candidate's checksum "
                f"'{candidate.checksum}' -- a stale/mismatched evaluation does not authorize "
                "promoting this artifact"
            ),
            denial_reason=PromotionDenialReason.CHECKSUM_MISMATCH,
        )

    if not evaluation.passed:
        return PromotionResult(
            approved=False,
            artifact=candidate,
            reason=f"evaluation suite '{evaluation.suite_version}' did not pass for this candidate",
            denial_reason=PromotionDenialReason.EVALUATION_FAILED,
        )

    if require_human_review and not evaluation.reviewed_by:
        return PromotionResult(
            approved=False,
            artifact=candidate,
            reason=(
                "evaluation passed but no reviewer is recorded -- a version bump is a "
                "reviewed change (Section 13.5), not an automatic promotion on a green suite run"
            ),
            denial_reason=PromotionDenialReason.NOT_REVIEWED,
        )

    return PromotionResult(
        approved=True,
        artifact=candidate,
        reason=(
            f"evaluation suite '{evaluation.suite_version}' passed for checksum "
            f"{candidate.checksum}, reviewed by {evaluation.reviewed_by}"
        ),
    )
