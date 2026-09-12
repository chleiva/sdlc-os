"""Gate approver resolution against D4's real JiraClient (talking to its
own local mock Jira server, never a live org -- see conftest.py)."""
from __future__ import annotations

import pytest

from gates.approver import ApproverResolutionError, resolve_default_approver

from ._helpers import seed_issue


def test_resolves_assignee_when_present(jira_client, jira_mock):
    _, store = jira_mock
    seed_issue(
        store, key="PROJ-1",
        assignee={"accountId": "acc-dev-1", "displayName": "Dev One"},
        reporter={"accountId": "acc-reporter-1", "displayName": "Reporter One"},
    )
    approver = resolve_default_approver(jira_client, issue_key="PROJ-1")
    assert approver.account_id == "acc-dev-1"
    assert approver.display_name == "Dev One"
    assert approver.source == "assignee"


def test_falls_back_to_reporter_when_unassigned(jira_client, jira_mock):
    _, store = jira_mock
    seed_issue(
        store, key="PROJ-2",
        assignee=None,
        reporter={"accountId": "acc-reporter-2", "displayName": "Reporter Two"},
    )
    approver = resolve_default_approver(jira_client, issue_key="PROJ-2")
    assert approver.account_id == "acc-reporter-2"
    assert approver.source == "reporter"


def test_raises_when_neither_assignee_nor_reporter(jira_client, jira_mock):
    _, store = jira_mock
    seed_issue(store, key="PROJ-3", assignee=None, reporter=None)
    with pytest.raises(ApproverResolutionError):
        resolve_default_approver(jira_client, issue_key="PROJ-3")
