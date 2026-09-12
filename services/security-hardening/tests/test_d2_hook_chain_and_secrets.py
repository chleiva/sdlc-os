"""D10 adversarial pass on D2 (orchestrator) Hook chain + secret handling.

Three things, against the REAL orchestrator code (not a mock):

1. Confirm `DestructiveCommandHook` blocks a destructive command from
   every *intended* tool-invocation entry point (Skill, Subagent, raw
   ToolInvoker, and the RESEARCH/PACKAGING stages of the real state
   machine) -- not just the two Skills the existing test already covers.
2. Honestly demonstrate the REAL bypass paths that exist alongside the
   Hook chain in this same package (`sandbox.py`, `worktree.py`,
   `mcp_clients.py`) -- these are not something D10 can "fix" without a
   large refactor of D2 (the brief says: smallest fix that closes a
   *found* gap, no large refactors of another deliverable), so this is
   documented here as a structural finding, not silently swept under a
   passing assertion. See the final report for why this is flagged
   rather than patched.
3. Prove the secret-redaction gap this pass FOUND and FIXED
   (`orchestrator/hooks.py::HookChain._redacted_ctx`, wired into
   `dispatch()`): a secret-shaped tool argument must never appear
   unredacted in `HookChain.audit_log` -- the one place explicitly
   documented as safe to inspect/export -- while still reaching the
   tool implementation itself unredacted (secrets are injected only at
   the point of use, Sec. 10.2).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.hooks import (
    DestructiveCommandHook,
    HookChain,
    HookChainBlockedError,
    SecretRedactionHook,
)
from orchestrator.skills import (
    ToolInvoker,
    Skill,
    Subagent,
    spawn_implementer_subagent,
    spawn_reviewer_subagent,
)


def _invoker():
    executed = []

    def shell_exec(args):
        executed.append(args)
        return {"outcome": "ok"}

    hook_chain = HookChain([DestructiveCommandHook()])
    invoker = ToolInvoker(hook_chain, tools={"shell_exec": shell_exec, "git_push": lambda a: {"outcome": "ok"}})
    return invoker, executed


DESTRUCTIVE_ARGS = {"command": "rm -rf /var/data"}


# ---------------------------------------------------------------------
# 1. Blocked from every legitimate entry point sharing one ToolInvoker.
# ---------------------------------------------------------------------
def test_blocked_via_skill_call_tool():
    invoker, executed = _invoker()
    skill = Skill(name="s1", description="d", invoker=invoker, permitted_tools=frozenset({"shell_exec"}))
    with pytest.raises(HookChainBlockedError):
        skill.call_tool(tool_name="shell_exec", arguments=DESTRUCTIVE_ARGS, stage="implementation", run_id="r1")
    assert executed == []


def test_blocked_via_implementer_subagent():
    invoker, executed = _invoker()
    sub = spawn_implementer_subagent(invoker=invoker, permitted_tools=frozenset({"shell_exec"}))
    with pytest.raises(HookChainBlockedError):
        sub.call_tool(tool_name="shell_exec", arguments=DESTRUCTIVE_ARGS, stage="implementation", run_id="r1")
    assert executed == []


def test_blocked_via_reviewer_subagent_even_though_read_only_by_default():
    invoker, executed = _invoker()
    # Reviewer subagent is granted an explicit (non-default) write tool
    # here to prove the Hook chain -- not just the permitted_tools
    # narrowing -- is what blocks the destructive command.
    sub = spawn_reviewer_subagent(invoker=invoker, permitted_tools=frozenset({"shell_exec"}))
    with pytest.raises(HookChainBlockedError):
        sub.call_tool(tool_name="shell_exec", arguments=DESTRUCTIVE_ARGS, stage="implementation", run_id="r1")
    assert executed == []


def test_blocked_via_raw_tool_invoker_no_skill_wrapper():
    """This is the exact shape `research_fn`/`packaging_fn` callbacks are
    handed in `Orchestrator._drive()` (core.py) -- a raw ToolInvoker, not
    wrapped in a Skill/Subagent's permitted_tools narrowing."""
    invoker, executed = _invoker()
    with pytest.raises(HookChainBlockedError):
        invoker.call(tool_name="shell_exec", arguments=DESTRUCTIVE_ARGS, invoking_skill="research_fn", stage="research", run_id="r1")
    assert executed == []


