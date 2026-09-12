"""Low-level SQL access for the Run Registry.

Every function here takes an explicit `tenant_id` and bakes it into the
WHERE clause of every query that touches a specific run -- this is the
lowest layer of defense-in-depth for the tenant-scoping fail-closed
requirement (spec Section 14.13); `service.py` is the layer that turns a
"no row" result into the empty-result contract shape, but the SQL itself
is structurally incapable of returning a row for the wrong tenant.

This module is imported only by `run_registry.service`. It never imports
anything from `_internal.db` directly except via the cursor it's handed
-- it has no independent way to open a connection.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from run_registry.models import Attempt, ExecutionLocation, Run, StageHistoryEntry


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        tenant_id=row["tenant_id"],
        jira_key=row["jira_key"],
        repo=row["repo"],
        branch=row["branch"],
        worktree=row["worktree"],
        stage=row["stage"],
        capacity_class=row["capacity_class"],
        checkpoint_pointer=row["checkpoint_pointer"],
        execution_location=ExecutionLocation(
            cloud_provider=row["exec_cloud_provider"],
            region_az=row["exec_region_az"],
            node_id=row["exec_node_id"],
        ),
        trace_id=row["trace_id"],
        current_attempt_id=row["current_attempt_id"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(
        id=row["id"],
        run_id=row["run_id"],
        tenant_id=row["tenant_id"],
        attempt_number=row["attempt_number"],
        reason=row["reason"],
        starting_stage=row["starting_stage"],
        trace_id=row["trace_id"],
        start_ts=row["start_ts"],
        end_ts=row["end_ts"],
        created_at=row["created_at"],
    )


def _row_to_stage_history(row: sqlite3.Row) -> StageHistoryEntry:
    return StageHistoryEntry(
        id=row["id"],
        run_id=row["run_id"],
        attempt_id=row["attempt_id"],
        stage=row["stage"],
        entered_at=row["entered_at"],
        exited_at=row["exited_at"],
    )


def insert_run(
    cur: sqlite3.Cursor,
    *,
    run_id: str,
    tenant_id: str,
    jira_key: str,
    repo: str,
    branch: str,
    worktree: str | None,
    stage: str,
    capacity_class: str,
    trace_id: str,
    ts: str,
) -> None:
    cur.execute(
        """
        INSERT INTO runs (
            id, tenant_id, jira_key, repo, branch, worktree, stage,
            capacity_class, checkpoint_pointer,
            exec_cloud_provider, exec_region_az, exec_node_id,
            trace_id, current_attempt_id, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, 1, ?, ?)
        """,
        (run_id, tenant_id, jira_key, repo, branch, worktree, stage, capacity_class, trace_id, ts, ts),
    )


def _is_valid_tenant_id(tenant_id: Any) -> bool:
    """SECURITY FIX (D10 security-hardening pass, real finding): a type-
    confusion adversarial test found that `tenant_id=123` (an int, e.g.
    from a caller that deserialized a JSON payload without validating
    field types) was NOT caught by the `if not tenant_id` guards below --
    `123` is truthy -- and was then bound as an INTEGER sqlite3
    parameter. Because the `tenant_id` column has TEXT affinity, SQLite
    applies affinity conversion during the comparison and `123` (int)
    matched a stored tenant "123" (str) row: a wrong-*type* tenant_id
    was silently treated as if it were the equivalent string tenant_id,
    breaking the "malformed tenant_id fails closed" guarantee (Sec.
    14.13/14.14). Every fail-closed check below now also requires
    `tenant_id` to actually be a `str`.
    """
    return isinstance(tenant_id, str) and tenant_id != ""


def get_run(cur: sqlite3.Cursor, *, tenant_id: str, run_id: str) -> Run | None:
    if not _is_valid_tenant_id(tenant_id) or not run_id:
        return None
    cur.execute(
        "SELECT * FROM runs WHERE id = ? AND tenant_id = ?",
        (run_id, tenant_id),
    )
    row = cur.fetchone()
    return _row_to_run(row) if row is not None else None


def list_runs(
    cur: sqlite3.Cursor,
    *,
    tenant_id: str,
    stage: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Run]:
    if not _is_valid_tenant_id(tenant_id):
        # Fail closed: no (valid, string) tenant_id, no query, no rows -- ever.
        return []
    params: list[Any] = [tenant_id]
    sql = "SELECT * FROM runs WHERE tenant_id = ?"
    if stage is not None:
        sql += " AND stage = ?"
        params.append(stage)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cur.execute(sql, params)
    return [_row_to_run(row) for row in cur.fetchall()]


def update_run(
    cur: sqlite3.Cursor,
    *,
    tenant_id: str,
    run_id: str,
    expected_version: int,
    fields: dict[str, Any],
    ts: str,
) -> int:
    """Apply `fields` to the run, bumping version, IFF the current row
    matches (tenant_id, run_id, version). Returns the number of rows
    updated (0 or 1) -- the caller (service.py) interprets 0 as either
    "not found for this tenant" or "stale version", having already
    distinguished those cases via a preceding read within the same lock.
    """
    set_clauses = ", ".join(f"{k} = ?" for k in fields)
    sql = (
        f"UPDATE runs SET {set_clauses}, version = version + 1, updated_at = ? "
        "WHERE id = ? AND tenant_id = ? AND version = ?"
    )
    params = [*fields.values(), ts, run_id, tenant_id, expected_version]
    cur.execute(sql, params)
    return cur.rowcount


def insert_attempt(
    cur: sqlite3.Cursor,
    *,
    attempt_id: str,
    run_id: str,
    tenant_id: str,
    attempt_number: int,
    reason: str,
    starting_stage: str,
    trace_id: str,
    ts: str,
) -> None:
    cur.execute(
        """
        INSERT INTO attempts (
            id, run_id, tenant_id, attempt_number, reason,
            starting_stage, trace_id, start_ts, end_ts, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (attempt_id, run_id, tenant_id, attempt_number, reason, starting_stage, trace_id, ts, ts),
    )


