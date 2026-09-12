#!/usr/bin/env python3
"""Stub/mock Issue-Tracker (Jira) MCP server (F3).

Runnable standalone, no real Jira behind it:

    python3 services/mcp-stubs/issue-tracker/server.py

See services/mcp-stubs/README.md for the tenant_id-driven scenario
convention shared by every stub server in this deliverable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anyio

from _common.schema_loader import load_schema
from _common.server_runtime import ToolImpl, build_server, run_stdio

SCHEMA_PATH = Path(__file__).parent / "schema" / "issue-tracker.schema.json"
SCHEMA = load_schema(SCHEMA_PATH)


def create_epic_success(args: dict) -> dict:
    project_key = args["project_key"]
    return {"issue_key": f"{project_key}-100", "url": f"https://jira.example.com/browse/{project_key}-100"}


def create_epic_empty(args: dict) -> dict:
    project_key = args["project_key"]
    return {
        "reason": f"An epic with summary '{args['summary']}' already exists in project {project_key}; no duplicate created.",
        "existing_issue_key": f"{project_key}-100",
    }


def create_story_success(args: dict) -> dict:
    project_key = args["project_key"]
    return {
        "issue_key": f"{project_key}-101",
        "url": f"https://jira.example.com/browse/{project_key}-101",
        "epic_key": args["epic_key"],
    }


def create_story_empty(args: dict) -> dict:
    project_key = args["project_key"]
    return {
        "reason": f"A story with summary '{args['summary']}' already exists under {args['epic_key']}; no duplicate created.",
        "existing_issue_key": f"{project_key}-101",
    }


def get_issue_success(args: dict) -> dict:
    return {
        "issue_key": args["issue_key"],
        "summary": "Add multi-tenant support to the job dispatcher",
        "description": "As a platform operator I want tenant_id enforced end-to-end so no run can cross a tenant boundary.",
        "status": "In Progress",
        "issue_type": "Story",
        "size": "M",
        "labels": ["ai-factory"],
        "links": [{"type": "is-blocked-by", "issue_key": "PROJ-42"}],
    }


def get_issue_empty(args: dict) -> dict:
    return {
        "reason": f"Issue '{args['issue_key']}' resolves, but has been archived; its content is no longer retrievable.",
    }


def transition_status_success(args: dict) -> dict:
    return {
        "issue_key": args["issue_key"],
        "previous_status": "Selected for Development",
        "new_status": args["target_status"],
        "transitioned_at": "2026-09-12T10:00:00Z",
    }


def transition_status_empty(args: dict) -> dict:
    return {
        "reason": f"Issue '{args['issue_key']}' is already in status '{args['target_status']}'; no transition performed.",
    }


def post_comment_success(args: dict) -> dict:
    return {"comment_id": "10042", "issue_key": args["issue_key"], "created_at": "2026-09-12T10:05:00Z"}


def post_comment_empty(args: dict) -> dict:
    return {"reason": "Comment body was empty after template rendering; nothing posted."}


IMPLS = {
    "create-epic": ToolImpl(create_epic_success, create_epic_empty),
    "create-story": ToolImpl(create_story_success, create_story_empty),
    "get-issue": ToolImpl(get_issue_success, get_issue_empty),
    "transition-status": ToolImpl(transition_status_success, transition_status_empty),
    "post-comment": ToolImpl(post_comment_success, post_comment_empty),
}

server = build_server("issue-tracker-stub", SCHEMA["version"], SCHEMA, IMPLS)

if __name__ == "__main__":
    anyio.run(run_stdio, server)
