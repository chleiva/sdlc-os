"""Small shared test helpers -- not itself a test module."""
from __future__ import annotations

from mocks.jira_mock_server import MockIssue


def seed_issue(store, *, key="PROJ-1", assignee=None, reporter=None) -> MockIssue:
    extra = {}
    if assignee is not None:
        extra["assignee"] = assignee
    if reporter is not None:
        extra["reporter"] = reporter
    issue = MockIssue(
        key=key,
        project_key="PROJ",
        issue_type="Story",
        summary="A story",
        description={"type": "doc", "version": 1, "content": []},
        fields_extra=extra,
    )
    store.seed(issue)
    return issue
