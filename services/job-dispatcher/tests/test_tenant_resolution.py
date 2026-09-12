"""Sec. 4.4 tenant resolution: from the configured Jira project ->
tenant mapping, only ever reachable after auth (see
test_webhook_auth_gate.py for that ordering proof)."""

from __future__ import annotations

import pytest

from job_dispatcher.tenant_resolution import TenantResolutionError

from conftest import PROJECT_A, PROJECT_B, SECRET_A, TENANT_A, TENANT_B, signed_webhook


def test_resolves_tenant_from_configured_project_mapping(dispatcher):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-1",
        "project_key": PROJECT_A,
        "repository": "org/repo-a",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    result = dispatcher.handle_webhook(headers=headers, body=body)

    assert result.tenant_id == TENANT_A
    assert result.jira_key == "PROJA-1"


def test_unmapped_project_is_rejected(dispatcher):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "GHOST-1",
        "project_key": "GHOST",  # not in the configured directory at all
        "repository": "org/repo",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    with pytest.raises(TenantResolutionError, match="not mapped to any tenant"):
        dispatcher.handle_webhook(headers=headers, body=body)


def test_project_tenant_mismatch_is_rejected(dispatcher):
    """The signed header claims tenant-a's key was used, but the body's
    project_key resolves (via the real Sec. 4.4 mapping) to tenant-b --
    a defense-in-depth check independent of the header claim."""
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJB-1",
        "project_key": PROJECT_B,  # maps to TENANT_B, not TENANT_A
        "repository": "org/repo",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    # Signed with tenant-a's secret and tenant-a's header claim (so it
    # passes Sec. 17.3 auth cleanly) but names tenant-b's project.
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    with pytest.raises(TenantResolutionError, match="does not match"):
        dispatcher.handle_webhook(headers=headers, body=body)
