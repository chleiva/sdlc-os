"""CLI entry point to apply pending migrations against a DB file.

    python -m run_registry.migrate [path/to/db.sqlite]

Defaults to `run_registry.db` in the current directory (or
$RUN_REGISTRY_DB_PATH). This is a thin wrapper around
`_internal.db.apply_migrations` for operators/CI -- normal service
startup already applies pending migrations itself
(`ConnectionFactory.__init__`), so this is for pre-flighting a migration
independently of starting the service.
"""

from __future__ import annotations

import os
import sys

from run_registry._internal.db import _connect, apply_migrations


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    db_path = argv[0] if argv else os.environ.get("RUN_REGISTRY_DB_PATH", "run_registry.db")
    conn = _connect(db_path)
    try:
        applied = apply_migrations(conn)
    finally:
        conn.close()
    if applied:
        print(f"Applied migrations: {applied}")
    else:
        print("Schema already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
