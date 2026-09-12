"""DashboardService: the one place this deliverable talks to the
Registry Service.

Everything downstream (the HTTP layer, the tests) goes through
`DashboardService.poll()`. It:

  * never opens the Registry's SQLite file -- it holds a
    `run_registry.RegistryService` instance and calls only its public
    methods (`list_runs`, by tenant_id);
  * enforces tenant scoping itself at the *call* boundary (which
    tenant_id(s) it is even willing to ask the Registry Service about),
    on top of the Registry Service's own fail-closed enforcement --
    defense in depth, not a replacement for it;
  * is the single place "a poll" happens, so both the HTTP handler and
    the tests exercise the exact same, real (not mocked) query path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from run_registry import RegistryService
from run_registry.models import Run
from run_registry.result import Outcome

from fleet_dashboard import cards, columns, config
from fleet_dashboard.poll_state import PollHistory


class TenantScopeError(Exception):
    """Raised when a request does not name a resolvable, authorized
    tenant scope -- see `resolve_scope()`. Never raised for "authorized
    but empty," only for "the caller didn't tell us which tenant(s),
    unambiguously, that they're allowed to see."
    """


@dataclass(frozen=True)
class Filters:
    repository: str | None = None
    team: str | None = None
    cloud: str | None = None
    column: str | None = None
    # Accepted for API completeness (brief: "filterable by ... autonomy
    # level") but currently a documented no-op: the Registry schema has
    # no autonomy-level field to filter on. See dashboard_service's
    # `UNSUPPORTED_FILTER_NOTE`.
    autonomy_level: str | None = None


UNSUPPORTED_FILTER_NOTE = (
    "autonomy_level is accepted but not applied: the Run Registry schema "
    "(run_registry.models.Run) has no autonomy-level field to filter on. "
    "Flagged for human review -- see README.md 'Known gaps'."
)


def resolve_scope(
    *, authorized_tenant_ids: frozenset[str], requested_tenant_id: str | None, scope_all: bool
) -> frozenset[str]:
    """Which tenant_id(s) a request may query, given what its identity is
    authorized for and what it explicitly asked for.

    Fails closed and never defaults to "everything I'm authorized for"
    silently: Sec 16.5 requires the cross-tenant view be an explicit,
    audited grant, "not a default." So:

      * an explicit `requested_tenant_id` narrows to that one tenant --
        IF it is in `authorized_tenant_ids` (never trusted on its own);
        a tenant_id outside the authorized set resolves to an empty
        scope (fails closed), never an error that would confirm or deny
        that tenant's existence.
      * `scope_all=True` (an explicit opt-in) returns every authorized
        tenant -- only meaningful, and only reachable in practice, for an
        identity already granted more than one tenant (an operator).
      * neither given: if the identity is authorized for exactly one
        tenant, that's unambiguous and we use it. If authorized for more
        than one and neither `requested_tenant_id` nor `scope_all` was
        given, we refuse to guess -- `TenantScopeError`.
    """
    if requested_tenant_id is not None:
        if requested_tenant_id in authorized_tenant_ids:
            return frozenset({requested_tenant_id})
        return frozenset()  # fail closed: never confirms/denies existence

    if scope_all:
        return authorized_tenant_ids

    if len(authorized_tenant_ids) == 1:
        return authorized_tenant_ids

    if len(authorized_tenant_ids) == 0:
        return frozenset()

    raise TenantScopeError(
        "identity is authorized for multiple tenants; pass an explicit "
        "tenant_id or scope=all_tenants (operator-only in practice) -- "
        "no implicit cross-tenant default"
    )


class DashboardService:
    def __init__(self, registry: RegistryService, *, poll_history: PollHistory | None = None):
        self._registry = registry
        self._poll_history = poll_history if poll_history is not None else PollHistory()

    def _fetch_all_runs(self, tenant_id: str) -> list[Run]:
        """Fully drain `list_runs` for one tenant_id via its own
        pagination -- the only Registry Service call this module makes.
        """
        runs: list[Run] = []
        offset = 0
        while True:
            result = self._registry.list_runs(
                tenant_id=tenant_id, limit=config.LIST_RUNS_PAGE_SIZE, offset=offset
            )
            if result.outcome is Outcome.EMPTY:
                break
            if result.outcome is Outcome.ERROR:
                # Registry Service errors are surfaced, never swallowed
                # into a false "no runs" empty board.
                raise RuntimeError(f"registry list_runs error: {result.error}")
            page = result.data or []
            runs.extend(page)
            if len(page) < config.LIST_RUNS_PAGE_SIZE:
                break
            offset += config.LIST_RUNS_PAGE_SIZE
        return runs

    def poll(
        self,
        *,
        tenant_scope: frozenset[str],
        filters: Filters = Filters(),
    ) -> dict[str, Any]:
        """Run exactly one real poll of the Registry Service, scoped to
        `tenant_scope`, and return the served view.

        `tenant_scope` must already be the *resolved, authorized* set
        (see `resolve_scope`) -- this method does not re-check
        authorization; it only ever asks the Registry Service about the
        tenant_id(s) it is handed, which is why callers must resolve
        scope first rather than pass through a raw client-supplied
        tenant_id.
        """
        polled_at = datetime.now(timezone.utc)
        all_cards: list[dict[str, Any]] = []

        for tenant_id in sorted(tenant_scope):
            runs = self._fetch_all_runs(tenant_id)
            active_run_ids = {r.id for r in runs}
            self._poll_history.prune(tenant_id=tenant_id, active_run_ids=active_run_ids)

            for run in runs:
                if not columns.is_shown_on_board(run.stage):
                    continue  # terminal stage: cleared from the board
                observation = self._poll_history.observe(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    stage=run.stage,
                    checkpoint_pointer=run.checkpoint_pointer,
                    now=polled_at.timestamp(),
                )
                card = cards.build_card(run, observation=observation, polled_at=polled_at)
                all_cards.append(card)

        all_cards = _apply_filters(all_cards, filters)

        return {
            "polled_at": polled_at.isoformat(),
            "poll_interval_seconds": config.POLL_INTERVAL_SECONDS,
            "tenant_ids_viewed": sorted(tenant_scope),
            "columns": list(columns.COLUMNS),
            "cards": all_cards,
            "notes": [UNSUPPORTED_FILTER_NOTE] if filters.autonomy_level is not None else [],
        }


def _apply_filters(all_cards: list[dict[str, Any]], filters: Filters) -> list[dict[str, Any]]:
    def keep(card: dict[str, Any]) -> bool:
        if filters.repository is not None and card["repository"] != filters.repository:
            return False
        if filters.team is not None and card["team"] != filters.team:
            return False
        if filters.cloud is not None and card["cloud"] != filters.cloud:
            return False
        if filters.column is not None and card["column"] != filters.column:
            return False
        # filters.autonomy_level: deliberately not applied -- see
        # UNSUPPORTED_FILTER_NOTE.
        return True

    return [c for c in all_cards if keep(c)]