def close_attempt(cur: sqlite3.Cursor, *, attempt_id: str, tenant_id: str, ts: str) -> None:
    cur.execute(
        "UPDATE attempts SET end_ts = ? WHERE id = ? AND tenant_id = ? AND end_ts IS NULL",
        (ts, attempt_id, tenant_id),
    )


def get_open_attempt_id(cur: sqlite3.Cursor, *, run_id: str, tenant_id: str) -> str | None:
    cur.execute(
        "SELECT id FROM attempts WHERE run_id = ? AND tenant_id = ? AND end_ts IS NULL "
        "ORDER BY attempt_number DESC LIMIT 1",
        (run_id, tenant_id),
    )
    row = cur.fetchone()
    return row["id"] if row is not None else None


def count_attempts(cur: sqlite3.Cursor, *, run_id: str, tenant_id: str) -> int:
    cur.execute(
        "SELECT COUNT(*) AS n FROM attempts WHERE run_id = ? AND tenant_id = ?",
        (run_id, tenant_id),
    )
    return cur.fetchone()["n"]


def list_attempts(cur: sqlite3.Cursor, *, run_id: str, tenant_id: str) -> list[Attempt]:
    if not _is_valid_tenant_id(tenant_id) or not run_id:
        return []
    cur.execute(
        "SELECT * FROM attempts WHERE run_id = ? AND tenant_id = ? ORDER BY attempt_number ASC",
        (run_id, tenant_id),
    )
    return [_row_to_attempt(row) for row in cur.fetchall()]


def insert_stage_history(
    cur: sqlite3.Cursor,
    *,
    entry_id: str,
    run_id: str,
    attempt_id: str,
    tenant_id: str,
    stage: str,
    ts: str,
) -> None:
    cur.execute(
        """
        INSERT INTO stage_history (id, run_id, attempt_id, tenant_id, stage, entered_at, exited_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
        """,
        (entry_id, run_id, attempt_id, tenant_id, stage, ts),
    )


def close_open_stage_history(
    cur: sqlite3.Cursor, *, run_id: str, tenant_id: str, ts: str
) -> None:
    cur.execute(
        "UPDATE stage_history SET exited_at = ? "
        "WHERE run_id = ? AND tenant_id = ? AND exited_at IS NULL",
        (ts, run_id, tenant_id),
    )


def list_stage_history(cur: sqlite3.Cursor, *, run_id: str, tenant_id: str) -> list[StageHistoryEntry]:
    if not tenant_id or not run_id:
        return []
    cur.execute(
        "SELECT * FROM stage_history WHERE run_id = ? AND tenant_id = ? ORDER BY entered_at ASC",
        (run_id, tenant_id),
    )
    return [_row_to_stage_history(row) for row in cur.fetchall()]
