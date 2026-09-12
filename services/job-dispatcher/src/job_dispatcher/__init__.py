"""D1: Job Dispatcher, Tenant Resolution, Webhook Auth (SDLC Auto, Wave 1).

The single entry point for every external trigger (master spec Sec.
4.4, 14.11). Public surface kept deliberately small and ordered to
match the mandatory call sequence:

  1. `webhook_auth` -- Sec. 17.3 HMAC + timestamp verification, first
     and unconditionally.
  2. `tenant_resolution` -- Sec. 4.4 Jira-project-to-tenant mapping,
     only reachable with a value `webhook_auth` produced.
  3. `capacity` / `tenant_queue` -- Sec. 14.11/14.13 capacity request +
     per-tenant queueing.
  4. `dispatcher.JobDispatcher` -- wires the above together with F2's
     `RegistryService.create_run` and D4's `JiraClient.post_comment`.
"""

from job_dispatcher.dispatcher import DispatchError, DispatchResult, JobDispatcher
from job_dispatcher.tenant_resolution import TenantDirectory
from job_dispatcher.webhook_auth import AuthenticatedTrigger, AuthenticationError

__all__ = [
    "JobDispatcher",
    "DispatchResult",
    "DispatchError",
    "TenantDirectory",
    "AuthenticatedTrigger",
    "AuthenticationError",
]
