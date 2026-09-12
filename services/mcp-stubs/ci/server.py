#!/usr/bin/env python3
"""Stub/mock CI MCP server (F3).

Runnable standalone, no real CI behind it:

    python3 services/mcp-stubs/ci/server.py

See services/mcp-stubs/README.md for the tenant_id-driven scenario
convention shared by every stub server in this deliverable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anyio

from _common.schema_loader import load_schema
from _common.server_runtime import ToolImpl, build_server, run_stdio

SCHEMA_PATH = Path(__file__).parent / "schema" / "ci.schema.json"
SCHEMA = load_schema(SCHEMA_PATH)


def trigger_run_success(args: dict) -> dict:
    return {"run_id": "run-70042", "status": "queued", "triggered_at": "2026-09-12T10:10:00Z"}


def trigger_run_empty(args: dict) -> dict:
    return {
        "reason": f"Resolved scope for ref '{args['ref']}' contains no runnable CI jobs (e.g. a docs-only change); no run triggered.",
        "resolved_scope": [],
    }


def get_run_status_result_success(args: dict) -> dict:
    return {
        "run_id": args["run_id"],
        "status": "passed",
        "started_at": "2026-09-12T10:10:05Z",
        "completed_at": "2026-09-12T10:14:30Z",
        "results": {
            "tests": {"total": 214, "passed": 214, "failed": 0},
            "security_scan": {"findings": 0, "highest_severity": None},
        },
    }


def get_run_status_result_empty(args: dict) -> dict:
    return {
        "reason": f"Run '{args['run_id']}' completed, but no tests were applicable to the changed scope.",
    }


IMPLS = {
    "trigger-run": ToolImpl(trigger_run_success, trigger_run_empty),
    "get-run-status-result": ToolImpl(get_run_status_result_success, get_run_status_result_empty),
}

server = build_server("ci-stub", SCHEMA["version"], SCHEMA, IMPLS)

if __name__ == "__main__":
    anyio.run(run_stdio, server)
