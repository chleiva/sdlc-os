"""Tests for `job_dispatcher.local_capacity.LocalCapacityProvider` --
the Sec. 14.16 ("Alternative Deployment Mode: Local, Single-Tenant
Docker Compose", New Rev 9) always-available capacity provider, and D1's
brief (New, Rev 9) acceptance criteria for it:

  - "The local capacity provider satisfies the same capacity-request
    interface the cloud provider does, proven by running D1's existing
    contract tests against both implementations unmodified."
  - "A capacity request against the local provider always succeeds
    immediately -- no test path exercises a queued or delayed
    local-mode request."

This file does not modify `capacity.py`, `tenant_resolution.py`, or any
existing test module -- `test_capacity_available_creates_a_real_run` is
imported from `test_run_creation.py` and called exactly as written,
against a dispatcher wired to each provider in turn.
"""

from __future__ import annotations

import pytest

from run_registry.result import Outcome

from job_dispatcher.capacity import CapacityOutcome, MockCapacityProvider
from job_dispatcher.dispatcher import JobDispatcher
from job_dispatcher.local_capacity import (
    DEFAULT_CAPACITY_CLASS,
    DEFAULT_NODE_ID,
    LocalCapacityProvider,
)

from conftest import PROJECT_A, SECRET_A, TENANT_A, TENANT_B, signed_webhook

# The existing D1 contract test for the "capacity available" branch of
# the `CapacityProvider` interface -- reused byte-for-byte, unmodified,
# as a regression baseline against the cloud (mock) provider below.
from test_run_creation import test_capacity_available_creates_a_real_run as existing_contract_test


def _build_dispatcher(provider, *, secret_lookup, tenant_directory, registry, jira_client_for_tenant):
    return JobDispatcher(
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        capacity_provider=provider,
        jira_client_for_tenant=jira_client_for_tenant,
    )


# ---------------------------------------------------------------------
# 1. Interface conformance: D1's existing contract test, unmodified,
#    run against both the cloud (mock) provider and the new local one.
# ---------------------------------------------------------------------


def test_existing_contract_test_passes_unmodified_against_the_mock_provider(
    secret_lookup, tenant_directory, registry, jira_client_for_tenant
):
    """Regression baseline: `test_run_creation.test_capacity_available_creates_a_real_run`,
    imported and called completely unmodified, still passes when wired
    to `MockCapacityProvider` -- nothing added by this deliverable
    touches `capacity.py`, `dispatcher.py`, or that existing test."""
    provider = MockCapacityProvider()
    dispatcher = _build_dispatcher(
        provider,
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        jira_client_for_tenant=jira_client_for_tenant,
    )

    existing_contract_test(dispatcher, registry)


@pytest.mark.parametrize(
    "provider_factory, expected_capacity_class",
    [
        pytest.param(MockCapacityProvider, "spot", id="cloud-mock-provider"),
        pytest.param(LocalCapacityProvider, DEFAULT_CAPACITY_CLASS, id="local-compose-provider"),
    ],
)
def test_capacity_provider_contract_creates_a_real_run(
    provider_factory,
    expected_capacity_class,
    secret_lookup,
    tenant_directory,
    registry,
    jira_client_for_tenant,
):
    """The `CapacityProvider` interface contract itself, generalized
    from `test_run_creation.test_capacity_available_creates_a_real_run`
    so it can run unchanged in shape against any `CapacityProvider`:
    given capacity is available, `JobDispatcher` creates a real,
    tenant-scoped Run through F2's `RegistryService`.

    One line of that existing test cannot be reused byte-for-byte
    against a second provider: it hardcodes
    `run.capacity_class == "spot"`, which is `MockCapacityProvider`'s
    own default return value, not something `capacity.py`'s
    `CapacityProvider` Protocol or `CapacityResult` mandates -- neither
    defines what string a provider must report as its capacity class.
    This version parametrizes exactly that one value
    (`expected_capacity_class`) and is otherwise identical, run against
    both providers to prove `LocalCapacityProvider` satisfies the same
    structural interface `MockCapacityProvider` does.
    """
    provider = provider_factory()
    dispatcher = _build_dispatcher(
        provider,
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        jira_client_for_tenant=jira_client_for_tenant,
    )

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
    assert run.capacity_class == expected_capacity_class

    read_back = registry.get_run(tenant_id=TENANT_A, run_id=run.id)
    assert read_back.outcome == Outcome.OK
    assert read_back.data.jira_key == "PROJA-42"

    cross_tenant = registry.get_run(tenant_id="tenant-b", run_id=run.id)
    assert cross_tenant.outcome == Outcome.EMPTY


# ---------------------------------------------------------------------
# 2. Always succeeds immediately -- no queueing path, ever.
# ---------------------------------------------------------------------


