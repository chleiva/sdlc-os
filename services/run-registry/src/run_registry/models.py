"""Typed shapes returned by the Registry Service.

These are plain dataclasses, not ORM models -- `repository.py` builds
them from `sqlite3.Row` objects, and `mcp_server.py` serializes them to
plain dicts for the MCP tool result payload. Field names and types here
are the Registry Service's public data contract; changing them is a
shared-contract change per the repo's own ground rules (CLAUDE.md /
00-README.md rule 4) and should not be done unilaterally.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionLocation:
    cloud_provider: str | None = None
    region_az: str | None = None
    node_id: str | None = None


@dataclass(frozen=True)
class Attempt:
    """One immutable Attempt record (spec Section 14.14).

    Attempts are append-only: once created, `reason`, `starting_stage`,
    `trace_id`, `start_ts`, `run_id`, `tenant_id`, and `attempt_number`
    never change. `end_ts` is set exactly once, when the attempt
    concludes (a new Attempt starts on the same Run, or the Run reaches a
    terminal stage) -- this is the one permitted mutation and it never
    rewrites the historical fields above.
    """

    id: str
    run_id: str
    tenant_id: str
    attempt_number: int
    reason: str  # one of: initial | re-plan | retry | resumed-after-interruption
    starting_stage: str
    trace_id: str
    start_ts: str
    end_ts: str | None
    created_at: str


@dataclass(frozen=True)
class StageHistoryEntry:
    id: str
    run_id: str
    attempt_id: str
    stage: str
    entered_at: str
    exited_at: str | None


@dataclass(frozen=True)
class Run:
    """Current, aggregate view of one story's progress (spec Section 14.12).

    `version` is the optimistic-concurrency token: every write must be
    given the version last read for this run, per Section 14.14.
    """

    id: str
    tenant_id: str
    jira_key: str
    repo: str
    branch: str
    worktree: str | None
    stage: str
    capacity_class: str
    checkpoint_pointer: str | None
    execution_location: ExecutionLocation
    trace_id: str
    current_attempt_id: str | None
    version: int
    created_at: str
    updated_at: str
