"""Acceptance criterion: "A model-version bump (including a
BF16<->quantized-build switch) is a reviewed change, re-validated against
the evaluation suite before promotion -- not a background update."
"""

from __future__ import annotations

from tenant_cell.model_version_governance import (
    EvaluationSuiteResult,
    ModelArtifact,
    PromotionDenialReason,
    promote_model_version,
)

CANDIDATE = ModelArtifact(name="ornith-1.5-35b-a3b", checksum="sha256:new-nvfp4-build", build="nvfp4")


def test_unreviewed_bump_with_no_evaluation_result_is_rejected():
    result = promote_model_version(CANDIDATE, evaluation=None)
    assert not result.approved
    assert result.denial_reason == PromotionDenialReason.NO_EVALUATION_RESULT


def test_bump_with_a_stale_mismatched_evaluation_checksum_is_rejected():
    stale_eval = EvaluationSuiteResult(
        artifact_checksum="sha256:old-bf16-build",  # a different artifact's result
        suite_version="section-20.1-v3",
        passed=True,
        reviewed_by="alice@example.com",
    )
    result = promote_model_version(CANDIDATE, evaluation=stale_eval)
    assert not result.approved
    assert result.denial_reason == PromotionDenialReason.CHECKSUM_MISMATCH


def test_bump_with_a_failed_evaluation_suite_is_rejected():
    failed_eval = EvaluationSuiteResult(
        artifact_checksum=CANDIDATE.checksum,
        suite_version="section-20.1-v3",
        passed=False,
        reviewed_by="alice@example.com",
    )
    result = promote_model_version(CANDIDATE, evaluation=failed_eval)
    assert not result.approved
    assert result.denial_reason == PromotionDenialReason.EVALUATION_FAILED


def test_passing_evaluation_with_no_recorded_reviewer_is_rejected():
    unreviewed_eval = EvaluationSuiteResult(
        artifact_checksum=CANDIDATE.checksum,
        suite_version="section-20.1-v3",
        passed=True,
        reviewed_by=None,
    )
    result = promote_model_version(CANDIDATE, evaluation=unreviewed_eval)
    assert not result.approved
    assert result.denial_reason == PromotionDenialReason.NOT_REVIEWED


def test_a_properly_reviewed_and_passing_bump_is_approved():
    good_eval = EvaluationSuiteResult(
        artifact_checksum=CANDIDATE.checksum,
        suite_version="section-20.1-v3",
        passed=True,
        reviewed_by="alice@example.com",
    )
    result = promote_model_version(CANDIDATE, evaluation=good_eval)
    assert result.approved
    assert result.denial_reason is None
