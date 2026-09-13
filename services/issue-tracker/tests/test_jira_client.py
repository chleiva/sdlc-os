"""Exercises `JiraClient` (real Jira Cloud REST API v3 request/response
handling) against the local mock Jira server. Every assertion here is
about D4's own request-building/response-parsing code -- the mock only
stands in for Atlassian's data center, per SETUP.md.
"""


def test_create_epic_success_returns_ok_result(jira_client):
    result = jira_client.create_epic(
        project_key="PROJ", summary="Add multi-tenant billing",
        description="Overall goal: tenant-scoped billing.",
        acceptance_criteria=["Tenants are billed independently", "No cross-tenant leakage"],
        size="L",
    )
    assert result["outcome"] == "ok"
    assert result["data"]["issue_key"].startswith("PROJ-")
    assert result["data"]["url"].endswith(result["data"]["issue_key"])


def test_create_epic_is_idempotent_on_duplicate_summary(jira_client):
    first = jira_client.create_epic(
        project_key="PROJ", summary="Duplicate epic test",
        description="d", acceptance_criteria=["ac1"],
    )
    assert first["outcome"] == "ok"

    second = jira_client.create_epic(
        project_key="PROJ", summary="Duplicate epic test",
        description="d", acceptance_criteria=["ac1"],
    )
    assert second["outcome"] == "empty"
    assert second["existing_issue_key"] == first["data"]["issue_key"]


def test_create_story_under_epic_sets_parent_and_size_label(jira_client):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Parent epic", description="d", acceptance_criteria=["ac"],
    )
    epic_key = epic["data"]["issue_key"]

    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic_key, summary="Do the thing",
        description="Implement the thing.", size="M",
    )
    assert story["outcome"] == "ok"
    assert story["data"]["epic_key"] == epic_key

    fetched = jira_client.get_issue(issue_key=story["data"]["issue_key"])
    assert fetched["outcome"] == "ok"
    assert fetched["data"]["size"] == "M"
    assert "size:M" in fetched["data"]["labels"]


def test_dependency_sequencing_round_trips_as_native_issue_links(jira_client):
    """Acceptance criterion: "Epic->Story dependency sequencing
    round-trips as native Jira issue links, inspectable in Jira's own
    UI." We can't click Jira's UI here, but we prove the mock's issue
    store -- which speaks the real issueLink request/response shape --
    holds a real bidirectional `issuelinks` entry, which is exactly
    what a human would see rendered in Jira's own issue view.
    """
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Epic with deps", description="d", acceptance_criteria=["ac"],
    )
    epic_key = epic["data"]["issue_key"]

    dep = jira_client.create_story(
        project_key="PROJ", epic_key=epic_key, summary="Dependency story",
        description="d", size="S",
    )
    dep_key = dep["data"]["issue_key"]

    dependent = jira_client.create_story(
        project_key="PROJ", epic_key=epic_key, summary="Dependent story",
        description="d", size="S", depends_on=[dep_key],
    )
    dependent_key = dependent["data"]["issue_key"]

    dependent_fetched = jira_client.get_issue(issue_key=dependent_key)
    assert dependent_fetched["outcome"] == "ok"
    assert {"type": "is-blocked-by", "issue_key": dep_key} in dependent_fetched["data"]["links"]

    dep_fetched = jira_client.get_issue(issue_key=dep_key)
    assert dep_fetched["outcome"] == "ok"
    assert {"type": "blocks", "issue_key": dependent_key} in dep_fetched["data"]["links"]


def test_get_issue_not_found(jira_client):
    result = jira_client.get_issue(issue_key="PROJ-99999")
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "not-found"
    assert result["error"]["retryable"] is False


def test_get_issue_archived_is_empty_not_error(jira_client, jira_mock):
    _base_url, store = jira_mock
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Archived epic", description="d", acceptance_criteria=["ac"],
    )
    key = epic["data"]["issue_key"]
    store.get(key).archived = True

    result = jira_client.get_issue(issue_key=key)
    assert result["outcome"] == "empty"
    assert "archived" in result["reason"]
    assert "error" not in result


