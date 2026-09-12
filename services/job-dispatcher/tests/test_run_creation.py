"""When capacity is available, D1 calls F2's real, tested
`RegistryService.create_run` -- no mocking of run_registry needed, it is
real code (per the brief)."""

from __future__ import annotations

from run_registry.result import Outcome

from conftest import PROJECT_A, SECRET_A, TENANT_A, signed_webhook


def test_capacity_available_creates_a_real_run(dispatcher, registry):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-42",
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    result = dispatcher.handle_webhook(headers=headers, body=body)

    assert result.outcome == "run_created"
    run = result.run
    assert run.tenant_id == TENANT_A
    assert run.jira_key == "PROJA-42"
    assert run.repo == "acme/widgets"
    assert run.branch == "ai-factory/proja-42"
    assert run.capacity_class == "spot"

    # And it is really there in the Registry -- read back through the
    # real RegistryService, tenant-scoped.
    read_back = registry.get_run(tenant_id=TENANT_A, run_id=run.id)
    assert read_back.outcome == Outcome.OK
    assert read_back.data.jira_key == "PROJA-42"

    # Never visible to a different tenant (Sec. 14.13 fail-closed scoping).
    cross_tenant = registry.get_run(tenant_id="tenant-b", run_id=run.id)
    assert cross_tenant.outcome == Outcome.EMPTY
