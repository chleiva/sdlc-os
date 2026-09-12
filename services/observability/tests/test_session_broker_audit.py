"""Acceptance criterion: "A direct session-broker connection to a node
produces its own logged audit event" (master spec Section 16.4:
"identity-gated, no-inbound-port ... every session is itself logged").
"""

from __future__ import annotations

import pytest

from observability import EventSink, MockSessionBrokerBackend, ObservabilityClient
from observability.session_broker import SessionBrokerClient, SessionBrokerError


def test_opening_a_direct_session_is_itself_a_logged_audit_event(observability_client, session_backend, sink):
    handle = observability_client.open_direct_session(
        node_id="i-0123456789abcdef0",
        identity="alice@acme.com",
        reason="trace does not explain a repeated OOM on this node",
        trace_id="trace-direct-access",
        tenant_id="tenant-a",
    )
    assert handle.node_id == "i-0123456789abcdef0"
    assert handle.identity == "alice@acme.com"

    # The mock backend really "opened" a session (structurally: no
    # listening socket, just an opaque token handed back -- see
    # `MockSessionBrokerBackend`).
    assert handle.session_id in session_backend.opened_sessions
    assert session_backend.opened_sessions[handle.session_id] == ("i-0123456789abcdef0", "alice@acme.com")

    # The audit event itself -- not a side effect a caller could forget,
    # the open call IS the audit event.
    audit_events = sink.by_kind("audit")
    assert len(audit_events) == 1
    ev = audit_events[0]
    assert ev.name == "session_broker_open"
    assert ev.trace_id == "trace-direct-access"
    assert ev.attributes["identity"] == "alice@acme.com"
    assert ev.attributes["reason"] == "trace does not explain a repeated OOM on this node"
    assert ev.attributes["node_id"] == "i-0123456789abcdef0"
    assert ev.attributes["session_id"] == handle.session_id


def test_closing_a_direct_session_is_also_a_logged_audit_event(observability_client, session_backend, sink):
    handle = observability_client.open_direct_session(
        node_id="i-node-2", identity="bob@acme.com", reason="debugging infra fault", trace_id="trace-2"
    )
    observability_client.session_broker.close_session(handle, trace_id="trace-2")

    assert handle.session_id in session_backend.closed_sessions
    audit_events = sink.by_kind("audit")
    assert {e.name for e in audit_events} == {"session_broker_open", "session_broker_close"}
    close_event = next(e for e in audit_events if e.name == "session_broker_close")
    assert close_event.attributes["session_id"] == handle.session_id


def test_identity_gated_refuses_to_open_without_a_resolved_identity(sink):
    client = SessionBrokerClient(MockSessionBrokerBackend(), sink)
    with pytest.raises(SessionBrokerError):
        client.open_session(node_id="i-node-3", identity="", reason="some reason")
    # No audit event for a refused open -- nothing was actually opened.
    assert sink.by_kind("audit") == []


def test_refuses_to_open_without_a_reason():
    """A reason is required precisely because this is the *exceptional*
    path (Section 16.4): the audit trail must say why the normal
    off-node trace/dashboard path did not suffice, not just who
    connected."""
    sink_local = EventSink()
    client = SessionBrokerClient(MockSessionBrokerBackend(), sink_local)
    with pytest.raises(SessionBrokerError):
        client.open_session(node_id="i-node-4", identity="carol@acme.com", reason="")
    assert sink_local.by_kind("audit") == []
