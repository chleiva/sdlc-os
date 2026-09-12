"""Structural (fitness) test: this deliverable never opens the Registry's
underlying store directly.

Brief, acceptance criteria: "No direct database connection to the
Registry exists anywhere in this deliverable's code." This is a
grep-based check over fleet-dashboard's own source tree (never
services/run-registry -- we don't own that code and don't scan it) for:

  * any `import sqlite3` / `from sqlite3 import ...`, and
  * any literal reference to a `.db` file path (the pattern F2's own
    Registry Service and tests use for its SQLite file).

A hit on either means someone reached around `RegistryService` for a raw
connection -- which this test is here to catch before it ships.
"""

from __future__ import annotations

import re
from pathlib import Path

_THIS_DELIVERABLE_ROOT = Path(__file__).resolve().parent.parent  # services/fleet-dashboard/
_SRC_DIR = _THIS_DELIVERABLE_ROOT / "src"
_TESTS_DIR = _THIS_DELIVERABLE_ROOT / "tests"

_SQLITE_IMPORT_RE = re.compile(r"^\s*(import\s+sqlite3\b|from\s+sqlite3\s+import\b)", re.MULTILINE)
# Any literal ".db" file-extension reference (e.g. "registry.db",
# "run_registry.db") -- the Registry's own store file naming convention.
_DB_FILE_LITERAL_RE = re.compile(r"[\"']([^\"'\n]*\.db)[\"']")

_ALLOWED_DB_LITERAL_CONTEXTS = (
    # These are *default paths handed to RegistryService itself* (the one
    # supported entry point), not a raw connection -- see the file's own
    # surrounding code. Listed explicitly so this test stays a real
    # tripwire rather than a rubber stamp: any new literal not on this
    # list fails the test and must be justified here.
    "run_registry.db",  # default db_path string passed to RegistryService(...)
    "registry.db",       # test fixtures' tmp_path db file, also passed to RegistryService(...)
)


# This test file itself legitimately mentions "sqlite3" and ".db" -- once
# in the regex patterns above (as literal text to search FOR, not a
# usage), and once in a runtime import used only to assert that a
# DashboardService instance holds no such object (see the third test
# below). Both are the checker, not the thing being checked, so this
# file is excluded from the two static scans and covered instead by that
# runtime assertion.
_SELF = Path(__file__).resolve()


def _iter_deliverable_source_files():
    """Every .py file that ships as part of this deliverable: its
    installable package (src/fleet_dashboard) and its test suite, MINUS
    this checker file itself (see docstring above).
    """
    for directory in (_SRC_DIR, _TESTS_DIR):
        for path in directory.rglob("*.py"):
            if path.resolve() != _SELF:
                yield path


def test_no_sqlite3_import_anywhere_in_this_deliverable():
    hits = []
    for path in _iter_deliverable_source_files():
        text = path.read_text(encoding="utf-8")
        if _SQLITE_IMPORT_RE.search(text):
            hits.append(str(path))
    assert hits == [], f"sqlite3 imported directly in: {hits}"


def test_no_unexplained_db_file_literal_anywhere_in_this_deliverable():
    hits = []
    for path in _iter_deliverable_source_files():
        text = path.read_text(encoding="utf-8")
        for match in _DB_FILE_LITERAL_RE.finditer(text):
            literal = match.group(1)
            basename = literal.rsplit("/", 1)[-1]
            if basename not in _ALLOWED_DB_LITERAL_CONTEXTS:
                hits.append((str(path), literal))
    assert hits == [], f"unexplained .db file literal(s): {hits}"


def test_dashboard_service_holds_no_sqlite_connection_attribute():
    """Belt-and-braces runtime check alongside the static grep above:
    a live DashboardService instance has no attribute that is itself a
    sqlite3.Connection/Cursor -- it only holds a RegistryService.
    """
    import sqlite3

    from fleet_dashboard.dashboard_service import DashboardService
    from run_registry import RegistryService

    registry = RegistryService(":memory:")
    try:
        svc = DashboardService(registry)
        for attr_name in dir(svc):
            if attr_name.startswith("__"):
                continue
            value = getattr(svc, attr_name, None)
            assert not isinstance(value, (sqlite3.Connection, sqlite3.Cursor)), attr_name
    finally:
        registry.close()