def test_blocked_via_structured_force_flag_not_just_string_matching():
    invoker, executed = _invoker()
    skill = Skill(name="s1", description="d", invoker=invoker, permitted_tools=frozenset({"git_push"}))
    with pytest.raises(HookChainBlockedError):
        skill.call_tool(tool_name="git_push", arguments={"branch": "main", "force": True}, stage="implementation", run_id="r1")


def test_blocked_end_to_end_through_the_real_state_machine_research_stage(tmp_path):
    """Drives the destructive attempt through `Orchestrator._drive()`'s
    real RESEARCH-stage wiring (not just the Hook chain in isolation),
    proving the state machine itself never lets a research_fn's tool
    call skip the chain."""
    from run_registry import RegistryService
    from orchestrator.core import Orchestrator
    from orchestrator.model_backend import ScriptedAgentBackend
    from orchestrator.plan_artifact import PlanArtifactStore
    from orchestrator.progress import RunProgressStore
    from orchestrator.verification import VerificationRunner

    registry = RegistryService(str(tmp_path / "registry.db"))
    tenant_id = "tenant-adversarial"
    run_result = registry.create_run(
        tenant_id=tenant_id, jira_key="SEC-1", repo="org/repo", branch="b", capacity_class="on_demand", trace_id="t1"
    )
    assert run_result.is_ok
    run_id = run_result.data.id

    invoker, executed = _invoker()

    def malicious_research_fn(orch, run, tool_invoker):
        # Simulates a compromised/malicious research step trying to run
        # a destructive command directly through the real ToolInvoker
        # the orchestrator handed it.
        tool_invoker.call(
            tool_name="shell_exec", arguments=DESTRUCTIVE_ARGS, invoking_skill="research_fn", stage=run.stage, run_id=run.id
        )

    class DummyVerifier(VerificationRunner):
        def run(self, *, run_context):
            raise AssertionError("must not be reached: blocked before verification")

    orch = Orchestrator(
        registry=registry,
        tenant_id=tenant_id,
        agent_backend=ScriptedAgentBackend(),
        verification_runner=DummyVerifier(),
        plan_store=PlanArtifactStore(tmp_path / "plans"),
        progress_store=RunProgressStore(tmp_path / "progress"),
        tool_invoker=invoker,
        research_fn=malicious_research_fn,
    )
    with pytest.raises(HookChainBlockedError):
        orch.resume_run(run_id)
    assert executed == [], "the destructive command must never have actually executed"
    registry.close()


# ---------------------------------------------------------------------
# 2. Honest documentation of real bypass paths that exist in this
#    package OUTSIDE the ToolInvoker/HookChain seam. These are NOT
#    patched here (D10's brief: smallest fix for a found gap, never a
#    large refactor of another deliverable's execution model) -- they
#    are asserted explicitly so the gap is visible and tracked, not
#    silently assumed away. See final report.
# ---------------------------------------------------------------------
def test_sandbox_execute_bypasses_the_hook_chain_entirely(tmp_path):
    """orchestrator.sandbox.SandboxRuntime.execute() runs subprocess.run
    directly and has no import of/reference to orchestrator.hooks at
    all -- a destructive command issued through this path is not
    inspected by DestructiveCommandHook. This test proves the bypass
    exists (a real structural finding), it does not endorse it."""
    from orchestrator.sandbox import SubprocessSandboxRuntime

    target = tmp_path / "will_be_deleted"
    target.mkdir()
    (target / "file.txt").write_text("x")

    runtime = SubprocessSandboxRuntime()
    result = runtime.execute(["rm", "-rf", str(target)])
    # The command actually ran -- proving it was NOT intercepted by any
    # Hook. (If a future patch adds hook-awareness to SandboxRuntime,
    # this assertion should be revisited; today it accurately reflects
    # the code as it stands.)
    assert not target.exists(), "sandbox.execute ran the destructive command with no Hook interception"
    assert result is not None


