"""Contract tests for the Repository Index MCP stub server.

Point D3 (or any other deliverable's agent) at this module as a template:
spawn `services/mcp-stubs/index/server.py`, call each tool with a
tenant_id from _common.scenarios, and assert the response matches this
server's own schema/index.schema.json for every one of the six named
outcomes (ok, empty, and the four named error conditions).
"""
import pytest
from jsonschema import Draft202012Validator

from _common.scenarios import ALL_SCENARIO_TENANTS

from ._helpers import assert_matches_contract, call_tool, list_tools, schema_doc

SERVER = "index"
SCHEMA = schema_doc(SERVER)

BASE_ARGS = {
    "find-definition": {"repository": "acme/app", "symbol": "process_payment", "origin": {"file": "src/api.py", "line": 12}},
    "find-references": {"repository": "acme/app", "symbol": "process_payment"},
    "find-callers": {"repository": "acme/app", "symbol": "process_payment"},
    "search": {"query": "how are sessions authenticated"},
    "get-file-module-summary": {"repository": "acme/app", "path": "src/generated/schema.pb.go"},
    "get-ownership-metadata": {"repository": "acme/app", "path": "src/api.py"},
}


def test_schema_file_is_valid_2020_12():
    for tool_name, meta in SCHEMA["tools"].items():
        Draft202012Validator.check_schema(meta["input_schema"])
        Draft202012Validator.check_schema(meta["output_schema"])


def test_advertised_tools_match_brief():
    expected = {
        "find-definition",
        "find-references",
        "find-callers",
        "search",
        "get-file-module-summary",
        "get-ownership-metadata",
    }
    assert set(SCHEMA["tools"]) == expected

    tools = list_tools(SERVER)
    assert {t.name for t in tools.tools} == expected
    for t in tools.tools:
        # The running server must advertise exactly what the schema file
        # says -- no drift between the file and the live process.
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


def test_find_references_is_always_exhaustive_flagged():
    for scenario, tenant_id in [("ok", ALL_SCENARIO_TENANTS["ok"]), ("empty", ALL_SCENARIO_TENANTS["empty"])]:
        result = call_tool(SERVER, "find-references", {"tenant_id": tenant_id, **BASE_ARGS["find-references"]})
        payload = assert_matches_contract(result, SCHEMA["tools"]["find-references"], scenario)
        key = "data" if scenario == "ok" else None
        exhaustive = payload["data"]["exhaustive"] if scenario == "ok" else payload["exhaustive"]
        assert exhaustive is True


def test_search_is_always_labeled_non_authoritative():
    for scenario, tenant_id in [("ok", ALL_SCENARIO_TENANTS["ok"]), ("empty", ALL_SCENARIO_TENANTS["empty"])]:
        result = call_tool(SERVER, "search", {"tenant_id": tenant_id, **BASE_ARGS["search"]})
        payload = assert_matches_contract(result, SCHEMA["tools"]["search"], scenario)
        block = payload["data"] if scenario == "ok" else payload
        assert block["authoritative"] is False
        assert block["label"] == "non-authoritative"
