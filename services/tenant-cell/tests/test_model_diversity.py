"""Acceptance criterion: "The model-diversity requirement is enforced per
cell -- a cell can't be provisioned with only one self-hosted model and
no configured reviewer path."
"""

from __future__ import annotations

from tenant_cell.model_diversity import (
    FrontierEscalationConfig,
    ModelSpec,
    TenantCellModelConfig,
    validate_model_diversity,
)
from tenant_cell.tenant_cell import mark_tenant_cell_ready

ORNITH = ModelSpec(name="ornith-1.5-35b-a3b", base_architecture_lineage="qwen3.5-moe", weight_checksum="sha256:aaa")
QWEN_FINE_TUNE = ModelSpec(name="qwen3.6-ft-reviewer", base_architecture_lineage="qwen3.5-moe", weight_checksum="sha256:bbb")
DISTINCT_SECOND_MODEL = ModelSpec(name="llama4-reviewer", base_architecture_lineage="llama4", weight_checksum="sha256:ccc")


def test_rejects_single_model_with_no_reviewer_path():
    config = TenantCellModelConfig(tenant_id="acme", primary_model=ORNITH)
    result = validate_model_diversity(config)
    assert not result.satisfied
    assert result.route is None

    readiness = mark_tenant_cell_ready(config)
    assert not readiness.ready


def test_rejects_second_model_from_the_same_architecture_lineage():
    """Section 13.4's explicit worked example: a same-family checkpoint
    (a second Qwen3.5/3.6-family model) does NOT satisfy diversity, even
    though a "second model" is technically configured.
    """
    config = TenantCellModelConfig(tenant_id="acme", primary_model=ORNITH, reviewer_model=QWEN_FINE_TUNE)
    result = validate_model_diversity(config)
    assert not result.satisfied

    readiness = mark_tenant_cell_ready(config)
    assert not readiness.ready


def test_accepts_second_architecturally_distinct_self_hosted_model():
    config = TenantCellModelConfig(
        tenant_id="acme", primary_model=ORNITH, reviewer_model=DISTINCT_SECOND_MODEL
    )
    result = validate_model_diversity(config)
    assert result.satisfied
    assert result.route == "second_self_hosted_model"

    readiness = mark_tenant_cell_ready(config)
    assert readiness.ready


def test_accepts_frontier_api_escalation_path_with_no_second_model():
    config = TenantCellModelConfig(
        tenant_id="acme",
        primary_model=ORNITH,
        frontier_escalation=FrontierEscalationConfig(provider="anthropic", endpoint="https://api.anthropic.com"),
    )
    result = validate_model_diversity(config)
    assert result.satisfied
    assert result.route == "frontier_escalation"

    readiness = mark_tenant_cell_ready(config)
    assert readiness.ready


def test_disabled_frontier_escalation_does_not_satisfy_the_requirement():
    config = TenantCellModelConfig(
        tenant_id="acme",
        primary_model=ORNITH,
        frontier_escalation=FrontierEscalationConfig(
            provider="anthropic", endpoint="https://api.anthropic.com", enabled=False
        ),
    )
    result = validate_model_diversity(config)
    assert not result.satisfied

    readiness = mark_tenant_cell_ready(config)
    assert not readiness.ready


def test_both_second_model_and_frontier_escalation_configured():
    config = TenantCellModelConfig(
        tenant_id="acme",
        primary_model=ORNITH,
        reviewer_model=DISTINCT_SECOND_MODEL,
        frontier_escalation=FrontierEscalationConfig(provider="anthropic", endpoint="https://api.anthropic.com"),
    )
    result = validate_model_diversity(config)
    assert result.satisfied
    assert result.route == "both"
