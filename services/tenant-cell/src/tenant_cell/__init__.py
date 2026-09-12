"""D6: Model Serving + Tenant Compute Cell Provisioning -- control-logic
service (SDLC Auto, Wave 1).

Public surface. See each module's own docstring for what it covers:

  * `naming` -- tenant-id-derived, IaC-mirroring resource naming.
  * `clock` -- injectable clock so timing-sensitive tests never sleep for real.
  * `provisioning_client` -- the mock boundary (Kubernetes/cloud API).
  * `interruption_watcher` -- the spot-interruption cordon/drain/checkpoint sequence.
  * `cold_start` -- launch-to-serving-ready budget tracking.
  * `model_diversity` -- Section 13.4 enforcement.
  * `model_version_governance` -- Section 13.5 promotion gate.
  * `tenant_cell` -- ties the above into "is this tenant cell ready".
"""

from tenant_cell.clock import Clock, FakeClock, RealClock
from tenant_cell.cold_start import ColdStartResult, track_cold_start
from tenant_cell.interruption_watcher import (
    InterruptionResult,
    InterruptionWatcher,
    Step,
    StepTiming,
)
from tenant_cell.model_diversity import (
    DiversityCheckResult,
    FrontierEscalationConfig,
    ModelSpec,
    TenantCellModelConfig,
    validate_model_diversity,
)
from tenant_cell.model_version_governance import (
    EvaluationSuiteResult,
    ModelArtifact,
    PromotionDenialReason,
    PromotionResult,
    promote_model_version,
)
from tenant_cell.naming import (
    model_serving_namespace,
    node_pool_name,
    tenant_environment,
    tenant_slug,
)
from tenant_cell.provisioning_client import (
    DrainResult,
    FakeProvisioningClient,
    NodeHandle,
    ProvisioningClient,
)
from tenant_cell.tenant_cell import TenantCellReadiness, mark_tenant_cell_ready

__all__ = [
    "Clock",
    "ColdStartResult",
    "DiversityCheckResult",
    "DrainResult",
    "EvaluationSuiteResult",
    "FakeClock",
    "FakeProvisioningClient",
    "FrontierEscalationConfig",
    "InterruptionResult",
    "InterruptionWatcher",
    "ModelArtifact",
    "ModelSpec",
    "NodeHandle",
    "PromotionDenialReason",
    "PromotionResult",
    "ProvisioningClient",
    "RealClock",
    "Step",
    "StepTiming",
    "TenantCellModelConfig",
    "TenantCellReadiness",
    "mark_tenant_cell_ready",
    "model_serving_namespace",
    "node_pool_name",
    "promote_model_version",
    "tenant_environment",
    "tenant_slug",
    "track_cold_start",
    "validate_model_diversity",
]
