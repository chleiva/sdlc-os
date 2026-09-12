"""Acceptance criterion: "A Hook that blocks a destructive command
(Section 10.2) fires regardless of which Skill attempted it."

Proof shape: one `ToolInvoker` (one `HookChain`, with `DestructiveCommandHook`
registered), two entirely independently-constructed `Skill` objects
(different names, different descriptions, different permitted-tool sets
-- nothing shared between them except the invoker), each attempting the
same destructive shell command through its own `call_tool`. Both must be
blocked, and the tool implementation itself (a real callable that would
have actually deleted something, replaced here with a list append so the
test can prove it never ran) must never execute for either.
"""
from __future__ import annotations

import pytest

from orchestrator.hooks import DestructiveCommandHook, HookChain, HookChainBlockedError
from orchestrator.skills import Skill, ToolInvoker


@pytest.fixture
def invoker():
    executed: list[dict] = []

    def shell_exec(arguments: dict) -> dict:
        executed.append(arguments)
        return {"outcome": "ok", "data": {"ran": True}}

    hook_chain = HookChain([DestructiveCommandHook()])
    inv = ToolInvoker(hook_chain, tools={"shell_exec": shell_exec})
    inv.executed = executed  # type: ignore[attr-defined]
    return inv


def test_destructive_command_blocked_regardless_of_which_skill_attempted_it(invoker):
    skill_a = Skill(name="release-cutter", description="cuts a release", invoker=invoker, permitted_tools=frozenset({"shell_exec"}))
    skill_b = Skill(name="db-migrator", description="runs a db migration", invoker=invoker, permitted_tools=frozenset({"shell_exec"}))

    with pytest.raises(HookChainBlockedError) as exc_a:
        skill_a.call_tool(tool_name="shell_exec", arguments={"command": "rm -rf /var/data"}, stage="implementation", run_id="run-1")
    assert "DestructiveCommandHook" == exc_a.value.hook_name

    with pytest.raises(HookChainBlockedError) as exc_b:
        skill_b.call_tool(tool_name="shell_exec", arguments={"command": "rm -rf /var/data"}, stage="implementation", run_id="run-1")
    assert "DestructiveCommandHook" == exc_b.value.hook_name

    # Neither Skill's attempt ever reached the real tool implementation.
    assert invoker.executed == []


def test_non_destructive_command_from_either_skill_is_allowed(invoker):
    skill_a = Skill(name="release-cutter", description="cuts a release", invoker=invoker, permitted_tools=frozenset({"shell_exec"}))
    skill_b = Skill(name="db-migrator", description="runs a db migration", invoker=invoker, permitted_tools=frozenset({"shell_exec"}))

    skill_a.call_tool(tool_name="shell_exec", arguments={"command": "ls -la"}, stage="implementation", run_id="run-1")
    skill_b.call_tool(tool_name="shell_exec", arguments={"command": "pytest -q"}, stage="implementation", run_id="run-1")

    assert len(invoker.executed) == 2


def test_force_push_blocked_via_declared_flag_not_string_matching(invoker):
    """Section 10.2 names force-push explicitly; this proves the Hook
    also blocks it via a structured flag argument, not only regex over a
    shell string -- the same guardrail catches a tool-call-shaped
    force-push (e.g. a real MCP `git_push` tool call), not only a raw
    shell command."""
    invoker.register_tool("git_push", lambda args: {"outcome": "ok"})
    skill = Skill(name="release-cutter", description="cuts a release", invoker=invoker, permitted_tools=frozenset({"git_push"}))
    with pytest.raises(HookChainBlockedError):
        skill.call_tool(tool_name="git_push", arguments={"branch": "main", "force": True}, stage="implementation", run_id="run-1")


def test_hook_audit_log_records_every_attempt_including_blocked_ones(invoker):
    skill = Skill(name="release-cutter", description="cuts a release", invoker=invoker, permitted_tools=frozenset({"shell_exec"}))
    with pytest.raises(HookChainBlockedError):
        skill.call_tool(tool_name="shell_exec", arguments={"command": "rm -rf /"}, stage="implementation", run_id="run-1")
    skill.call_tool(tool_name="shell_exec", arguments={"command": "ls"}, stage="implementation", run_id="run-1")

    records = invoker.hook_chain.audit_log
    assert len(records) == 2
    assert records[0].blocked_by == "DestructiveCommandHook"
    assert records[1].blocked_by is None
