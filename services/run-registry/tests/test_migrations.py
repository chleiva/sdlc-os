"""Migrations are versioned, tracked, and idempotent to apply."""

from __future__ import annotations

from run_registry._internal.db import _connect, apply_migrations


def test_migrations_apply_and_are_tracked(tmp_path):
    db_path = str(tmp_path / "mig.db")
    conn = _connect(db_path)
    try:
        applied = apply_migrations(conn)
        assert applied == [1]

        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert [r[0] for r in rows] == [1]

        # Re-applying is a no-op.
        applied_again = apply_migrations(conn)
        assert applied_again == []

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"runs", "attempts", "stage_history", "schema_migrations"} <= tables
    finally:
        conn.close()