def test_transition_to_configured_approval_status_succeeds(jira_client):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Transition test epic", description="d", acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Transition test story",
        description="d", size="S",
    )
    key = story["data"]["issue_key"]

    result = jira_client.transition_status(issue_key=key, target_status="In Progress")
    assert result["outcome"] == "ok"
    assert result["data"]["previous_status"] == "Selected for Development"
    assert result["data"]["new_status"] == "In Progress"
    assert "transitioned_at" in result["data"]


def test_transition_already_in_target_status_is_empty(jira_client):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Already-there epic", description="d", acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Already-there story",
        description="d", size="S",
    )
    key = story["data"]["issue_key"]
    jira_client.transition_status(issue_key=key, target_status="In Progress")

    result = jira_client.transition_status(issue_key=key, target_status="In Progress")
    assert result["outcome"] == "empty"
    assert "already in status" in result["reason"]


def test_post_comment_success(jira_client):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Comment test epic", description="d", acceptance_criteria=["ac"],
    )
    key = epic["data"]["issue_key"]

    result = jira_client.post_comment(issue_key=key, body="Here is the plan.", comment_type="plan")
    assert result["outcome"] == "ok"
    assert result["data"]["issue_key"] == key
    assert "comment_id" in result["data"]


def test_post_comment_with_blank_body_is_empty_result(jira_client):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Blank comment epic", description="d", acceptance_criteria=["ac"],
    )
    key = epic["data"]["issue_key"]

    result = jira_client.post_comment(issue_key=key, body="   \n  \n", comment_type="plan")
    assert result["outcome"] == "empty"
    assert "empty" in result["reason"]


def test_find_stories_in_status_returns_matching_candidates(jira_client):
    """The polling-based trigger path's own query (New): every issue
    created here starts in the mock's default status ("Selected for
    Development", matching the fixture's `TenantJiraConfig.trigger_status`)
    -- a real search for that status must find it, and must NOT find an
    issue explicitly transitioned away from it."""
    findable = jira_client.create_epic(
        project_key="PROJ", summary="Findable via status search", description="d", acceptance_criteria=["ac"],
    )
    findable_key = findable["data"]["issue_key"]

    moved = jira_client.create_epic(
        project_key="PROJ", summary="Not findable -- already moved on", description="d", acceptance_criteria=["ac"],
    )
    moved_key = moved["data"]["issue_key"]
    jira_client.transition_status(issue_key=moved_key, target_status="In Progress")

    result = jira_client.find_stories_in_status(project_key="PROJ", status="Selected for Development")
    assert result["outcome"] == "ok"
    assert findable_key in result["data"]["issue_keys"]
    assert moved_key not in result["data"]["issue_keys"]


def test_rate_limited_response_is_named_error_with_retry_after(jira_client, jira_mock):
    """Forces the mock to answer 429 (as a real Jira Cloud rate-limit
    response would) via the mock's test-only `X-Force-Status` hook."""
    _base_url, store = jira_mock
    store.force_retry_after = 7
    jira_client.session.headers.update({"X-Force-Status": "429"})
    try:
        result = jira_client.get_issue(issue_key="PROJ-1")
    finally:
        del jira_client.session.headers["X-Force-Status"]
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "rate-limited"
    assert result["error"]["retryable"] is True
    assert result["error"]["retry_after_seconds"] == 7


def test_upstream_5xx_maps_to_upstream_unavailable(jira_client):
    jira_client.session.headers.update({"X-Force-Status": "503"})
    try:
        result = jira_client.get_issue(issue_key="PROJ-1")
    finally:
        del jira_client.session.headers["X-Force-Status"]
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "upstream-unavailable"
    assert result["error"]["retryable"] is True


def test_permission_denied_response_maps_correctly(jira_client):
    jira_client.session.headers.update({"X-Force-Status": "403"})
    try:
        result = jira_client.get_issue(issue_key="PROJ-1")
    finally:
        del jira_client.session.headers["X-Force-Status"]
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"
    assert result["error"]["retryable"] is False
