#!/usr/bin/env python3
"""Stub/mock Repository Index MCP server (F3).

Runnable standalone, no real index behind it:

    python3 services/mcp-stubs/index/server.py

Speaks MCP over stdio. Every tool's advertised input/output schema comes
verbatim from schema/index.schema.json (this process never hand-copies a
shape). Which canned outcome a call gets is driven entirely by the
tenant_id in the request -- see services/mcp-stubs/_common/scenarios.py
and the README for the full sentinel-tenant_id convention.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anyio

from _common.schema_loader import load_schema
from _common.server_runtime import ToolImpl, build_server, run_stdio

SCHEMA_PATH = Path(__file__).parent / "schema" / "index.schema.json"
SCHEMA = load_schema(SCHEMA_PATH)


def find_definition_success(args: dict) -> dict:
    symbol = args["symbol"]
    return {
        "definitions": [
            {
                "file": f"src/{symbol.lower()}.py",
                "line": 42,
                "column": 5,
                "symbol": symbol,
                "kind": "function",
                "snippet": f"def {symbol}(...):",
            }
        ]
    }


def find_definition_empty(args: dict) -> dict:
    return {
        "reason": f"No definition found for symbol '{args['symbol']}' in repository '{args['repository']}'.",
    }


def find_references_success(args: dict) -> dict:
    symbol = args["symbol"]
    return {
        "references": [
            {"file": "src/service.py", "line": 88, "column": 12, "context_snippet": f"result = {symbol}(payload)"},
            {"file": "tests/test_service.py", "line": 15, "column": 8, "context_snippet": f"assert {symbol}(x) == y"},
        ],
        "total_count": 2,
        "exhaustive": True,
    }


def find_references_empty(args: dict) -> dict:
    return {
        "reason": f"Symbol '{args['symbol']}' has zero references anywhere in the index.",
        "exhaustive": True,
    }


def find_callers_success(args: dict) -> dict:
    symbol = args["symbol"]
    return {
        "hop": 1,
        "callers": [
            {"file": "src/handlers.py", "line": 120, "call_site_symbol": symbol, "containing_symbol": "handle_request"},
        ],
        "next_cursor": None,
    }


def find_callers_empty(args: dict) -> dict:
    return {"reason": f"'{args['symbol']}' has no callers anywhere in the index."}


def search_success(args: dict) -> dict:
    return {
        "results": [
            {"file": "src/auth/session.py", "score": 0.83, "symbol": "SessionManager", "snippet": "class SessionManager: ..."},
            {"file": "docs/auth.md", "score": 0.61, "snippet": "## Session handling"},
        ],
        "authoritative": False,
        "label": "non-authoritative",
    }


def search_empty(args: dict) -> dict:
    return {
        "reason": f"No candidates matched query '{args['query']}'.",
        "authoritative": False,
        "label": "non-authoritative",
    }


def get_file_module_summary_success(args: dict) -> dict:
    path = args["path"]
    return {
        "path": path,
        "summary": (
            f"Generated summary of {path}: defines the module's public API "
            "surface. Large/generated file -- full contents not returned "
            "(spec Sec. 6.3); use the source-control server's "
            "get-file-contents if the caller has decided it must fully "
            "read this file."
        ),
        "symbol_count": 37,
        "generated": path.endswith((".pb.go", ".g.dart", "_pb2.py")),
    }


def get_file_module_summary_empty(args: dict) -> dict:
    return {"reason": f"'{args['path']}' is indexed but empty (zero bytes) -- nothing to summarize."}


def get_ownership_metadata_success(args: dict) -> dict:
    return {
        "path": args["path"],
        "owners": ["@platform-team", "@jane-doe"],
        "change_frequency": {
            "commits_last_90d": 14,
            "last_modified": "2026-08-30T09:15:00Z",
            "top_contributors": ["jane-doe", "chris"],
        },
    }


def get_ownership_metadata_empty(args: dict) -> dict:
    return {
        "reason": f"'{args['path']}' has no CODEOWNERS entry and no commit history (untracked/new file).",
    }


IMPLS = {
    "find-definition": ToolImpl(find_definition_success, find_definition_empty),
    "find-references": ToolImpl(find_references_success, find_references_empty),
    "find-callers": ToolImpl(find_callers_success, find_callers_empty),
    "search": ToolImpl(search_success, search_empty),
    "get-file-module-summary": ToolImpl(get_file_module_summary_success, get_file_module_summary_empty),
    "get-ownership-metadata": ToolImpl(get_ownership_metadata_success, get_ownership_metadata_empty),
}

server = build_server("index-stub", SCHEMA["version"], SCHEMA, IMPLS)

if __name__ == "__main__":
    anyio.run(run_stdio, server)
