"""D11 also instruments job-dispatcher's webhook/queue paths (per the
task brief: "job-dispatcher's webhook/queue paths"). Not one of the four
headline acceptance criteria, but real, additive instrumentation that
should ship real events -- proven here directly against the real
`JobDispatcher`, using lightweight stand-ins for its own external
dependencies (a fake capacity provider, a stub Jira client) rather than
D4's full mock Jira server, since this test is about the D11
instrumentation, not re-proving D1's own webhook/tenant-resolution/
capacity logic (already covered by job-dispatcher's own test suite,
unmodified and still 21/21 passing -- see the final agent report).
"""

from __future__ import annotations

from run_registry import RegistryService

from job_dispatcher.capacity import CapacityOutcome, CapacityResult
from job_dispatcher.dispatcher import JobDispatcher
from job_dispatcher.tenant_resolution import ResolvedTrigger, TenantDirectory

from observability import EventSink, ObservabilityClient


class _AlwaysAvailableCapacityProvider:
    def request_capacity(self, tenant_id: str) -> CapacityResult:
        return CapacityResult(outcome=CapacityOutcome.AVAILABLE, tenant_id=tenant_id, capacity_class="spot")


class _AlwaysUnavailableCapacityProvider:
    def request_capacity(self, tenant_id: str) -> CapacityResult:
        return CapacityResult(
            outcome=CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT,
            tenant_id=tenant_id,
            detail="2/2 concurrent jobs already running",
        )


class _StubJiraClient:
    def __init__(self):
        self.comments = []

    def post_comment(self, *, issue_key, body, comment_type):
        self.comments.append((issue_key, body, comment_type))
        return {"outcome": "ok"}


def _make_dispatcher(*, registry, capacity_provider, observability):
    return JobDispatcher(
        secret_lookup=lambda tenant_id: b"unused-in-this-test",
        tenant_directory=TenantDirectory.from_mapping({"PROJ": "tenant-a"}),
        registry=registry,
        capacity_provider=capacity_provider,
        jira_client_for_tenant=lambda tenant_id: _StubJiraClient(),
        observability=observability,
    )


def test_run_created_path_ships_a_stage_transition_event(tmp_path):
    registry = RegistryService(str(tmp_path / "registry.db"))
    sink = EventSink()
    observability = ObservabilityClient(sink=sink, service_name="job-dispatcher")
    dispatcher = _make_dispatcher(registry=registry, capacity_provider=_AlwaysAvailableCapacityProvider(), observability=observability)

    resolved = ResolvedTrigger(tenant_id="tenant-a", jira_key="PROJ-1", project_key="PROJ", repository="acme/app", status="To Do", labels=())
    result = dispatcher._dispatch(resolved)  # internal dispatch entry point, same one handle_webhook uses

    assert result.outcome == "run_created"
    events = [e for e in sink.by_kind("span") if e.name == "stage_transition"]
    assert len(events) == 1
    assert events[0].trace_id == result.run.trace_id
    assert events[0].run_id == result.run.id
    assert events[0].attributes["source"] == "job_dispatcher_webhook"
    registry.close()


def test_queued_path_ships_a_queued_event(tmp_path):
    registry = RegistryService(str(tmp_path / "registry.db"))
    sink = EventSink()
    observability = ObservabilityClient(sink=sink, service_name="job-dispatcher")
    dispatcher = _make_dispatcher(registry=registry, capacity_provider=_AlwaysUnavailableCapacityProvider(), observability=observability)

    resolved = ResolvedTrigger(tenant_id="tenant-a", jira_key="PROJ-2", project_key="PROJ", repository="acme/app", status="To Do", labels=())
    result = dispatcher._dispatch(resolved)

    assert result.outcome == "queued"
    events = [e for e in sink.by_kind("log") if e.name == "webhook_queued"]
    assert len(events) == 1
    assert events[0].tenant_id == "tenant-a"
    assert events[0].attributes["jira_key"] == "PROJ-2"
    assert events[0].attributes["reason"] == CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT.value
    registry.close()


def test_dispatcher_without_observability_client_is_unaffected(tmp_path):
    """The optional param truly defaults to None with no behavior
    change -- job-dispatcher's own, unmodified test suite (which never
    passes `observability=`) is the real proof of this; this is a small,
    local sanity check of the same property."""
    registry = RegistryService(str(tmp_path / "registry.db"))
    dispatcher = _make_dispatcher(registry=registry, capacity_provider=_AlwaysAvailableCapacityProvider(), observability=None)
    resolved = ResolvedTrigger(tenant_id="tenant-a", jira_key="PROJ-3", project_key="PROJ", repository="acme/app", status="To Do", labels=())
    result = dispatcher._dispatch(resolved)
    assert result.outcome == "run_created"
    registry.close()
