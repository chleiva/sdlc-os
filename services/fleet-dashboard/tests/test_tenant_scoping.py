"""Acceptance criterion: "A viewer authorized for Tenant A cannot see
Tenant B's cards under any filter combination."

These tests go through `DashboardService.poll()` -- the same path the
HTTP layer uses -- and separately through the raw scope-resolution
function, so both the "what data comes back" and "what scope gets
resolved in the first place" halves of tenant isolation are covered.
`test_http_api.py` repeats the sharpest of these at the actual HTTP
layer, including a malicious client-supplied tenant_id.
"""

from __future__ import annotations

import itertools

import pytest

from fleet_dashboard.dashboard_service import Filters, TenantScopeError, resolve_scope
from tests.conftest import advance, make_run


def _all_cards_belong_to(cards, tenant_id):
    return all(c["tenant_id"] == tenant_id for c in cards)


@pytest.fixture
def seeded(registry, tenant_a, tenant_b):
    """Distinct, identifiable data for each tenant, deliberately
    overlapping on every filterable field (same repo/team/cloud) so a
    filter can never be the thing that "accidentally" hides tenant B's
    data -- only correct tenant scoping can.
    """
    run_a = make_run(
        registry, tenant_a, jira_key="SHARED-1", repo="shared/repo", branch="agent/a",
    )
    registry.write_execution_location(
        tenant_id=tenant_a, run_id=run_a.id, expected_version=run_a.version,
        cloud_provider="aws", region_az="us-east-1", node_id="node-a",
    )
    run_b = make_run(
        registry, tenant_b, jira_key="SHARED-1", repo="shared/repo", branch="agent/b",
    )
    registry.write_execution_location(
        tenant_id=tenant_b, run_id=run_b.id, expected_version=run_b.version,
        cloud_provider="aws", region_az="us-east-1", node_id="node-b",
    )
    return run_a, run_b


def test_viewer_a_never_sees_tenant_b_cards_under_any_filter_combo(dashboard, seeded, tenant_a, tenant_b):
    filter_axes = {
        "repository": [None, "shared/repo", "nonexistent/repo"],
        "team": [None, "SHARED"],
        "cloud": [None, "aws", "gcp"],
        "column": [None, "Analysis"],
    }
    keys = list(filter_axes)
    for combo in itertools.product(*(filter_axes[k] for k in keys)):
        filters = Filters(**dict(zip(keys, combo)))
        result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=filters)
        assert _all_cards_belong_to(result["cards"], tenant_a)
        assert not any(c["tenant_id"] == tenant_b for c in result["cards"])


def test_viewer_b_never_sees_tenant_a_cards_under_any_filter_combo(dashboard, seeded, tenant_a, tenant_b):
    filter_axes = {
        "repository": [None, "shared/repo"],
        "cloud": [None, "aws"],
    }
    keys = list(filter_axes)
    for combo in itertools.product(*(filter_axes[k] for k in keys)):
        filters = Filters(**dict(zip(keys, combo)))
        result = dashboard.poll(tenant_scope=frozenset({tenant_b}), filters=filters)
        assert _all_cards_belong_to(result["cards"], tenant_b)


def test_resolve_scope_never_trusts_a_client_supplied_tenant_id_outside_the_authorized_set(tenant_a, tenant_b):
    # Viewer authorized only for tenant_a explicitly asks for tenant_b:
    # fails closed to an *empty* scope, not an error, and never resolves
    # to tenant_b.
    scope = resolve_scope(
        authorized_tenant_ids=frozenset({tenant_a}),
        requested_tenant_id=tenant_b,
        scope_all=False,
    )
    assert scope == frozenset()


def test_resolve_scope_refuses_to_guess_for_a_multi_tenant_identity_with_no_explicit_choice(tenant_a, tenant_b):
    with pytest.raises(TenantScopeError):
        resolve_scope(
            authorized_tenant_ids=frozenset({tenant_a, tenant_b}),
            requested_tenant_id=None,
            scope_all=False,
        )


def test_resolve_scope_scope_all_only_returns_the_authorized_set_never_more(tenant_a, tenant_b):
    scope = resolve_scope(
        authorized_tenant_ids=frozenset({tenant_a, tenant_b}),
        requested_tenant_id=None,
        scope_all=True,
    )
    assert scope == frozenset({tenant_a, tenant_b})


def test_unauthorized_identity_sees_nothing_even_with_a_valid_looking_tenant_id(dashboard, seeded, tenant_a):
    scope = resolve_scope(
        authorized_tenant_ids=frozenset(),  # unknown/unauthenticated identity
        requested_tenant_id=tenant_a,
        scope_all=False,
    )
    assert scope == frozenset()
    result = dashboard.poll(tenant_scope=scope, filters=Filters())
    assert result["cards"] == []


def test_empty_result_for_tenant_with_no_runs_is_not_mistaken_for_an_error(dashboard, tenant_a):
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    assert result["cards"] == []
    assert result["tenant_ids_viewed"] == [tenant_a]
