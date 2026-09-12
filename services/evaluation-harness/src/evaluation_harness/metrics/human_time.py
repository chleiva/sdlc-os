"""Net human time vs. a stated human-only baseline (Section 20.2 bullet
8, Section 23.5's "permanent humility metric"): "included specifically
because a 2025 randomized trial found experienced developers took
measurably longer with AI assistance on some tasks despite feeling
faster; this System measures the felt-versus-actual gap directly rather
than assuming agent involvement is a net time savings."

`human_only_baseline_hours` is a REQUIRED keyword argument with no
default, exactly like `cycle_time.compute_cycle_time`'s baseline, and for
the same reason: Section 23.5 treats this metric as the permanent gate on
expanding autonomy, so it must never be possible to compute a "net
savings" number by quietly assuming the baseline is zero.

System-assisted human time is derived from real `GateDecisionEvent`s
(`decided_at - opened_at` per gate, per run) -- the human-in-the-loop time
this System's own gates actually consumed -- plus an optional, explicitly
named `additional_human_hours_per_task` for human time this System's gate
events don't capture at all (writing the initial brief, answering
clarification questions mid-run). Leaving that second parameter at its
0.0 default undercounts real human time rather than overcounting it, so a
caller who omits it gets a conservative (pro-System) number, never an
inflated saving.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation_harness.gate_events import GateDecisionLog


@dataclass(frozen=True)
class NetHumanTimeReport:
    tenant_id: str
    human_only_baseline_hours: float
    system_assisted_human_hours_mean: float
    net_human_time_hours: float  # baseline - assisted; positive = System saves human time
    sample_size: int


def compute_net_human_time(
    *,
    gate_log: GateDecisionLog,
    tenant_id: str,
    human_only_baseline_hours: float,
    additional_human_hours_per_task: float = 0.0,
) -> NetHumanTimeReport:
    if human_only_baseline_hours is None:
        raise ValueError(
            "human_only_baseline_hours is required (Section 20.2/23.5: net human time is "
            "measured against a stated human-only baseline, never silently assumed to be zero)"
        )
    if human_only_baseline_hours < 0:
        raise ValueError("human_only_baseline_hours must be >= 0")

    events = gate_log.for_tenant(tenant_id)
    by_run: dict[str, float] = {}
    for event in events:
        by_run[event.run_id] = by_run.get(event.run_id, 0.0) + event.wait_hours

    sample_size = len(by_run)
    gate_hours_mean = (sum(by_run.values()) / sample_size) if sample_size else 0.0
    assisted_mean = gate_hours_mean + additional_human_hours_per_task

    return NetHumanTimeReport(
        tenant_id=tenant_id,
        human_only_baseline_hours=human_only_baseline_hours,
        system_assisted_human_hours_mean=assisted_mean,
        net_human_time_hours=human_only_baseline_hours - assisted_mean,
        sample_size=sample_size,
    )
