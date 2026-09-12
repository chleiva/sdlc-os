"""Acceptance criterion: "An unsigned or replayed (>5min old) webhook is
rejected before any tenant-resolution or capacity-provisioning code
runs."

This proves it two ways, per the brief's explicit instruction not to
settle for "returns 401":

  1. A call-counting spy replacing `tenant_resolution.require_resolved`
     (the only function `dispatcher.py` calls to go from an
     authenticated trigger to anything downstream) proves the spy is
     never invoked on a rejected request, and *is* invoked on an
     accepted one (the positive control -- without it, "never called"
     could just mean the spy is wired up wrong).
  2. A poisoned `RegistryService`/`CapacityProvider`/`JiraClient`
     substitute that raises on any call proves the same thing one
     layer further down: even if `require_resolved` had a bug that let
     something through, nothing downstream of it executes either.
"""

from __future__ import annotations

import time

import pytest

import job_dispatcher.dispatcher as dispatcher_module
from job_dispatcher.dispatcher import JobDispatcher
from job_dispatcher.webhook_auth import AuthenticationError

from conftest import PROJECT_A, SECRET_A, TENANT_A, signed_webhook


class _NeverCallMe(Exception):
    """Raised by every poisoned downstream stub below -- if this ever
    fires, an unauthenticated request reached code it must not reach."""


def _poisoned_registry():
    class PoisonedRegistry:
        def create_run(self, **kwargs):
            raise _NeverCallMe("RegistryService.create_run must not run before auth passes")

    return PoisonedRegistry()


def _poisoned_capacity_provider():
    class PoisonedProvider:
        def request_capacity(self, tenant_id):
            raise _NeverCallMe("CapacityProvider.request_capacity must not run before auth passes")

    return PoisonedProvider()


def _poisoned_jira_client_for_tenant():
    def _for_tenant(tenant_id):
        raise _NeverCallMe("jira_client_for_tenant must not run before auth passes")

    return _for_tenant


@pytest.fixture
def poisoned_dispatcher(tenant_directory, secret_lookup):
    """A JobDispatcher wired so that reaching tenant resolution,
    capacity provisioning, or Jira commenting raises -- the strongest
    possible proof that a rejected-at-auth request never gets there."""
    return JobDispatcher(
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=_poisoned_registry(),
        capacity_provider=_poisoned_capacity_provider(),
        jira_client_for_tenant=_poisoned_jira_client_for_tenant(),
    )


def test_unsigned_request_never_reaches_downstream_code(poisoned_dispatcher):
    body = b'{"tenant_id":"tenant-a","issue_key":"PROJA-1","project_key":"PROJA","repository":"org/repo"}'
    # No signature headers at all.
    with pytest.raises(AuthenticationError) as exc_info:
        poisoned_dispatcher.handle_webhook(headers={}, body=body)
    assert exc_info.value.rejected.reject_reason == "missing-tenant-id"


def test_stale_timestamp_never_reaches_downstream_code(poisoned_dispatcher):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-1",
        "project_key": PROJECT_A,
        "repository": "org/repo",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    six_minutes_ago = time.time() - 360  # > 5 minute replay window
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj, now=six_minutes_ago)

    with pytest.raises(AuthenticationError) as exc_info:
        poisoned_dispatcher.handle_webhook(headers=headers, body=body)
    assert exc_info.value.rejected.reject_reason == "stale-timestamp"


def test_tampered_signature_never_reaches_downstream_code(poisoned_dispatcher):
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-1",
        "project_key": PROJECT_A,
        "repository": "org/repo",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)
    tampered_body = body.replace(b"PROJA-1", b"PROJA-999")

    with pytest.raises(AuthenticationError) as exc_info:
        poisoned_dispatcher.handle_webhook(headers=headers, body=tampered_body)
    assert exc_info.value.rejected.reject_reason == "signature-mismatch"


def test_spy_proves_tenant_resolution_is_never_called_on_rejection(
    monkeypatch, tenant_directory, secret_lookup, registry, capacity_provider, jira_client_for_tenant
):
    """The stronger, call-counter-based proof the brief asks for:
    `dispatcher.require_resolved` (the single function that turns an
    AuthenticatedTrigger into anything tenant-resolution-shaped) is
    replaced with a spy. An unsigned request must produce zero calls to
    it; a validly-signed one must produce exactly one -- proving the
    spy itself is correctly wired (not just silently never called for
    an unrelated reason).
    """
    calls = []
    real_require_resolved = dispatcher_module.require_resolved

    def spy_require_resolved(*, trigger, directory):
        calls.append(trigger)
        return real_require_resolved(trigger=trigger, directory=directory)

    monkeypatch.setattr(dispatcher_module, "require_resolved", spy_require_resolved)

    dispatcher = JobDispatcher(
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        capacity_provider=capacity_provider,
        jira_client_for_tenant=jira_client_for_tenant,
    )

    # -- negative case: unsigned request --
    with pytest.raises(AuthenticationError):
        dispatcher.handle_webhook(headers={}, body=b"{}")
    assert calls == [], "tenant resolution must not be called for an unsigned request"

    # -- negative case: stale timestamp --
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-1",
        "project_key": PROJECT_A,
        "repository": "org/repo",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }
    stale_headers, stale_body = signed_webhook(
        tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj, now=time.time() - 301
    )
    with pytest.raises(AuthenticationError):
        dispatcher.handle_webhook(headers=stale_headers, body=stale_body)
    assert calls == [], "tenant resolution must not be called for a stale-timestamped request"

    # -- positive control: a validly-signed, fresh request DOES reach it --
    fresh_headers, fresh_body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)
    dispatcher.handle_webhook(headers=fresh_headers, body=fresh_body)
    assert len(calls) == 1, "a validly-authenticated request must reach tenant resolution exactly once"


def test_replay_window_boundary(poisoned_dispatcher):
    """299s old (just inside 5 minutes) is accepted through auth (may
    still fail later for unrelated reasons, but must NOT be an
    AuthenticationError); 301s old (just outside) is rejected."""
    body_obj = {
        "tenant_id": TENANT_A,
        "issue_key": "PROJA-1",
        "project_key": PROJECT_A,
        "repository": "org/repo",
        "status": "Selected for Development",
        "labels": ["ai-factory"],
    }

    just_inside_headers, just_inside_body = signed_webhook(
        tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj, now=time.time() - 299
    )
    # Passes auth -> reaches the poisoned registry/capacity provider ->
    # raises _NeverCallMe, NOT AuthenticationError. That distinction IS
    # the proof auth accepted it.
    with pytest.raises(_NeverCallMe):
        poisoned_dispatcher.handle_webhook(headers=just_inside_headers, body=just_inside_body)

    just_outside_headers, just_outside_body = signed_webhook(
        tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj, now=time.time() - 301
    )
    with pytest.raises(AuthenticationError) as exc_info:
        poisoned_dispatcher.handle_webhook(headers=just_outside_headers, body=just_outside_body)
    assert exc_info.value.rejected.reject_reason == "stale-timestamp"
