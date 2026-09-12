"""Acceptance criterion: "A capacity-unavailable trigger produces a
visible Jira comment, not silence" (Sec. 14.11), for both named
unavailable outcomes: spot-exhausted-and-on-demand-failed, and a
max-concurrent-job limit hit. Posted through D4's real `JiraClient`
against its own mock Jira server -- nothing about the comment posting
itself is mocked."""

from __future__ import annotations

from job_dispatcher.capacity import CapacityOutcome, ScenarioStep

from conftest import PROJECT_A, SECRET_A, TENANT_A, seed_issue, signed_webhook


def test_spot_exhausted_and_on_demand_failed_queues_and_posts_comment(
    dispatcher, capacity_provider, jira_mock
):
    _base_url, store = jira_mock
    issue_key = seed_issue(store, project_key=PROJECT_A, key_hint="PROJA-100")
    capacity_provider.set_scenario(
        TENANT_A,
        [ScenarioStep(outcome=CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED, detail="spot pool empty in all AZs")],
    )

    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": issue_key,
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    result = dispatcher.handle_webhook(headers=headers, body=body)

    assert result.outcome == "queued"
    assert result.capacity_outcome == CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED
    assert result.comment_posted is True
    assert dispatcher.queue_depth(TENANT_A) == 1

    # Not silence: a real comment landed on the real (mocked) Jira issue.
    issue = store.get(issue_key)
    assert len(issue.comments) == 1
    comment_text = str(issue.comments[0]["body"])
    assert "spot" in comment_text.lower() or "capacity" in comment_text.lower()


def test_max_concurrent_limit_hit_queues_and_posts_comment(dispatcher, capacity_provider, jira_mock):
    _base_url, store = jira_mock
    issue_key = seed_issue(store, project_key=PROJECT_A, key_hint="PROJA-101")
    capacity_provider.set_scenario(
        TENANT_A,
        [ScenarioStep(outcome=CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT, detail="5 of 5 concurrent jobs in use")],
    )

    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": issue_key,
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    result = dispatcher.handle_webhook(headers=headers, body=body)

    assert result.outcome == "queued"
    assert result.capacity_outcome == CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT
    assert result.comment_posted is True

    issue = store.get(issue_key)
    assert len(issue.comments) == 1


def test_queued_trigger_is_not_dropped_and_later_creates_a_run_when_capacity_frees_up(
    dispatcher, capacity_provider, jira_mock, registry
):
    """"queue the trigger, don't drop it" -- proven by actually
    retrying it later and getting a real Run out of it, not just
    checking a queue counter."""
    _base_url, store = jira_mock
    issue_key = seed_issue(store, project_key=PROJECT_A, key_hint="PROJA-102")
    capacity_provider.set_scenario(
        TENANT_A,
        [
            ScenarioStep(outcome=CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED),
            ScenarioStep(outcome=CapacityOutcome.AVAILABLE, capacity_class="on_demand"),
        ],
    )

    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": issue_key,
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    first = dispatcher.handle_webhook(headers=headers, body=body)
    assert first.outcome == "queued"
    assert dispatcher.queue_depth(TENANT_A) == 1

    retried = dispatcher.retry_queued(TENANT_A)
    assert retried is not None
    assert retried.outcome == "run_created"
    assert retried.run.capacity_class == "on_demand"
    assert dispatcher.queue_depth(TENANT_A) == 0

    # And a second retry with nothing queued is a documented no-op, not
    # an error.
    assert dispatcher.retry_queued(TENANT_A) is None
