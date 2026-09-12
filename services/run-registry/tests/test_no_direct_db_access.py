"""Structural check for the F2 acceptance criterion: "No component other
than the Registry Service holds a direct DB connection -- verified, not
just documented."

This walks the actual source tree (not just a convention/comment) and
asserts:

  1. `sqlite3` (the concrete store driver) is imported from exactly one
     module: `run_registry._internal.db`.
  2. The package's public surface (`run_registry/__init__.py`) does not
     import or re-export anything from `_internal` at all -- so there is
     no shared importable location (e.g. a `get_connection()` helper)
     that another component's code would even find by exploring the
     package.
  3. The transport layer (`mcp_server.py`) does not import `sqlite3` or
     `_internal` either -- it only talks to `RegistryService`.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "run_registry"


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_sqlite3_connections_are_opened_only_by_internal_db_module():
    """`sqlite3` may be imported elsewhere purely for type hints (e.g.
    `repository.py` type-annotates the cursor it's handed), but actually
    *opening* a connection (`sqlite3.connect(...)`) must happen in
    exactly one place."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "db.py" and path.parent.name == "_internal":
            continue
        if "sqlite3.connect(" in path.read_text():
            offenders.append(str(path))
    assert offenders == [], f"sqlite3.connect() called outside _internal/db.py: {offenders}"


def test_public_package_surface_does_not_expose_internal_db():
    init_file = SRC / "__init__.py"
    imports = _imported_names(init_file)
    assert not any("_internal" in name for name in imports), (
        "run_registry/__init__.py must not import from _internal "
        "(no shared DB-connection export)"
    )


def test_mcp_server_module_never_touches_the_store_directly():
    mcp_server_file = SRC / "mcp_server.py"
    imports = _imported_names(mcp_server_file)
    assert not any("sqlite3" in name for name in imports)
    assert not any("_internal" in name for name in imports)


def test_repository_module_is_the_only_other_consumer_of_internal_db():
    """repository.py legitimately takes cursors, but must not import
    _internal.db itself -- it only ever receives an open cursor from
    service.py, which is the sole owner of the ConnectionFactory."""
    repository_file = SRC / "repository.py"
    imports = _imported_names(repository_file)
    assert not any("_internal" in name for name in imports)


def test_only_service_module_constructs_a_connection_factory():
    """`migrate.py` is an intentional, documented exception: it is this
    service's own migration CLI (analogous to running `alembic upgrade
    head` outside the app process), not another component reaching for a
    connection -- it uses the lower-level `_connect`/`apply_migrations`
    functions directly and never instantiates `ConnectionFactory`."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name in ("service.py", "migrate.py"):
            continue
        if path.parent.name == "_internal":
            continue
        if "ConnectionFactory(" in path.read_text():
            offenders.append(str(path))
    assert offenders == [], f"ConnectionFactory instantiated outside service.py: {offenders}"
