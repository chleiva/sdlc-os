"""End-to-end (within D4's own boundary) test of the Automation-rule
payload contract: a Jira Automation event for an opted-in story
produces a real, Sec. 17.3-signed dispatch to D1's job-dispatcher
endpoint; a story lacking the opt-in signal produces none.

Acceptance criterion "A story without the opt-in label, even in the
trigger status, produces no webhook" is proven end to end here (not
just at the gating-function level in test_gating.py): the full
`RelayApp.handle_automation_event` path, backed by a real `get-issue`
call against the mock Jira server, refuses to construct a
`SignedDispatch` at all.

Acceptance criterion "A plan comment posted, then a transition to the
approval status, correctly resumes the paused run (via D1/D2)" is
provable only up to D4's own boundary, since D1/D2 do not exist yet in
this repository (Wave 1, built in parallel -- see the brief's
"Explicitly not in scope"). What *is* proven here, honestly, up to
that boundary:
  1. Posting a plan comment and transitioning to the approval status
     are both real Jira Cloud REST API v3 calls that succeed against
     the mock (see test_jira_client.py).
  2. The same transition, when it is the one a human performs to
     signal approval, is exactly the event an Automation rule
     (automation-rule.json) would re-fire on, which this test proves
     produces a validly-signed dispatch carrying the issue key -- the
     one thing D1 needs to resume the paused run.
  3. The signature D4 produces verifies correctly using D1's *own*
     verification logic (`webhook_signing.verify_request`), i.e. the
     handoff contract at the D4/D1 boundary is exercised for real.
What is NOT proven here (because D1/D2 do not exist): that D1 actually
receives this HTTP request, resolves it to a paused run, and resumes
that run's orchestration. That is D1's/D2's own test surface once they
exist.
"""

import pytest

from issue_tracker.gating import NotOptedInError
from issue_tracker.webhook_relay import RelayApp, RelayError
from issue_tracker.webhook_signing import verify_request


def _make_relay(tenant_config, jira_client, dispatcher_url="http://d1.internal/dispatch"):
    secrets = {"tenant-acme": b"tenant-acme-webhook-secret-key"}
    repos = {("tenant-acme", "PROJ"): "github.com/acme/widget-service"}
    return RelayApp(
        jira_client_for_tenant=lambda tenant_id: jira_client,
        config_for_tenant=lambda tenant_id: tenant_config,
        secret_for_tenant=lambda tenant_id: secrets.get(tenant_id),
        repository_for_project=lambda tenant_id, project_key: repos.get((tenant_id, project_key)),
        dispatcher_url=dispatcher_url,
    ), secrets


def test_opted_in_story_in_trigger_status_produces_signed_dispatch(jira_client, tenant_config):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Relay epic", description="d", acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Relay opted-in story",
        description="d", size="S", labels=["ai-factory"],
    )
    key = story["data"]["issue_key"]

    relay, secrets = _make_relay(tenant_config, jira_client)
    dispatch = relay.handle_automation_event({"tenant_id": "tenant-acme", "issue_key": key, "project_key": "PROJ"})

    assert dispatch.opted_in_story.issue_key == key
    assert dispatch.body_json()["repository"] == "github.com/acme/widget-service"

    # The D4/D1 boundary: D1's own verification logic must accept this
    # signature, using only the per-tenant secret it holds -- no shared
    # in-process state between "signer" and "verifier" here.
    verify = verify_request(
        headers=dispatch.headers, body=dispatch.body,
        secret_lookup=lambda tenant_id: secrets.get(tenant_id),
    )
    assert verify.ok
    assert verify.tenant_id == "tenant-acme"


def test_story_without_opt_in_label_produces_no_dispatch(jira_client, tenant_config):
    """The acceptance criterion, proven end to end through the relay:
    even though the story is in the trigger status, lacking the opt-in
    label means `handle_automation_event` never returns a dispatch --
    it raises `NotOptedInError` instead, which is structurally
    impossible to mistake for a `SignedDispatch`.
    """
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Relay epic no label", description="d", acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Relay non-opted-in story",
        description="d", size="S",  # no labels -> no opt-in signal
    )
    key = story["data"]["issue_key"]
    assert story["data"]["issue_key"]  # sanity: story really was created, in the trigger status

    relay, _secrets = _make_relay(tenant_config, jira_client)
    with pytest.raises(NotOptedInError):
        relay.handle_automation_event({"tenant_id": "tenant-acme", "issue_key": key, "project_key": "PROJ"})


def test_unresolvable_repository_refuses_dispatch(jira_client, tenant_config):
    epic = jira_client.create_epic(
        project_key="PROJ", summary="Relay epic unknown project", description="d", acceptance_criteria=["ac"],
    )
    story = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Relay story unknown project",
        description="d", size="S", labels=["ai-factory"],
    )
    key = story["data"]["issue_key"]

    relay, _secrets = _make_relay(tenant_config, jira_client)
    with pytest.raises(RelayError):
        relay.handle_automation_event({"tenant_id": "tenant-acme", "issue_key": key, "project_key": "UNKNOWN"})


def test_malformed_automation_payload_is_rejected(jira_client, tenant_config):
    relay, _secrets = _make_relay(tenant_config, jira_client)
    with pytest.raises(RelayError):
        relay.handle_automation_event({"tenant_id": "tenant-acme"})  # missing issue_key/project_key


def test_http_relay_server_end_to_end_over_real_sockets(jira_client, tenant_config):
    """Same proof as above, but over a real HTTP POST to a real
    (loopback) socket, using `webhook_relay.serve_forever` -- not just
    an in-process function call -- since a story-without-label webhook
    ultimately must not go out over the wire, not merely fail to
    return a Python object.
    """
    import json
    import threading
    import urllib.request

    from issue_tracker.webhook_relay import serve_forever

    epic = jira_client.create_epic(
        project_key="PROJ", summary="Relay HTTP epic", description="d", acceptance_criteria=["ac"],
    )
    opted_in = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Relay HTTP opted-in story",
        description="d", size="S", labels=["ai-factory"],
    )
    not_opted_in = jira_client.create_story(
        project_key="PROJ", epic_key=epic["data"]["issue_key"], summary="Relay HTTP non-opted-in story",
        description="d", size="S",
    )

    forwarded = []
    relay, _secrets = _make_relay(tenant_config, jira_client)
    server = serve_forever(relay, forward=lambda dispatch: forwarded.append(dispatch))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        def post(issue_key: str) -> dict:
            body = json.dumps({"tenant_id": "tenant-acme", "issue_key": issue_key, "project_key": "PROJ"}).encode()
            req = urllib.request.Request(f"http://{host}:{port}/", data=body, method="POST",
                                          headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())

        ok_response = post(opted_in["data"]["issue_key"])
        assert ok_response["dispatched"] is True
        assert len(forwarded) == 1

        no_dispatch_response = post(not_opted_in["data"]["issue_key"])
        assert no_dispatch_response["dispatched"] is False
        assert len(forwarded) == 1  # still just the one -- nothing forwarded for the non-opted-in story
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
