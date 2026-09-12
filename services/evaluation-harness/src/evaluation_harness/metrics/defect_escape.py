"""Defect escape rate (Section 20.2 bullet 6): issues found in production
that verification (Section 11) should have caught.

`run_registry` has no incident/production-defect tracker -- that data
lives with whatever component watches production (an incident
management system, not yet built anywhere in this repo). `DefectEscapeSource`
is the pluggable seam; `SyntheticDefectEscapeSource` is this environment's
deterministic stand-in, documented as exactly where a real incident-feed
integration plugs in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from run_registry import RegistryService, stages


@runtime_checkable
class DefectEscapeSource(Protocol):
    def escaped_defect_run_ids(self, tenant_id: str) -> frozenset[str]: ...


class SyntheticDefectEscapeSource:
    """Deterministic stand-in: a fixed, caller-supplied set of run ids
    known (in the fixture) to have produced a production defect that
    verification should have caught."""

    def __init__(self, escaped_run_ids: frozenset[str] | set[str] = frozenset()):
        self._ids = frozenset(escaped_run_ids)

    def escaped_defect_run_ids(self, tenant_id: str) -> frozenset[str]:
        return self._ids


@dataclass(frozen=True)
class DefectEscapeReport:
    tenant_id: str
    completed_count: int
    escaped_count: int
    defect_escape_rate: float


def compute_defect_escape_rate(
    *, registry: RegistryService, tenant_id: str, defect_source: DefectEscapeSource
) -> DefectEscapeReport:
    result = registry.list_runs(tenant_id=tenant_id, limit=10_000)
    runs = result.data if result.is_ok else []
    completed = [r for r in runs if r.stage == stages.COMPLETED]

    escaped_ids = defect_source.escaped_defect_run_ids(tenant_id)
    escaped_count = sum(1 for r in completed if r.id in escaped_ids)
    completed_count = len(completed)

    return DefectEscapeReport(
        tenant_id=tenant_id,
        completed_count=completed_count,
        escaped_count=escaped_count,
        defect_escape_rate=(escaped_count / completed_count) if completed_count else 0.0,
    )