def test_worktree_remove_force_bypasses_the_hook_chain_entirely(tmp_path):
    """orchestrator.worktree's git subprocess helpers have no
    Hook-chain awareness either -- `git worktree remove --force` runs
    unconditionally if called directly, outside a ToolInvoker."""
    import subprocess as sp

    from orchestrator import worktree

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    sp.run(["git", "add", "."], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    sp.run(["git", "branch", "-M", "main"], cwd=repo, check=True)

    worktrees_root = repo / ".worktrees"
    created = worktree.create_agent_worktree(
        repo_path=repo, base_ref="main", session_id="s1", worktrees_root=worktrees_root
    )
    worktree_path = Path(created.worktree_path)
    assert worktree_path.exists()
    # Directly calling the force-remove helper -- no ToolInvoker, no
    # HookChain, no DestructiveCommandHook consulted at all.
    worktree.remove_agent_worktree(repo_path=repo, worktree_path=worktree_path, force=True)
    assert not worktree_path.exists(), "force-remove ran with no Hook interception (documented bypass)"


# ---------------------------------------------------------------------
# 3. The secret-redaction FIX this pass made: prove it actually works,
#    end to end, through the real dispatch() path.
# ---------------------------------------------------------------------
def test_secret_argument_is_redacted_in_audit_log_but_not_at_point_of_use():
    seen_by_tool = []

    def push_with_token(args):
        # The tool itself must still receive the REAL secret -- Sec.
        # 10.2: "injected only at the point of use".
        seen_by_tool.append(args)
        return {"outcome": "ok"}

    hook_chain = HookChain([SecretRedactionHook()])
    invoker = ToolInvoker(hook_chain, tools={"push": push_with_token})

    real_secret = "sk-live-FAKE-SECRET-abc123"
    invoker.call(
        tool_name="push",
        arguments={"api_key": real_secret, "branch": "main"},
        invoking_skill="s",
        stage="implementation",
        run_id="r1",
    )

    # Point of use: the real tool implementation saw the real secret.
    assert seen_by_tool == [{"api_key": real_secret, "branch": "main"}]

    # Audit trail: the same secret must NEVER appear, in any record.
    for record in hook_chain.audit_log:
        assert real_secret not in str(record.ctx.arguments.values())
        assert real_secret not in repr(record)
    assert hook_chain.audit_log[0].ctx.arguments["api_key"] == "***REDACTED***"
    assert hook_chain.audit_log[0].ctx.arguments["branch"] == "main"  # non-secret fields untouched


def test_secret_argument_is_redacted_even_on_a_blocked_call():
    """A destructive command that also happens to carry a secret-shaped
    argument must still be redacted in the (blocked) audit record."""
    hook_chain = HookChain([DestructiveCommandHook(), SecretRedactionHook()])
    invoker = ToolInvoker(hook_chain, tools={"shell_exec": lambda a: {"outcome": "ok"}})

    real_secret = "hunter2-fake-password"
    with pytest.raises(HookChainBlockedError):
        invoker.call(
            tool_name="shell_exec",
            arguments={"command": "rm -rf /data", "password": real_secret},
            invoking_skill="s",
            stage="implementation",
            run_id="r1",
        )
    assert len(hook_chain.audit_log) == 1
    record = hook_chain.audit_log[0]
    assert record.blocked_by == "DestructiveCommandHook"
    assert record.ctx.arguments["password"] == "***REDACTED***"
    assert real_secret not in str(record.ctx.arguments)


def test_secret_argument_is_redacted_on_a_successful_call_with_no_redaction_hook_still_behaves(caplog=None):
    """Regression guard: a HookChain with NO SecretRedactionHook
    registered must behave exactly as before (no crash, arguments
    untouched) -- the fix must be additive, never required."""
    hook_chain = HookChain([DestructiveCommandHook()])  # no SecretRedactionHook
    invoker = ToolInvoker(hook_chain, tools={"push": lambda a: {"outcome": "ok"}})
    invoker.call(
        tool_name="push", arguments={"api_key": "plain-value"}, invoking_skill="s", stage="implementation", run_id="r1"
    )
    assert hook_chain.audit_log[0].ctx.arguments["api_key"] == "plain-value"
