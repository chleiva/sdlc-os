"""Connection factory and migration runner for the Run Registry's store.

This module is the ONLY place in the codebase that imports `sqlite3` or
opens a connection to the underlying store. It is private
(`run_registry._internal.db`) and is not imported by anything except
`run_registry.service.RegistryService`. See that class's docstring and
the package `_internal/__init__.py` for why.

Local dev / test store: SQLite, via Python's stdlib `sqlite3`. Local dev
is explicitly all this needs to be for now (F2 brief: "SQLite is fine for
now"). To keep a later swap to a managed Postgres (spec Section 14.12)
straightforward:

  * No SQLite-only SQL features are used in migrations/ or in
    repository.py's queries (no `AUTOINCREMENT`, no `INSERT OR REPLACE`,
    no SQLite pragfindma-specific functions). Primary keys are
    application-generated UUIDs, not DB-assigned integers.
  * Parameter placeholders are the only sqlite3-specific detail
    (`?` positional) -- swapping engines means changing this module's
    `connect()`/`executemany` plumbing and repository.py's placeholder
    style, never the schema or the query logic itself.
  * Timestamps are ISO-8601 TEXT, portable to Postgres TEXT or TIMESTAMPTZ
    without a data migration.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "migrations"


def _migration_files() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any migration files not yet recorded in schema_migrations.

    Returns the list of migration version numbers newly applied (empty if
    the schema was already up to date). Idempotent: safe to call on every
    process start.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    newly_applied: list[int] = []
    for path in _migration_files():
        version = int(path.name.split("_", 1)[0])
        if version in applied:
            continue
        sql = path.read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        conn.commit()
        newly_applied.append(version)
    return newly_applied


class ConnectionFactory:
    """Opens (and migrates) a connection to the Registry's store.

    One instance per `RegistryService`; not a process-wide singleton and
    not exported anywhere a caller could import it independently of the
    service. Thread-safe via a lock around each use, since sqlite3
    connections aren't safe for concurrent use from multiple threads by
    default.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = _connect(db_path)
        apply_migrations(self._conn)

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def close(self) -> None:
        self._conn.close()
