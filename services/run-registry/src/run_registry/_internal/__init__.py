"""Private implementation details of the Run Registry.

Nothing under `run_registry._internal` is part of the Registry Service's
public API and nothing here is re-exported from `run_registry/__init__.py`.
Per the F2 brief ("No component gets a direct database connection other
than this service"), the DB connection factory lives here specifically so
there is no shared importable location another component's code would
reach for -- the only caller of `_internal.db` in this whole codebase is
`run_registry.service`. `tests/test_no_direct_db_access.py` enforces this
structurally (not just by convention) by scanning the source tree.
"""
