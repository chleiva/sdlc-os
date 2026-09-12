"""Per-card field checks: repository + Jira issue, cloud/region/node,
trace link, and (Development column only) checkpoint count + capacity
class, per the brief's column-mapping table.
"""

from __future__ import annotations

from fleet_dashboard.dashboard_service import Filters
from tests.conftest import advance, make_run


def _card_for(result, run_id):
    matches = [c for c in result["cards"] if c["run_id"] == run_id]
    assert len(matches) == 1
    return matches[0]


def test_card_carries_repo_jira_issue_location_and_trace_link(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a, jira_key="PROJ-42", repo="org/svc", capacity_class="on_demand")
    registry.write_execution_location(
        tenant_id=tenant_a, run_id=run.id, expected_version=run.version,
        cloud_provider="aws", region_az="us-west-2", node_id="i-abc123",
    )
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)

    assert card["repository"] == "org/svc"
    assert card["jira_issue"] == "PROJ-42"
    assert card["team"] == "PROJ"
    assert card["cloud"] == "aws"
    assert card["region_az"] == "us-west-2"
    assert card["node_id"] == "i-abc123"
    assert card["trace_id"] == run.trace_id
    assert card["trace_id"] in card["trace_url"]


def test_development_card_shows_checkpoint_count_and_capacity_class(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a, capacity_class="spot")
    for stage in ("research", "plan_authoring", "plan_approval_gate", "implementation"):
        run = advance(registry, run, stage)

    dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())  # observe 0 checkpoints

    run = registry.write_checkpoint(
        tenant_id=tenant_a, run_id=run.id, expected_version=run.version,
        checkpoint_pointer="s3://ckpt/1",
    ).data
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)
    assert card["column"] == "Development"
    assert card["capacity_class"] == "spot"
    assert card["checkpoint_count_observed"] == 1

    run = registry.write_checkpoint(
        tenant_id=tenant_a, run_id=run.id, expected_version=run.version,
        checkpoint_pointer="s3://ckpt/2",
    ).data
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)
    assert card["checkpoint_count_observed"] == 2


def test_non_development_cards_do_not_carry_checkpoint_fields(dashboard, registry, tenant_a):
    run = make_run(registry, tenant_a)
    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters())
    card = _card_for(result, run.id)
    assert card["column"] == "Analysis"
    assert "checkpoint_count_observed" not in card


def test_repository_and_cloud_filters_narrow_results(dashboard, registry, tenant_a):
    r1 = make_run(registry, tenant_a, repo="org/one")
    registry.write_execution_location(
        tenant_id=tenant_a, run_id=r1.id, expected_version=r1.version, cloud_provider="aws",
    )
    r2 = make_run(registry, tenant_a, repo="org/two")
    registry.write_execution_location(
        tenant_id=tenant_a, run_id=r2.id, expected_version=r2.version, cloud_provider="gcp",
    )

    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters(repository="org/one"))
    assert {c["run_id"] for c in result["cards"]} == {r1.id}

    result = dashboard.poll(tenant_scope=frozenset({tenant_a}), filters=Filters(cloud="gcp"))
    assert {c["run_id"] for c in result["cards"]} == {r2.id}


def test_autonomy_level_filter_is_accepted_but_documented_as_unsupported(dashboard, registry, tenant_a):
    make_run(registry, tenant_a)
    result = dashboard.poll(
        tenant_scope=frozenset({tenant_a}), filters=Filters(autonomy_level="supervised")
    )
    assert len(result["cards"]) == 1  # not filtered out -- the param is a documented no-op
    assert any("autonomy_level" in note for note in result["notes"])
