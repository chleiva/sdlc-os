"""Contract tests for the Source-Control (GitHub App or equivalent) MCP
stub server.

Point D5's agent at this module as a template: spawn
`services/mcp-stubs/source-control/server.py`, call each tool with a
tenant_id from _common.scenarios, and assert the response matches this
server's own schema/source-control.schema.json for every named outcome.
"""
import pytest
from jsonschema import Draft202012Validator

from _common.scenarios import ALL_SCENARIO_TENANTS

from ._helpers import assert_matches_contract, call_tool, list_tools, schema_doc

SERVER = "source-control"
SCHEMA = schema_doc(SERVER)

BASE_ARGS = {
    "create-branch-worktree": {
        "repository": "acme/app",
        "base_ref": "main",
        "branch_name": "agent/run-42",
        "session_id": "session-42",
    },
    "open-pr": {
        "repository": "acme/app",
        "head_branch": "agent/run-42",
        "base_branch": "main",
        "title": "Enforce tenant_id on the Registry access API",
        "description": "Generated PR description tying back to the plan.",
    },
    "get-pr-check-status": {"repository": "acme/app", "pr_number": 4821},
    "get-file-contents": {"repository": "acme/app", "branch": "agent/run-42", "path": "src/api.py"},
    "list-files": {"repository": "acme/app", "branch": "agent/run-42"},
}


def test_schema_file_is_valid_2020_12():
    for tool_name, meta in SCHEMA["tools"].items():
        Draft202012Validator.check_schema(meta["input_schema"])
        Draft202012Validator.check_schema(meta["output_schema"])


def test_advertised_tools_match_brief():
    expected = {"create-branch-worktree", "open-pr", "get-pr-check-status", "get-file-contents", "list-files"}
    assert set(SCHEMA["tools"]) == expected

    tools = list_tools(SERVER)
    assert {t.name for t in tools.tools} == expected
    for t in tools.tools:
        assert t.input_schema == SCHEMA["tools"][t.name]["input_schema"]
        assert t.output_schema == SCHEMA["tools"][t.name]["output_schema"]


def test_every_tool_requires_tenant_id():
    for tool_name, meta in SCHEMA["tools"].items():
        assert "tenant_id" in meta["input_schema"]["required"], tool_name


@pytest.mark.parametrize("tool_name", list(BASE_ARGS))
@pytest.mark.parametrize("scenario,tenant_id", list(ALL_SCENARIO_TENANTS.items()))
def test_tool_scenarios(tool_name, scenario, tenant_id):
    args = {"tenant_id": tenant_id, **BASE_ARGS[tool_name]}
    result = call_tool(SERVER, tool_name, args)
    assert_matches_contract(result, SCHEMA["tools"][tool_name], scenario)
