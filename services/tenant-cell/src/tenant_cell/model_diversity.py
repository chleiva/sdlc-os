"""Model-diversity enforcement (spec Section 13.4, Section 13.7).

A tenant compute cell can never be provisioned with only one self-hosted
model serving both the implementer and reviewer/adversarial-verification
roles. Section 13.4 (Rev 4) is explicit that this is a stronger
requirement than "just add a second checkpoint": a second model must be
an architecturally distinct lineage (a genuinely different base-model
family), not merely a different fine-tune/checkpoint of the same family
-- "a second Qwen3.5/3.6-family checkpoint does **not** satisfy this
requirement" is the spec's own worked example, because shared
architecture and shared pretraining lineage are exactly the source of
shared review blind spots the rule exists to avoid.

The minimum posture (Section 13.4) is one of:
  * a second, architecturally distinct self-hosted model, or
  * a configured escalation path to a frontier API for the review pass, or
  * both.

This module's `validate_model_diversity` is the enforcement point:
`mark_tenant_cell_ready` in `tenant_cell.py` refuses to mark a cell ready
unless this passes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    base_architecture_lineage: str
    weight_checksum: str


@dataclass(frozen=True)
class FrontierEscalationConfig:
    provider: str
    endpoint: str
    enabled: bool = True


@dataclass(frozen=True)
class TenantCellModelConfig:
    tenant_id: str
    primary_model: ModelSpec
    reviewer_model: ModelSpec | None = None
    frontier_escalation: FrontierEscalationConfig | None = None


@dataclass(frozen=True)
class DiversityCheckResult:
    satisfied: bool
    reason: str
    route: str | None  # "second_self_hosted_model" | "frontier_escalation" | "both" | None


def validate_model_diversity(config: TenantCellModelConfig) -> DiversityCheckResult:
    has_distinct_second_model = (
        config.reviewer_model is not None
        and config.reviewer_model.base_architecture_lineage
        != config.primary_model.base_architecture_lineage
    )
    has_frontier_escalation = (
        config.frontier_escalation is not None and config.frontier_escalation.enabled
    )

    if config.reviewer_model is not None and not has_distinct_second_model and not has_frontier_escalation:
        return DiversityCheckResult(
            satisfied=False,
            reason=(
                f"reviewer_model '{config.reviewer_model.name}' shares the primary model's "
                f"'{config.primary_model.base_architecture_lineage}' lineage -- a same-lineage "
                "checkpoint/fine-tune does not satisfy Section 13.4's diversity requirement, "
                "and no frontier-API escalation path is configured either"
            ),
            route=None,
        )

    if has_distinct_second_model and has_frontier_escalation:
        return DiversityCheckResult(
            satisfied=True, reason="second architecturally-distinct model and frontier escalation both configured", route="both"
        )
    if has_distinct_second_model:
        return DiversityCheckResult(
            satisfied=True,
            reason=(
                f"second architecturally-distinct model configured "
                f"('{config.primary_model.base_architecture_lineage}' vs "
                f"'{config.reviewer_model.base_architecture_lineage}')"  # type: ignore[union-attr]
            ),
            route="second_self_hosted_model",
        )
    if has_frontier_escalation:
        return DiversityCheckResult(
            satisfied=True,
            reason=f"frontier-API reviewer escalation configured ('{config.frontier_escalation.provider}')",  # type: ignore[union-attr]
            route="frontier_escalation",
        )

    return DiversityCheckResult(
        satisfied=False,
        reason=(
            "only one self-hosted model is configured and no frontier-API reviewer escalation "
            "path is configured -- Section 13.4 requires one of: a second architecturally-"
            "distinct self-hosted model, a frontier-API escalation path, or both"
        ),
        route=None,
    )
