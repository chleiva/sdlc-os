-- Migration 0001: initial Run Registry schema.
--
-- Portability note: this schema intentionally avoids SQLite-only features
-- (no AUTOINCREMENT, no dynamic typing tricks). Primary keys are
-- application-generated UUIDs (TEXT) rather than DB-assigned integers, and
-- timestamps are stored as ISO-8601 TEXT. Both patterns work unchanged on
-- a managed Postgres (Aurora / Cloud SQL / Azure Database for PostgreSQL,
-- per spec Section 14.12) with no schema rewrite -- swapping the storage
-- engine later should only touch the connection/driver layer in
-- `_internal/db.py`, never these DDL statements or the queries in
-- `repository.py`.
--
-- Stage vocabulary is fixed per master spec Section 5 (nine stages) plus
-- the terminal pair from Section 14.14 (Completed, Abandoned). It is
-- enforced here via CHECK constraints as a second line of defense; the
-- authoritative enforcement is the Registry Service's transition
-- validation (see src/run_registry/stages.py), which is what actually
-- rejects illegal *transitions* -- the CHECK constraint only guarantees
-- the stored value is always one of the eleven known tokens, never a
-- free-text status string.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,

    -- Run and story identity (spec Section 14.12).
    jira_key             TEXT NOT NULL,
    repo                 TEXT NOT NULL,
    branch               TEXT NOT NULL,
    worktree             TEXT,

    -- Current pipeline stage: fixed nine-stage-plus-terminal vocabulary,
    -- never free text.
    stage TEXT NOT NULL CHECK (stage IN (
        'intake',
        'research',
        'plan_authoring',
        'plan_approval_gate',
        'implementation',
        'verification',
        'change_review_gate',
        'packaging',
        'retrospective',
        'completed',
        'abandoned'
    )),

    -- Current execution location (spec Section 14.12).
    exec_cloud_provider  TEXT,
    exec_region_az       TEXT,
    exec_node_id         TEXT,

    -- Capacity class + checkpoint pointer (spec Section 14.12, 14.8, 9.3).
    capacity_class TEXT NOT NULL CHECK (capacity_class IN ('spot', 'on_demand')),
    checkpoint_pointer   TEXT,

    -- FK to the Section 16.1 trace ID -- mirrors the current/latest
    -- Attempt's trace_id for convenience (see attempts.trace_id for the
    -- authoritative per-attempt value).
    trace_id             TEXT NOT NULL,

    -- Points at the latest Attempt row; the Run's stage always reflects
    -- this Attempt's progress (spec Section 14.14).
    current_attempt_id   TEXT,

    -- Optimistic concurrency (spec Section 14.14).
    version              INTEGER NOT NULL DEFAULT 1,

    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_runs_tenant_stage ON runs (tenant_id, stage);

-- Append-only Attempt history (spec Section 14.14). Rows are never
-- updated except to seal end_ts once, when the attempt concludes (either
-- because a new Attempt starts on the same Run, or the Run reaches a
-- terminal stage) -- the historical fields (reason, starting_stage,
-- trace_id, start_ts) are never rewritten.
CREATE TABLE IF NOT EXISTS attempts (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs (id),
    tenant_id        TEXT NOT NULL,
    attempt_number   INTEGER NOT NULL,

    reason TEXT NOT NULL CHECK (reason IN (
        'initial',
        're-plan',
        'retry',
        'resumed-after-interruption'
    )),

    starting_stage   TEXT NOT NULL,
    trace_id         TEXT NOT NULL,
    start_ts         TEXT NOT NULL,
    end_ts           TEXT,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_run ON attempts (run_id);
CREATE INDEX IF NOT EXISTS idx_attempts_tenant ON attempts (tenant_id);

-- Per-stage entry/exit timestamps (spec Section 14.12), one row per stage
-- entry. Feeds Section 20's cycle-time / rework metrics directly off the
-- Registry rather than a parallel system.
CREATE TABLE IF NOT EXISTS stage_history (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs (id),
    attempt_id  TEXT NOT NULL REFERENCES attempts (id),
    tenant_id   TEXT NOT NULL,
    stage       TEXT NOT NULL,
    entered_at  TEXT NOT NULL,
    exited_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_stage_history_run ON stage_history (run_id);
