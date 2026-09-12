"""Loads the F3 issue-tracker JSON Schema. F3 owns that schema file
(services/mcp-stubs/issue-tracker/schema/issue-tracker.schema.json) as
the single source of truth for the wire contract; this module only
reads it, never copies or forks its content.
"""

import json
from pathlib import Path


def load_schema(schema_path: str | Path) -> dict:
    return json.loads(Path(schema_path).read_text())
