"""Builds `InternalHistoricalTicketsSuite`'s task set from real
`run_registry` data.

Per the brief: "An internal historical tickets suite seeded from real
synthetic run_registry data you create in tests." This module reads that
data back out through F2's public `RegistryService` API only
(`list_runs` / `list_attempts`) -- never a direct store connection -- and
turns each Run into one `BenchmarkTask`, tagging its difficulty from the
Run's real Attempt history via `task_classification.classify_horizon`.
"""

from __future__ import annotations

from run_registry import RegistryService, stages
from run_registry.models import Run

from evaluation_harness.benchmarks.base import BenchmarkTask
from evaluation_harness.task_classification import classify_horizon


def build_tasks_from_registry(
    service: RegistryService, *, tenant_id: str, limit: int = 1000
) -> list[BenchmarkTask]:
    """Read every Run for `tenant_id` back out of the real Registry
    Service and convert it into an `internal-historical` `BenchmarkTask`.

    Returns an empty list (not an error) when the tenant has no runs yet
    -- mirrors `RegistryService`'s own empty-result-is-not-an-error
    convention.
    """
    result = service.list_runs(tenant_id=tenant_id, limit=limit)
    runs: list[Run] = result.data if result.is_ok else []

    tasks: list[BenchmarkTask] = []
    for run in runs:
        attempts_result = service.list_attempts(tenant_id=tenant_id, run_id=run.id)
        attempts = attempts_result.data if attempts_result.is_ok else []
        difficulty = classify_horizon(attempts)

        if run.stage == stages.COMPLETED:
            original_outcome = "completed"
        elif run.stage == stages.ABANDONED:
            original_outcome = "abandoned"
        else:
            original_outcome = "in_progress"

        tasks.append(
            BenchmarkTask(
                task_id=run.id,
                family="internal-historical",
                prompt=f"Re-resolve historical ticket {run.jira_key} in {run.repo}",
                difficulty=difficulty,
                metadata={
                    "jira_key": run.jira_key,
                    "repo": run.repo,
                    "original_outcome": original_outcome,
                    "attempt_count": len(attempts) if attempts else 1,
                },
            )
        )
    return tasks
