"""RegistryService: the single write path and single read path for Run
Registry state (master spec Section 14.14).

No other component in the system -- and no other module in this package
-- holds a database connection. `RegistryService` is the sole owner of a
`_internal.db.ConnectionFactory`; `mcp_server.py` wraps this class's
methods as MCP tools but never touches the store directly.

Every public method:
  * takes an explicit `tenant_id` and never queries without one --
    tenant-scoping is enforced here (via `repository.py`'s WHERE-clause
    scoping) as the single, auditable choke point per Section 14.13;
  * takes the caller's last-read `version` for every mutating op and
    rejects a stale one (STALE_VERSION), never silently overwriting
    (Section 14.14);
  * validates any stage transition against the fixed vocabulary
    (`stages.py`) before writing it, rejecting an illegal one
    (ILLEGAL_TRANSITION) rather than accepting it silently;
  * returns a `Result` (ok / empty / error), never raises a
    `RegistryError` to its caller.
"""

from __future__ import annotations

from run_registry import repository, stages
from run_registry._internal.db import ConnectionFactory
from run_registry.errors import ErrorCode
from run_registry.models import Attempt, Run
from run_registry.result import Result

_VALID_REASONS = frozenset({"initial", "re-plan", "retry", "resumed-after-interruption"})
_VALID_CAPACITY_CLASSES = frozenset({"spot", "on_demand"})


