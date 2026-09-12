"""Build one Kanban card (a plain, JSON-serializable dict) per Run.

Pure functions only -- no I/O here. `dashboard_service.py` is the only
caller, and it is the only place that touches `RegistryService` or
`PollHistory`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from run_registry.models import Run

from fleet_dashboard import budgets, columns
from fleet_dashboard.poll_state import RunObservation

# Trace-viewer link-out (Sec 16.4) -- we link to it, we don't reimplement
# it (brief, "Interfaces you consume"). The real base URL is a deployment
# concern; overridable via TraceLinkBuilder for tests/other environments.
_DEFAULT_TRACE_BASE_URL = "https://observability.internal.example/traces"


def trace_url(trace_id: str, *, base_url: str = _DEFAULT_TRACE_BASE_URL) -> str:
    return f"{base_url}/{trace_id}"


def team_for_jira_key(jira_key: str) -> str | None:
    """Best-effort "team" derived from the Jira project-key prefix
    (e.g. "PROJ-123" -> "PROJ"). The Run Registry schema (Sec 14.12,
    `run_registry.models.Run`) has no dedicated `team` field, so this is
    a heuristic grouping, not a stored fact -- documented in
    `DashboardService`'s filter docs and in this deliverable's report.
    """
    if not jira_key or "-" not in jira_key:
        return None
    prefix, _, rest = jira_key.partition("-")
    return prefix or None


def build_card(
    run: Run,
    *,
    observation: RunObservation,
    polled_at: datetime,
) -> dict[str, Any]:
    column = columns.column_for_stage(run.stage)
    elapsed_seconds = max(0.0, polled_at.timestamp() - observation.stage_since)

    card: dict[str, Any] = {
        "run_id": run.id,
        "tenant_id": run.tenant_id,
        "column": column,
        "stage": run.stage,
        "repository": run.repo,
        "branch": run.branch,
        "jira_issue": run.jira_key,
        "team": team_for_jira_key(run.jira_key),
        "cloud": run.execution_location.cloud_provider,
        "region_az": run.execution_location.region_az,
        "node_id": run.execution_location.node_id,
        "capacity_class": run.capacity_class,
        "trace_id": run.trace_id,
        "trace_url": trace_url(run.trace_id),
        "elapsed_in_stage_seconds": round(elapsed_seconds, 1),
        "elapsed_in_stage_is_exact": observation.stage_since_is_exact,
        "reference_budget_seconds": budgets.REFERENCE_BUDGET_SECONDS,
        "budget_fraction": round(budgets.budget_fraction(elapsed_seconds), 3),
        # Staleness disclosure (Sec 16.5 / Sec 22 "dashboard/registry
        # staleness" risk): every card carries a real last-updated
        # timestamp derived from the actual poll, never implying
        # liveness. `registry_updated_at` is the Registry row's own
        # `updated_at`; `polled_at` is when *this dashboard process*
        # last actually queried the Registry for it -- both are shown so
        # a viewer can see both "when the Registry last changed" and
        # "when we last checked," rather than one timestamp that could
        # be misread as real-time.
        "registry_updated_at": run.updated_at,
        "polled_at": polled_at.isoformat(),
        "version": run.version,
    }

    if column == columns.DEVELOPMENT:
        # Brief: "Shows checkpoint count and capacity class straight
        # from the Registry row" for Development-column cards. See
        # poll_state.py's module docstring for why "checkpoint count" is
        # an observed-since-this-process-started approximation rather
        # than a true lifetime count (the Registry stores only the
        # latest pointer, not a count).
        card["checkpoint_count_observed"] = observation.checkpoint_observed_count
        card["checkpoint_pointer"] = run.checkpoint_pointer

    if column == columns.DEVELOPMENT and observation.bounced_from_testing:
        # The exact, required behavior: "a verification failure produces
        # a visible edge back to Development on the SAME card, not a new
        # disconnected card." This is that edge -- rendered as a field
        # on the very card whose run_id never changed, not a separate
        # event/card.
        card["bounced_back_from_testing"] = True
        card["bounced_back_at"] = (
            datetime.fromtimestamp(observation.bounced_at, tz=timezone.utc).isoformat()
            if observation.bounced_at is not None
            else None
        )
    else:
        card["bounced_back_from_testing"] = False
        card["bounced_back_at"] = None

    return card
