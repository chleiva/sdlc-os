"""Bridges to F3's own contract artifacts -- imported/read directly, never
hand-copied, so this server cannot silently drift from the schema F3
fixed (see CLAUDE.md's "master spec wins" rule, applied here to F3's
schema being the ground truth for D3's wire shape).

This module does NOT modify anything under services/mcp-stubs/; it only
reads/imports from it, exactly as the D3 brief asks for.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MCP_STUBS_ROOT = Path(__file__).resolve().parents[3] / "mcp-stubs"
INDEX_SCHEMA_PATH = MCP_STUBS_ROOT / "index" / "schema" / "index.schema.json"

if str(MCP_STUBS_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_STUBS_ROOT))

# Reused verbatim from F3 -- the envelope shape (ok/empty/error) and the
# four named error condition strings are part of the contract every MCP
# server in this system shares (spec Sec. 7.6), not something specific
# to the stub. Importing it directly (rather than retyping the same
# three functions here) is what the brief means by "don't hand-copy/
# retype it and risk drift".
from _common import envelope  # noqa: E402
from _common.scenarios import ERROR_CONDITIONS  # noqa: E402


def load_index_schema() -> dict:
    return json.loads(INDEX_SCHEMA_PATH.read_text())


__all__ = ["envelope", "ERROR_CONDITIONS", "load_index_schema", "INDEX_SCHEMA_PATH", "MCP_STUBS_ROOT"]