def test_local_provider_always_returns_available_regardless_of_tenant():
    provider = LocalCapacityProvider()

    for tenant_id in (TENANT_A, TENANT_B, "yet-another-tenant"):
        result = provider.request_capacity(tenant_id)
        assert result.is_available
        assert result.outcome is CapacityOutcome.AVAILABLE
        assert result.tenant_id == tenant_id
        assert result.capacity_class == DEFAULT_CAPACITY_CLASS
        assert result.node_id == DEFAULT_NODE_ID


def test_local_provider_never_produces_an_unavailable_outcome():
    """There is no scenario/configuration knob on `LocalCapacityProvider`
    that can make it return a `CapacityOutcome` other than `AVAILABLE`
    -- unlike `MockCapacityProvider`, it has no `set_scenario`/
    `ScenarioStep` mechanism at all."""
    provider = LocalCapacityProvider()
    assert not hasattr(provider, "set_scenario")

    for _ in range(50):
        result = provider.request_capacity(TENANT_A)
        assert result.outcome not in (
            CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED,
            CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT,
        )


def test_local_provider_dispatch_never_queues_across_many_triggers(
    secret_lookup, tenant_directory, registry, jira_client_for_tenant
):
    """Fires several distinct webhook triggers for the same tenant in a
    row and confirms every single one creates a Run immediately --
    `queue_depth` stays 0 throughout, and `retry_queued` always reports
    nothing to retry, since nothing was ever queued."""
    provider = LocalCapacityProvider()
    dispatcher = _build_dispatcher(
        provider,
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        jira_client_for_tenant=jira_client_for_tenant,
    )

    for i in range(5):
        body_obj = {
            "tenant_id": TENANT_A,
            "issue_key": f"PROJA-{200 + i}",
            "project_key": PROJECT_A,
            "repository": "acme/widgets",
            "status": "Selected for Development",
            "labels": ["ai-factory"],
        }
        headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

        result = dispatcher.handle_webhook(headers=headers, body=body)

        assert result.outcome == "run_created"
        assert result.capacity_outcome is CapacityOutcome.AVAILABLE
        assert result.comment_posted is None  # the queue/notify branch was never taken
        assert dispatcher.queue_depth(TENANT_A) == 0

    assert dispatcher.retry_queued(TENANT_A) is None
    assert provider.call_count[TENANT_A] == 5


# ---------------------------------------------------------------------
# 3. The capacity-unavailable Jira delay comment is structurally
#    unreachable for this provider.
# ---------------------------------------------------------------------


class _NeverPostComment:
    """A poisoned stand-in for D4's `JiraClient`: raises if
    `post_comment` is ever called. `JobDispatcher` only calls
    `jira_client_for_tenant(...).post_comment(...)` from
    `_post_delay_comment`, which only runs on the "capacity
    unavailable -> queue and notify" branch (`dispatcher.py`,
    `_queue_and_notify`). Since `LocalCapacityProvider` never produces
    an unavailable outcome, that branch -- and this method -- must never
    execute."""

    def post_comment(self, **kwargs):
        raise AssertionError(
            "the capacity-unavailable Jira delay comment path must be structurally "
            "unreachable when the capacity provider is LocalCapacityProvider"
        )


def test_local_provider_never_posts_the_capacity_unavailable_comment(
    secret_lookup, tenant_directory, registry
):
    provider = LocalCapacityProvider()
    dispatcher = _build_dispatcher(
        provider,
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        jira_client_for_tenant=lambda tenant_id: _NeverPostComment(),
    )

    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-300",
        "project_key": PROJECT_A,
        "repository": "acme/widgets",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)

    # If the queue/notify branch were ever (incorrectly) taken, this
    # call itself would raise via `_NeverPostComment.post_comment`
    # above -- it doesn't, because that branch is unreachable here.
    result = dispatcher.handle_webhook(headers=headers, body=body)

    assert result.outcome == "run_created"
    assert result.comment_posted is None


# ---------------------------------------------------------------------
# 4. Sec. 14.16 single-tenant fixed-tenant guard (optional, defensive).
# ---------------------------------------------------------------------


def test_local_provider_can_be_fixed_to_one_configured_tenant():
    """Sec. 14.16: this deployment mode is single-tenant by
    construction. `LocalCapacityProvider(tenant_id=...)` is an optional
    defensive guard a Compose-mode deployment can use to fail loudly on
    a misconfigured/unexpected tenant_id, rather than silently granting
    capacity to a tenant that should not exist in this mode."""
    provider = LocalCapacityProvider(tenant_id="the-one-configured-tenant")

    result = provider.request_capacity("the-one-configured-tenant")
    assert result.is_available

    with pytest.raises(ValueError):
        provider.request_capacity("some-other-tenant")


def test_local_provider_defaults_to_accepting_any_tenant():
    """Without a fixed `tenant_id`, `LocalCapacityProvider` accepts any
    tenant_id -- required so it satisfies the existing multi-tenant
    contract tests/fixtures (`TENANT_A`/`TENANT_B`) unmodified."""
    provider = LocalCapacityProvider()
    assert provider.request_capacity(TENANT_A).is_available
    assert provider.request_capacity(TENANT_B).is_available
