"""Tenant compute cell readiness (spec Section 14.13).

Ties the pieces in this package together into the one check the brief's
acceptance criteria actually asks for: "a cell can't be provisioned with
only one self-hosted model and no configured reviewer path." This is the
gate a job dispatcher (D1) or an onboarding workflow would call before
routing any tenant traffic onto a freshly-provisioned cell.
"""

from __future__ import annotations

from dataclasses import dataclass

from tenant_cell.model_diversity import DiversityCheckResult, TenantCellModelConfig, validate_model_diversity


@dataclass(frozen=True)
class TenantCellReadiness:
    tenant_id: str
    ready: bool
    diversity_check: DiversityCheckResult
    reason: str


def mark_tenant_cell_ready(config: TenantCellModelConfig) -> TenantCellReadiness:
    """Refuses to mark a tenant cell ready unless Section 13.4's
    model-diversity requirement is satisfied. Never mutates any external
    state -- a caller (job dispatcher, onboarding workflow, the Fleet
    Control Dashboard) decides what to do with a `ready=False` result
    (e.g. block traffic routing, surface it as a blocked onboarding step).
    """
    diversity = validate_model_diversity(config)
    if not diversity.satisfied:
        return TenantCellReadiness(
            tenant_id=config.tenant_id,
            ready=False,
            diversity_check=diversity,
            reason=f"model-diversity requirement not satisfied: {diversity.reason}",
        )
    return TenantCellReadiness(
        tenant_id=config.tenant_id,
        ready=True,
        diversity_check=diversity,
        reason=f"model-diversity requirement satisfied via {diversity.route}: {diversity.reason}",
    )
