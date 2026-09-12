"""Bot-identity audit logging (spec Section 15 / Section 17.1).

Every GitHub API call this service makes is attributable to the App's own
installation identity -- never something indistinguishable from a human
using a personal access token. GitHub itself gives every App installation
a bot actor login of the form `<app-slug>[bot]` (e.g. `sdlc-auto[bot]`),
which is how the call shows up in GitHub's own audit log / PR "opened by"
field; this module mirrors that same identity into *our* audit trail so
the two are reconcilable, per spec Section 17.1's NHI-inventory
requirement ("named, not anonymous").

`AuditLogger` is the single place that constructs an audit record for an
outbound call -- nothing else in this package is allowed to fabricate one
with a different actor shape (see tests/test_bot_identity.py, a
structural test in the same spirit as F2's no-direct-db-access fitness
test).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

_BOT_ACTOR_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\[bot\]$")


def bot_actor(app_slug: str) -> str:
    """The App installation's bot identity, exactly as GitHub renders it
    (e.g. as the PR author or commit-status creator): `<app-slug>[bot]`.
    Raises if `app_slug` would not produce a well-formed GitHub bot login,
    so a caller can never accidentally construct something that reads as
    a human account."""
    actor = f"{app_slug}[bot]"
    if not _BOT_ACTOR_RE.match(actor):
        raise ValueError(f"{app_slug!r} does not produce a well-formed GitHub bot actor login")
    return actor


def is_bot_actor(actor: str) -> bool:
    return bool(_BOT_ACTOR_RE.match(actor))


@dataclass(frozen=True)
class AuditEvent:
    actor: str
    installation_id: str
    tenant_id: str
    action: str
    repository: str | None
    outcome: str  # "ok" | "denied" | "revoked" | "error"
    detail: str = ""
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "installation_id": self.installation_id,
            "tenant_id": self.tenant_id,
            "action": self.action,
            "repository": self.repository,
            "outcome": self.outcome,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }


class AuditLogger:
    """In-memory append-only audit trail. A real deployment would ship
    these events to the organization's central log sink; the in-memory
    list here is what tests inspect directly."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(
        self,
        *,
        app_slug: str,
        installation_id: str,
        tenant_id: str,
        action: str,
        repository: str | None,
        outcome: str,
        detail: str = "",
    ) -> AuditEvent:
        event = AuditEvent(
            actor=bot_actor(app_slug),
            installation_id=installation_id,
            tenant_id=tenant_id,
            action=action,
            repository=repository,
            outcome=outcome,
            detail=detail,
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()
