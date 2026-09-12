"""Contract tests for the CI MCP stub server.

Point D7's agent at this module as a template: spawn
`services/mcp-stubs/ci/server.py`, call each tool with a tenant_id from
_common.scenarios, and assert the response matches this server's own
schema/ci.schema.json for every named outcome.
"""
import pytest
from jsonschema import Draft202012Validator

from _common.scenarios import ALL_SCENARIO_TENANTS

from ._helpers import assert_matches_contract, call_tool, list_tools, schema_doc

SERVER = "ci"
SCHEMA = schema_doc(SERVER)

BASE_ARGS = {
    "trigger-run": {"repository": "acme/app", "ref": "agent/run-42", "scope": ["svc-payments"]},
    "get-run-status-result": {"repository": "acme/app", "run_id": "run-70042"},
}


def test_schema_file_is_valid_2020_12():
    for tool_name, meta in SCHEMA["tools"].items():
        Draft202012Validator.check_schema(meta["input_schema"])
        Draft202012Validator.check_schema(meta["output_schema"])


def test_advertised_tools_match_brief():
    expected = {"trigger-run", "get-run-status-result"}
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
