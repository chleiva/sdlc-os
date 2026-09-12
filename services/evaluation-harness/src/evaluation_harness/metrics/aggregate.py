"""`ProductionMetricsReport`: one call site that assembles every Section
20.2 live production metric from real `run_registry` data (plus the
pluggable cost/defect/gate-event sources this package defines for the
data F2's schema does not itself carry).

This is a thin composition over `metrics.success`, `.rework`,
`.cycle_time`, `.cost`, `.defect_escape`, `.overrides`, and
`.human_time` -- it does not recompute anything itself, and it never
blends the long-horizon/short-task split or the net-human-time baseline
into a single figure (see each field's source module for why).
"""

from __future__ import annotations

from dataclasses import dataclass

from run_registry import RegistryService

from evaluation_harness.gate_events import GateDecisionLog
from evaluation_harness.metrics.cost import AttemptCostSource, CostReport, compute_cost_per_completed_task
from evaluation_harness.metrics.cycle_time import CycleTimeReport, compute_cycle_time
from evaluation_harness.metrics.defect_escape import (
    DefectEscapeReport,
    DefectEscapeSource,
    compute_defect_escape_rate,
)
from evaluation_harness.metrics.human_time import NetHumanTimeReport, compute_net_human_time
from evaluation_harness.metrics.overrides import OverrideRateReport, compute_override_rates
from evaluation_harness.metrics.rework import DEFAULT_WINDOW_HOURS, ReworkReport, compute_rework_rate
from evaluation_harness.metrics.success import SuccessRateReport, compute_task_success_rate


@dataclass(frozen=True)
class ProductionMetricsReport:
    tenant_id: str
    success: SuccessRateReport
    rework: ReworkReport
    cycle_time: CycleTimeReport
    cost: CostReport
    defect_escape: DefectEscapeReport
    overrides: OverrideRateReport
    net_human_time: NetHumanTimeReport


def compute_production_metrics(
    *,
    registry: RegistryService,
    tenant_id: str,
    gate_log: GateDecisionLog,
    cost_source: AttemptCostSource,
    defect_source: DefectEscapeSource,
    human_baseline_hours: float,
    human_only_baseline_hours: float,
    rework_window_hours: float = DEFAULT_WINDOW_HOURS,
    additional_human_hours_per_task: float = 0.0,
) -> ProductionMetricsReport:
    rework = compute_rework_rate(
        registry=registry, tenant_id=tenant_id, window_hours=rework_window_hours
    )
    success = compute_task_success_rate(
        registry=registry, tenant_id=tenant_id, rework_run_ids=rework.rework_run_ids
    )
    cycle_time = compute_cycle_time(
        registry=registry, tenant_id=tenant_id, human_baseline_hours=human_baseline_hours
    )
    cost = compute_cost_per_completed_task(
        registry=registry, tenant_id=tenant_id, cost_source=cost_source
    )
    defect_escape = compute_defect_escape_rate(
        registry=registry, tenant_id=tenant_id, defect_source=defect_source
    )
    overrides = compute_override_rates(gate_log=gate_log, tenant_id=tenant_id)
    net_human_time = compute_net_human_time(
        gate_log=gate_log,
        tenant_id=tenant_id,
        human_only_baseline_hours=human_only_baseline_hours,
        additional_human_hours_per_task=additional_human_hours_per_task,
    )

    return ProductionMetricsReport(
        tenant_id=tenant_id,
        success=success,
        rework=rework,
        cycle_time=cycle_time,
        cost=cost,
        defect_escape=defect_escape,
        overrides=overrides,
        net_human_time=net_human_time,
    )
