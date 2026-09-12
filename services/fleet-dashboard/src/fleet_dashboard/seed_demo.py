"""DEMO SEEDING -- NOT REAL PRODUCTION DATA.

D1/D2/D6/D7 (job dispatcher, orchestrator, model serving, verification)
don't exist yet, so there is no real traffic that would populate the Run
Registry on its own. This script seeds a handful of Run/Attempt rows
directly through `RegistryService`'s own public create-Run /
append-Attempt / transition-stage / write-checkpoint /
write-execution-location operations -- the exact same API surface any
other Registry Service caller uses -- so a human can open the dashboard
and see realistic-looking cards in every column while building/reviewing
this deliverable.

Running this against a shared/staging Registry database would pollute it
with fake runs; it is meant for a scratch SQLite file (see README.md).
"""

from __future__ import annotations

import random
import sys
import time

from run_registry import RegistryService
from run_registry.result import Outcome

_REPOS = ["acme/checkout-service", "acme/web-frontend", "acme/billing-api"]
_CLOUDS = [("aws", "us-east-1", "i-0a1b2c3"), ("gcp", "us-central1", "gce-7f3d1"), ("azure", "eastus2", "vm-99f0a")]


def _mk_run(service: RegistryService, tenant_id: str, jira_key: str, repo: str, capacity_class: str):
    result = service.create_run(
        tenant_id=tenant_id,
        jira_key=jira_key,
        repo=repo,
        branch=f"agent/{jira_key.lower()}",
        capacity_class=capacity_class,
        trace_id=f"trace-{jira_key.lower()}-{random.randint(1000, 9999)}",
    )
    assert result.outcome == Outcome.OK, result
    return result.data


def _advance(service: RegistryService, run, next_stage: str):
    result = service.transition_stage(
        tenant_id=run.tenant_id,
        run_id=run.id,
        expected_version=run.version,
        next_stage=next_stage,
    )
    assert result.outcome == Outcome.OK, result
    return result.data


def _place(service: RegistryService, run, cloud, region, node):
    result = service.write_execution_location(
        tenant_id=run.tenant_id, run_id=run.id, expected_version=run.version,
        cloud_provider=cloud, region_az=region, node_id=node,
    )
    assert result.outcome == Outcome.OK, result
    return result.data


def _checkpoint(service: RegistryService, run, pointer: str):
    result = service.write_checkpoint(
        tenant_id=run.tenant_id, run_id=run.id, expected_version=run.version,
        checkpoint_pointer=pointer,
    )
    assert result.outcome == Outcome.OK, result
    return result.data


def seed(service: RegistryService, tenant_id: str, *, prefix: str = "DEMO") -> None:
    """Populate one card in (roughly) every board column for `tenant_id`,
    plus one Development-column card that has visibly bounced back from
    a failed verification pass, so a human viewing the dashboard sees
    every required card behavior at least once.
    """
    cloud, region, node = random.choice(_CLOUDS)

    # Analysis (intake)
    r = _mk_run(service, tenant_id, f"{prefix}-101", _REPOS[0], "spot")
    _place(service, r, cloud, region, node)

    # Analysis (research)
    r = _mk_run(service, tenant_id, f"{prefix}-102", _REPOS[1], "spot")
    r = _place(service, r, cloud, region, node)
    _advance(service, r, "research")

    # Design (plan authoring)
    r = _mk_run(service, tenant_id, f"{prefix}-103", _REPOS[2], "on_demand")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    _advance(service, r, "plan_authoring")

    # Design (plan approval gate -- "stalled at the gate")
    r = _mk_run(service, tenant_id, f"{prefix}-104", _REPOS[0], "spot")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    r = _advance(service, r, "plan_authoring")
    _advance(service, r, "plan_approval_gate")

    # Development (implementation, with a couple of checkpoints written)
    r = _mk_run(service, tenant_id, f"{prefix}-105", _REPOS[1], "on_demand")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    r = _advance(service, r, "plan_authoring")
    r = _advance(service, r, "plan_approval_gate")
    r = _advance(service, r, "implementation")
    r = _checkpoint(service, r, "s3://checkpoints/demo-105/ckpt-1")
    _checkpoint(service, r, "s3://checkpoints/demo-105/ckpt-2")

    # Testing (verification)
    r = _mk_run(service, tenant_id, f"{prefix}-106", _REPOS[2], "spot")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    r = _advance(service, r, "plan_authoring")
    r = _advance(service, r, "plan_approval_gate")
    r = _advance(service, r, "implementation")
    _advance(service, r, "verification")

    # Development, bounced back from a failed verification pass (the
    # dashboard only renders the "bounced back" badge once its own
    # poller has observed the verification -> implementation edge, so
    # this run is left at "verification" here; `seed_demo.py`'s CLI
    # entry point does the bounce-back transition live, right before the
    # server starts polling, so a viewer sees the badge appear for real.
    r = _mk_run(service, tenant_id, f"{prefix}-107", _REPOS[0], "spot")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    r = _advance(service, r, "plan_authoring")
    r = _advance(service, r, "plan_approval_gate")
    r = _advance(service, r, "implementation")
    _advance(service, r, "verification")

    # PR Review (change review gate)
    r = _mk_run(service, tenant_id, f"{prefix}-108", _REPOS[1], "on_demand")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    r = _advance(service, r, "plan_authoring")
    r = _advance(service, r, "plan_approval_gate")
    r = _advance(service, r, "implementation")
    r = _advance(service, r, "verification")
    _advance(service, r, "change_review_gate")

    # Commit (packaging)
    r = _mk_run(service, tenant_id, f"{prefix}-109", _REPOS[2], "on_demand")
    r = _place(service, r, cloud, region, node)
    r = _advance(service, r, "research")
    r = _advance(service, r, "plan_authoring")
    r = _advance(service, r, "plan_approval_gate")
    r = _advance(service, r, "implementation")
    r = _advance(service, r, "verification")
    r = _advance(service, r, "change_review_gate")
    _advance(service, r, "packaging")


def bounce_run_107_back_to_development(service: RegistryService, tenant_id: str, *, prefix: str = "DEMO") -> None:
    """Live-transition DEMO-107 from verification back to implementation
    so a viewer watching the dashboard sees the "bounced back from
    Testing" badge appear on a poll, the same way the live-update test
    proves it. Call this a few seconds after the server is up.
    """
    result = service.list_runs(tenant_id=tenant_id)
    if result.outcome != Outcome.OK:
        return
    for run in result.data:
        if run.jira_key == f"{prefix}-107" and run.stage == "verification":
            service.transition_stage(
                tenant_id=tenant_id, run_id=run.id, expected_version=run.version,
                next_stage="implementation",
            )
            return


def main(argv: list[str] | None = None) -> int:
    import os

    argv = sys.argv[1:] if argv is None else argv
    db_path = argv[0] if argv else os.environ.get("RUN_REGISTRY_DB_PATH", "run_registry.db")
    tenant_id = argv[1] if len(argv) > 1 else "tenant-a"

    service = RegistryService(db_path)
    try:
        seed(service, tenant_id)
        print(f"Seeded demo data for tenant_id={tenant_id!r} into {db_path!r}.")
        print("This is demo/seed data, not real production runs -- see README.md.")
        print("Waiting 5s, then bouncing DEMO-107 back to Development to demonstrate")
        print("the verification-failure edge live...")
        time.sleep(5)
        bounce_run_107_back_to_development(service, tenant_id)
        print("Done: DEMO-107 is now back at 'implementation' after a 'verification' stage.")
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
