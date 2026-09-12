"""Run Registry package public surface.

Deliberately small: `RegistryService` is the only supported entry point
for reading or writing run state (spec Section 14.14 -- "no component
holds a direct database connection to the Registry's underlying store").
This module does NOT import or re-export anything from `_internal.db` --
there is no `get_connection()` or similar convenience export for other
code to reach for. See `tests/test_no_direct_db_access.py`.
"""

from run_registry.errors import ErrorCode, RegistryError
from run_registry.models import Attempt, ExecutionLocation, Run, StageHistoryEntry
from run_registry.result import Outcome, Result
from run_registry.service import RegistryService

__all__ = [
    "RegistryService",
    "Result",
    "Outcome",
    "Run",
    "Attempt",
    "ExecutionLocation",
    "StageHistoryEntry",
    "ErrorCode",
    "RegistryError",
]
