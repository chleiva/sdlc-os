"""Proves the orchestrator's tool-call path against F3's *real* stub MCP
servers (real subprocesses speaking real MCP over stdio -- the same
`services/mcp-stubs/tests/_helpers.py` technique, not a hand-rolled
fake), wired through the same Hook chain every Skill/Subagent uses, for
at least the research stage's index/issue-tracker calls -- per the D2
brief's "build/test against F3's stubs first."
"""
from __future__ import annotations

import pytest

from orchestrator.hooks import DestructiveCommandHook, HookChain
from orchestrator.mcp_clients import index_client, issue_tracker_client, raise_on_error_outcome
from orchestrator.model_backend import ScriptedAgentBackend
from orchestrator.skills import ToolInvoker, spawn_reviewer_subagent

from ._factories import make_plan_output
from .conftest import make_orchestrator


def _build_research_invoker() -> ToolInvoker:
    idx = index_client()
    tracker = issue_tracker_client()

    def index_find_references(args: dict) -> dict:
        return raise_on_error_outcome(idx.call("find-references", args))

    def issue_get_issue(args: dict) -> dict:
        return raise_on_error_outcome(tracker.call("get-issue", args))

    hook_chain = HookChain([DestructiveCommandHook()])
    return ToolInvoker(hook_chain, tools={"index_find_references": index_find_references, "issue_get_issue": issue_get_issue})


def test_research_stage_calls_real_f3_stub_servers_through_the_hook_chain(registry, tenant_id, plan_store, progress_store):
    invoker = _build_research_invoker()
    research_calls: list[dict] = []

    def research_fn(orch, run, tool_invoker):
        result = tool_invoker.call(
            tool_name="issue_get_issue",
            arguments={"tenant_id": "tenant-acme", "issue_key": run.jira_key},
            invoking_skill="research-skill",
            stage=run.stage,
            run_id=run.id,
        )
        research_calls.append(result)
        refs = tool_invoker.call(
            tool_name="index_find_references",
            arguments={"tenant_id": "tenant-acme", "repository": run.repo, "symbol": "process_payment"},
            invoking_skill="research-skill",
            stage=run.stage,
            run_id=run.id,
        )
        research_calls.append(refs)

    backend = ScriptedAgentBackend(plans=[make_plan_output()])
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, tool_invoker=invoker, research_fn=research_fn,
    )
    status = orch.start_run(jira_key="PROJ-100", repo="acme/app", branch="feature/research", trace_id="trace-100")

    assert status.stage == "plan_approval_gate"
    assert len(research_calls) == 2
    assert research_calls[0]["outcome"] == "ok"
    assert research_calls[0]["data"]["issue_key"] == "PROJ-100"
    assert research_calls[1]["outcome"] == "ok"
    assert research_calls[1]["data"]["exhaustive"] is True  # F3's index server always flags this

    # Every one of those real MCP round-trips passed through the same
    # Hook chain (audit log proves it, and a blocking hook would have
    # fired against them exactly like it does in the pure-unit-test hook
    # chain suite).
    assert len(invoker.hook_chain.audit_log) == 2
    assert all(r.blocked_by is None for r in invoker.hook_chain.audit_log)


def test_reviewer_subagent_has_a_fresh_context_and_read_only_tools(registry):
    invoker = _build_research_invoker()

    reviewer = spawn_reviewer_subagent(invoker=invoker)
    assert reviewer.context == []
    # The reviewer's permitted tools are a strictly read-only set -- it
    # cannot, for instance, call a destructive/write-shaped tool even if
    # the invoker technically has one registered.
    invoker.register_tool("shell_exec", lambda args: {"ran": True})
    with pytest.raises(PermissionError):
        reviewer.call_tool(tool_name="shell_exec", arguments={"command": "ls"}, stage="verification", run_id="run-1")


def test_reviewer_subagent_never_shares_the_implementer_subagents_context():
    from orchestrator.skills import spawn_implementer_subagent

    invoker = _build_research_invoker()
    implementer = spawn_implementer_subagent(invoker=invoker, permitted_tools=frozenset({"index_find_references"}))
    implementer.add_context("implementer saw file src/foo.py and decided X")
    implementer.add_context("implementer assumed Y without checking")

    reviewer = spawn_reviewer_subagent(invoker=invoker)
    assert reviewer.context == []  # never inherited, not even after the implementer accumulated context

    # Mutating the implementer's context afterward must not leak into an
    # already-spawned reviewer either -- proving they are not the same
    # list object under the hood.
    implementer.add_context("implementer saw one more thing")
    assert reviewer.context == []
    assert implementer.context is not reviewer.context
