"""Section 8.6: "An orchestrator ... never spans two tenants in a single
process". This `Orchestrator` instance is constructed for exactly one
`tenant_id`; proves it cannot be tricked into reading or resuming another
tenant's run even when given that run's real id (F2's Registry Service
itself fails closed on tenant mismatch -- Section 14.13 -- this proves
the orchestrator layered on top does not accidentally reopen that door,
e.g. by caching a run keyed only by run_id)."""
from __future__ import annotations

import pytest

from orchestrator.core import OrchestratorError
from orchestrator.model_backend import ScriptedAgentBackend

from ._factories import make_plan_output
from .conftest import make_orchestrator


def test_orchestrator_cannot_resume_a_run_belonging_to_a_different_tenant(registry, plan_store, progress_store):
    tenant_a = "tenant-a"
    tenant_b = "tenant-b"

    orch_a = make_orchestrator(
        registry=registry, tenant_id=tenant_a, plan_store=plan_store, progress_store=progress_store,
        agent_backend=ScriptedAgentBackend(plans=[make_plan_output()]),
    )
    status = orch_a.start_run(jira_key="PROJ-200", repo="acme/app", branch="feature/a", trace_id="trace-200")
    run_id = status.run_id

    orch_b = make_orchestrator(
        registry=registry, tenant_id=tenant_b, plan_store=plan_store, progress_store=progress_store,
        agent_backend=ScriptedAgentBackend(),
    )
    with pytest.raises(OrchestratorError):
        orch_b.resume_run(run_id)

    with pytest.raises(OrchestratorError):
        orch_b.approve_plan(run_id, decision="approve")
