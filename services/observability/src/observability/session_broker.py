"""Session broker for exceptional direct node access (master spec
Section 16.4): "when an operator genuinely needs to reach the node
itself ... access goes through the cloud's identity-gated session broker
-- AWS Systems Manager Session Manager, or the equivalent on other
clouds -- not SSH. No inbound port is opened on the node to support this;
the broker connects outbound from the node and every session is itself
logged, which means this 'exceptional' access path is also an audit
event in its own right, not a hole in the audit trail."

There is no real AWS SSM (or equivalent) available in this environment,
so `MockSessionBrokerBackend` stands in for it. What's structurally real
regardless of backend:
  * identity-gated: `SessionBrokerClient.open_session` refuses to open a
    session without a resolved, non-empty `identity` string.
  * reason-gated: likewise refuses without a non-empty `reason` (the
    audit trail has to say *why* someone bypassed the normal off-node
    trace/dashboard path, not just *who*).
  * no-inbound-port semantics, modeled structurally: the backend
    Protocol has no "listen"/"accept" shape at all -- `open_session`'s
    only signature is "given a node id and an identity, hand back a
    session handle", exactly the shape of an outbound-initiated broker
    call (`ssm:StartSession`), never a connect-to-node-address call.
  * every open (and close) is itself a real, logged audit event shipped
    to the same off-node `EventSink` everything else in this library
    ships to -- see `tests/test_session_broker_audit.py`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from observability.sink import EventSink


@dataclass(frozen=True)
class SessionHandle:
    session_id: str
    node_id: str
    identity: str
    reason: str
    opened_at: float


class SessionBrokerBackend(Protocol):
    """Everything a real cloud's identity-gated broker needs to provide.
    No inbound-port-shaped method exists on this Protocol at all -- see
    module docstring."""

    def open_session(self, *, node_id: str, identity: str) -> str:
        """Returns an opaque backend session token/id."""
        ...

    def close_session(self, session_token: str) -> None: ...


class MockSessionBrokerBackend:
    """Stands in for AWS SSM Session Manager (or the equivalent broker on
    another cloud) -- no real AWS SSM account/agent exists in this
    environment. Real about this stand-in: it never opens a listening
    socket or accepts an inbound connection (mirroring the no-inbound-port
    property), and every call is deterministic/inspectable
    (`self.opened_sessions`) for a test to assert against directly.
    """

    def __init__(self) -> None:
        self.opened_sessions: dict[str, tuple[str, str]] = {}  # token -> (node_id, identity)
        self.closed_sessions: list[str] = []

    def open_session(self, *, node_id: str, identity: str) -> str:
        token = f"mock-ssm-session-{uuid.uuid4().hex[:12]}"
        self.opened_sessions[token] = (node_id, identity)
        return token

    def close_session(self, session_token: str) -> None:
        self.closed_sessions.append(session_token)


class SessionBrokerError(Exception):
    pass


class SessionBrokerClient:
    """The identity-gated front door. Constructed with a
    `SessionBrokerBackend` (real: `MockSessionBrokerBackend` here; a
    production deployment swaps in a `boto3`-backed one implementing the
    same Protocol -- see module docstring) and the shared `EventSink`
    every audit event lands in."""

    def __init__(self, backend: SessionBrokerBackend, sink: EventSink) -> None:
        self._backend = backend
        self._sink = sink

    def open_session(
        self,
        *,
        node_id: str,
        identity: str,
        reason: str,
        trace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> SessionHandle:
        if not identity:
            raise SessionBrokerError(
                "a resolved identity is required to open a direct session (Section 16.4: identity-gated)"
            )
        if not reason:
            raise SessionBrokerError(
                "a reason is required to open a direct session -- exceptional direct access must state "
                "why the off-node trace/dashboard did not explain the fault (Section 16.4)"
            )
        token = self._backend.open_session(node_id=node_id, identity=identity)
        handle = SessionHandle(
            session_id=token, node_id=node_id, identity=identity, reason=reason, opened_at=time.time()
        )
        # Every session open is itself a logged audit event -- not a side
        # effect a caller could forget to record, the open call IS the
        # audit event.
        self._sink.emit(
            kind="audit",
            name="session_broker_open",
            trace_id=trace_id,
            run_id=None,
            tenant_id=tenant_id,
            attributes={"node_id": node_id, "identity": identity, "reason": reason, "session_id": token},
        )
        return handle

    def close_session(self, handle: SessionHandle, *, trace_id: str | None = None) -> None:
        self._backend.close_session(handle.session_id)
        self._sink.emit(
            kind="audit",
            name="session_broker_close",
            trace_id=trace_id,
            run_id=None,
            tenant_id=None,
            attributes={"node_id": handle.node_id, "identity": handle.identity, "session_id": handle.session_id},
        )
