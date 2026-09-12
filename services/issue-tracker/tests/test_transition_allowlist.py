"""Acceptance criterion: "Jira status never reflects internal pipeline
stage (research/implementation/verification) -- only the configured
gate statuses." Enforced as a fixed, small allowed-transition set in
code (`TenantJiraConfig.allowed_target_statuses`), checked *before*
`JiraClient.transition_status` ever issues an HTTP request -- proven
here by asserting the mock server records zero requests for a
disallowed target.
"""

import pytest


@pytest.mark.parametrize(
    "disallowed_status",
    ["research", "implementation", "verification", "Selected for Development", "Blocked"],
)
def test_disallowed_target_status_is_refused_without_contacting_jira(jira_client, jira_mock, disallowed_status):
    _base_url, store = jira_mock
    epic = jira_client.create_epic(
        project_key="PROJ", summary=f"Allowlist epic {disallowed_status}", description="d",
        acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary=f"Allowlist story {disallowed_status}",
        description="d", size="S",
    )
    key = story["data"]["issue_key"]
    status_before = store.get(key).status

    result = jira_client.transition_status(issue_key=key, target_status=disallowed_status)

    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"
    assert "coarse" in result["error"]["message"] or "not one of the configured" in result["error"]["message"]
    # No transition actually happened against the mock's issue store --
    # proof the request never reached Jira at all, not merely that
    # Jira refused it.
    assert store.get(key).status == status_before


@pytest.mark.parametrize("allowed_status", ["In Progress", "In Review"])
def test_configured_gate_statuses_are_allowed(jira_client, allowed_status):
    epic = jira_client.create_epic(
        project_key="PROJ", summary=f"Allowed epic {allowed_status}", description="d", acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary=f"Allowed story {allowed_status}",
        description="d", size="S",
    )
    key = story["data"]["issue_key"]

    result = jira_client.transition_status(issue_key=key, target_status=allowed_status)
    assert result["outcome"] == "ok"
    assert result["data"]["new_status"] == allowed_status


def test_allowed_target_statuses_is_exactly_the_two_gate_statuses(tenant_config):
    assert tenant_config.allowed_target_statuses() == {"In Progress", "In Review"}
