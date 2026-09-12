#!/usr/bin/env python3
"""Stub/mock Source-Control (GitHub App or equivalent) MCP server (F3).

Runnable standalone, no real GitHub behind it:

    python3 services/mcp-stubs/source-control/server.py

See services/mcp-stubs/README.md for the tenant_id-driven scenario
convention shared by every stub server in this deliverable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anyio

from _common.schema_loader import load_schema
from _common.server_runtime import ToolImpl, build_server, run_stdio

SCHEMA_PATH = Path(__file__).parent / "schema" / "source-control.schema.json"
SCHEMA = load_schema(SCHEMA_PATH)


def create_branch_worktree_success(args: dict) -> dict:
    return {
        "branch_name": args["branch_name"],
        "base_ref": args["base_ref"],
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "worktree_path": f"/workspaces/{args['repository'].split('/')[-1]}/.worktrees/{args['session_id']}",
    }


def create_branch_worktree_empty(args: dict) -> dict:
    return {
        "reason": f"Branch '{args['branch_name']}' already exists pointing at base ref '{args['base_ref']}'; no new branch created.",
    }


def open_pr_success(args: dict) -> dict:
    return {"pr_number": 4821, "url": f"https://github.com/{args['repository']}/pull/4821", "state": "draft" if args.get("draft") else "open"}


def open_pr_empty(args: dict) -> dict:
    return {
        "reason": f"An open PR already exists for {args['head_branch']} -> {args['base_branch']}; no new PR opened.",
        "existing_pr_number": 4821,
    }


def get_pr_check_status_success(args: dict) -> dict:
    return {
        "pr_number": args["pr_number"],
        "state": "open",
        "mergeable": True,
        "checks": [
            {"name": "unit-tests", "status": "completed", "conclusion": "success", "url": "https://ci.example.com/runs/9001"},
            {"name": "security-scan", "status": "completed", "conclusion": "success", "url": "https://ci.example.com/runs/9002"},
        ],
    }


def get_pr_check_status_empty(args: dict) -> dict:
    return {"reason": f"PR #{args['pr_number']} exists but no checks have been registered against it yet."}


def get_file_contents_success(args: dict) -> dict:
    content = f"# {args['path']}\n# canned stub content on branch {args['branch']}\n"
    return {
        "path": args["path"],
        "branch": args["branch"],
        "content": content,
        "encoding": "utf-8",
        "size": len(content.encode("utf-8")),
        "sha": "f1e2d3c4b5a697887766554433221100ffeedd0",
    }


def get_file_contents_empty(args: dict) -> dict:
    return {"reason": f"'{args['path']}' exists on branch '{args['branch']}' but is empty (zero bytes)."}


def list_files_success(args: dict) -> dict:
    prefix = args.get("path_prefix", "")
    return {
        "files": [
            {"path": f"{prefix}src/main.py".lstrip("/"), "type": "file", "size": 2048},
            {"path": f"{prefix}src/utils.py".lstrip("/"), "type": "file", "size": 512},
            {"path": f"{prefix}tests".lstrip("/"), "type": "directory", "size": 0},
        ],
        "next_cursor": None,
    }


def list_files_empty(args: dict) -> dict:
    return {"reason": f"No files matched path prefix '{args.get('path_prefix', '')}' on branch '{args['branch']}'."}


IMPLS = {
    "create-branch-worktree": ToolImpl(create_branch_worktree_success, create_branch_worktree_empty),
    "open-pr": ToolImpl(open_pr_success, open_pr_empty),
    "get-pr-check-status": ToolImpl(get_pr_check_status_success, get_pr_check_status_empty),
    "get-file-contents": ToolImpl(get_file_contents_success, get_file_contents_empty),
    "list-files": ToolImpl(list_files_success, list_files_empty),
}

server = build_server("source-control-stub", SCHEMA["version"], SCHEMA, IMPLS)

if __name__ == "__main__":
    anyio.run(run_stdio, server)