class RegistryService:
    def __init__(self, db_path: str):
        self._db = ConnectionFactory(db_path)

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------
    # create Run
    # ------------------------------------------------------------------
    def create_run(
        self,
        *,
        tenant_id: str,
        jira_key: str,
        repo: str,
        branch: str,
        capacity_class: str,
        trace_id: str,
        worktree: str | None = None,
    ) -> Result[Run]:
        if not tenant_id:
            return Result.fail(ErrorCode.INVALID_INPUT, "tenant_id is required")
        if not jira_key or not repo or not branch or not trace_id:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                "jira_key, repo, branch, and trace_id are required",
            )
        if capacity_class not in _VALID_CAPACITY_CLASSES:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                f"capacity_class must be one of {sorted(_VALID_CAPACITY_CLASSES)}",
            )

        with self._db.lock:
            cur = self._db.cursor()
            ts = repository.now_iso()
            run_id = repository.new_id()
            attempt_id = repository.new_id()
            try:
                repository.insert_run(
                    cur,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    jira_key=jira_key,
                    repo=repo,
                    branch=branch,
                    worktree=worktree,
                    stage=stages.INTAKE,
                    capacity_class=capacity_class,
                    trace_id=trace_id,
                    ts=ts,
                )
                repository.insert_attempt(
                    cur,
                    attempt_id=attempt_id,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    attempt_number=1,
                    reason="initial",
                    starting_stage=stages.INTAKE,
                    trace_id=trace_id,
                    ts=ts,
                )
                repository.insert_stage_history(
                    cur,
                    entry_id=repository.new_id(),
                    run_id=run_id,
                    attempt_id=attempt_id,
                    tenant_id=tenant_id,
                    stage=stages.INTAKE,
                    ts=ts,
                )
                cur.execute(
                    "UPDATE runs SET current_attempt_id = ? WHERE id = ? AND tenant_id = ?",
                    (attempt_id, run_id, tenant_id),
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

            run = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            assert run is not None
            return Result.ok(run)

    # ------------------------------------------------------------------
    # read Run (by ID) / list Runs (scoped by tenant_id)
    # ------------------------------------------------------------------
    def get_run(self, *, tenant_id: str, run_id: str) -> Result[Run]:
        with self._db.lock:
            cur = self._db.cursor()
            run = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
        if run is None:
            # Fail closed: this branch covers "no such run" AND "run
            # belongs to a different tenant" identically -- a caller can
            # never tell the two apart from the response.
            return Result.empty()
        return Result.ok(run)

    def list_runs(
        self,
        *,
        tenant_id: str,
        stage: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Result[list[Run]]:
        with self._db.lock:
            cur = self._db.cursor()
            runs = repository.list_runs(
                cur, tenant_id=tenant_id, stage=stage, limit=limit, offset=offset
            )
        if not runs:
            return Result.empty()
        return Result.ok(runs)

    def list_attempts(self, *, tenant_id: str, run_id: str) -> Result[list[Attempt]]:
        """Full, queryable Attempt history for one Run (append-only)."""
        with self._db.lock:
            cur = self._db.cursor()
            # Fail closed here too: confirm the run belongs to this
            # tenant before returning any attempt for it.
            run = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            if run is None:
                return Result.empty()
            attempts = repository.list_attempts(cur, run_id=run_id, tenant_id=tenant_id)
        if not attempts:
            return Result.empty()
        return Result.ok(attempts)

    # ------------------------------------------------------------------
    # append Attempt
    # ------------------------------------------------------------------
    def append_attempt(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_version: int,
        reason: str,
        starting_stage: str,
        trace_id: str,
    ) -> Result[Attempt]:
        if reason not in _VALID_REASONS:
            return Result.fail(
                ErrorCode.INVALID_INPUT, f"reason must be one of {sorted(_VALID_REASONS)}"
            )
        if not stages.is_valid_stage(starting_stage):
            return Result.fail(
                ErrorCode.INVALID_INPUT, f"'{starting_stage}' is not a known stage"
            )
        if not trace_id:
            return Result.fail(ErrorCode.INVALID_INPUT, "trace_id is required")

        with self._db.lock:
            cur = self._db.cursor()
            run = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            if run is None:
                return Result.empty()
            if run.version != expected_version:
                return Result.fail(
                    ErrorCode.STALE_VERSION,
                    f"expected version {expected_version}, current version is {run.version}; "
                    "re-read the run before retrying",
                )
            if starting_stage != run.stage and not stages.is_legal_transition(
                run.stage, starting_stage
            ):
                return Result.fail(
                    ErrorCode.ILLEGAL_TRANSITION,
                    f"cannot start a new attempt at '{starting_stage}' from current stage "
                    f"'{run.stage}'",
                )

            ts = repository.now_iso()
            try:
                repository.close_attempt(cur, attempt_id=run.current_attempt_id, tenant_id=tenant_id, ts=ts) \
                    if run.current_attempt_id else None
                repository.close_open_stage_history(cur, run_id=run_id, tenant_id=tenant_id, ts=ts)

                attempt_number = repository.count_attempts(cur, run_id=run_id, tenant_id=tenant_id) + 1
                attempt_id = repository.new_id()
                repository.insert_attempt(
                    cur,
                    attempt_id=attempt_id,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    attempt_number=attempt_number,
                    reason=reason,
                    starting_stage=starting_stage,
                    trace_id=trace_id,
                    ts=ts,
                )
                repository.insert_stage_history(
                    cur,
                    entry_id=repository.new_id(),
                    run_id=run_id,
                    attempt_id=attempt_id,
                    tenant_id=tenant_id,
                    stage=starting_stage,
                    ts=ts,
                )
                rowcount = repository.update_run(
                    cur,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    expected_version=expected_version,
                    fields={
                        "stage": starting_stage,
                        "current_attempt_id": attempt_id,
                        "trace_id": trace_id,
                    },
                    ts=ts,
                )
                if rowcount == 0:
                    self._db.rollback()
                    return Result.fail(
                        ErrorCode.STALE_VERSION,
                        "version changed concurrently; re-read the run before retrying",
                    )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

            attempt = next(
                a
                for a in repository.list_attempts(cur, run_id=run_id, tenant_id=tenant_id)
                if a.id == attempt_id
            )
            return Result.ok(attempt)

    # ------------------------------------------------------------------
    # write stage transition (validated)
    # ------------------------------------------------------------------
    def transition_stage(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_version: int,
        next_stage: str,
    ) -> Result[Run]:
        if not stages.is_valid_stage(next_stage):
            return Result.fail(
                ErrorCode.INVALID_INPUT, f"'{next_stage}' is not a known stage"
            )

        with self._db.lock:
            cur = self._db.cursor()
            run = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            if run is None:
                return Result.empty()
            if run.version != expected_version:
                return Result.fail(
                    ErrorCode.STALE_VERSION,
                    f"expected version {expected_version}, current version is {run.version}; "
                    "re-read the run before retrying",
                )
            if not stages.is_legal_transition(run.stage, next_stage):
                return Result.fail(
                    ErrorCode.ILLEGAL_TRANSITION,
                    f"'{next_stage}' is not a legal next stage from '{run.stage}'",
                )

            ts = repository.now_iso()
            try:
                repository.close_open_stage_history(cur, run_id=run_id, tenant_id=tenant_id, ts=ts)
                assert run.current_attempt_id is not None
                repository.insert_stage_history(
                    cur,
                    entry_id=repository.new_id(),
                    run_id=run_id,
                    attempt_id=run.current_attempt_id,
                    tenant_id=tenant_id,
                    stage=next_stage,
                    ts=ts,
                )
                if next_stage in stages.TERMINAL_STAGES:
                    repository.close_attempt(
                        cur, attempt_id=run.current_attempt_id, tenant_id=tenant_id, ts=ts
                    )
                rowcount = repository.update_run(
                    cur,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    expected_version=expected_version,
                    fields={"stage": next_stage},
                    ts=ts,
                )
                if rowcount == 0:
                    self._db.rollback()
                    return Result.fail(
                        ErrorCode.STALE_VERSION,
                        "version changed concurrently; re-read the run before retrying",
                    )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

            updated = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            assert updated is not None
            return Result.ok(updated)

    # ------------------------------------------------------------------
    # write checkpoint pointer (+ optional capacity class, D6's write)
    # ------------------------------------------------------------------
    def write_checkpoint(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_version: int,
        checkpoint_pointer: str,
        capacity_class: str | None = None,
    ) -> Result[Run]:
        if not checkpoint_pointer:
            return Result.fail(ErrorCode.INVALID_INPUT, "checkpoint_pointer is required")
        if capacity_class is not None and capacity_class not in _VALID_CAPACITY_CLASSES:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                f"capacity_class must be one of {sorted(_VALID_CAPACITY_CLASSES)}",
            )

        fields: dict[str, str] = {"checkpoint_pointer": checkpoint_pointer}
        if capacity_class is not None:
            fields["capacity_class"] = capacity_class
        return self._write_fields(
            tenant_id=tenant_id, run_id=run_id, expected_version=expected_version, fields=fields
        )

    # ------------------------------------------------------------------
    # write execution-location update
    # ------------------------------------------------------------------
    def write_execution_location(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_version: int,
        cloud_provider: str | None = None,
        region_az: str | None = None,
        node_id: str | None = None,
    ) -> Result[Run]:
        fields: dict[str, str | None] = {}
        if cloud_provider is not None:
            fields["exec_cloud_provider"] = cloud_provider
        if region_az is not None:
            fields["exec_region_az"] = region_az
        if node_id is not None:
            fields["exec_node_id"] = node_id
        if not fields:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                "at least one of cloud_provider, region_az, node_id is required",
            )
        return self._write_fields(
            tenant_id=tenant_id, run_id=run_id, expected_version=expected_version, fields=fields
        )

    # ------------------------------------------------------------------
    # shared helper for simple field writes that carry the same
    # tenant-scoping / optimistic-concurrency discipline
    # ------------------------------------------------------------------
    def _write_fields(
        self, *, tenant_id: str, run_id: str, expected_version: int, fields: dict
    ) -> Result[Run]:
        with self._db.lock:
            cur = self._db.cursor()
            run = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            if run is None:
                return Result.empty()
            if run.version != expected_version:
                return Result.fail(
                    ErrorCode.STALE_VERSION,
                    f"expected version {expected_version}, current version is {run.version}; "
                    "re-read the run before retrying",
                )
            ts = repository.now_iso()
            try:
                rowcount = repository.update_run(
                    cur,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    expected_version=expected_version,
                    fields=fields,
                    ts=ts,
                )
                if rowcount == 0:
                    self._db.rollback()
                    return Result.fail(
                        ErrorCode.STALE_VERSION,
                        "version changed concurrently; re-read the run before retrying",
                    )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

            updated = repository.get_run(cur, tenant_id=tenant_id, run_id=run_id)
            assert updated is not None
            return Result.ok(updated)
