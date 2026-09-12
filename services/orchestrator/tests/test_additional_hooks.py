"""Coverage for the two other Section 10.2 guardrail Hooks beyond
`DestructiveCommandHook`: scope-boundary blocking and secret redaction."""
from __future__ import annotations

from orchestrator.hooks import HookChain, HookChainBlockedError, ScopeBoundaryHook, SecretRedactionHook
import pytest


def test_scope_boundary_hook_blocks_a_write_to_a_forbidden_path():
    hook_chain = HookChain([ScopeBoundaryHook(forbidden_paths=frozenset({"src/secrets.py"}))])

    def write_file(args):
        return {"written": args["path"]}

    from orchestrator.skills import ToolInvoker

    invoker = ToolInvoker(hook_chain, tools={"write_file": write_file})
    with pytest.raises(HookChainBlockedError):
        invoker.call(tool_name="write_file", arguments={"path": "src/secrets.py"}, invoking_skill="s", stage="implementation", run_id="r1")

    # An in-scope path is unaffected.
    result = invoker.call(tool_name="write_file", arguments={"path": "src/foo.py"}, invoking_skill="s", stage="implementation", run_id="r1")
    assert result == {"written": "src/foo.py"}


def test_scope_boundary_hook_is_a_no_op_when_constructed_with_no_forbidden_paths():
    hook_chain = HookChain([ScopeBoundaryHook()])
    from orchestrator.skills import ToolInvoker

    invoker = ToolInvoker(hook_chain, tools={"write_file": lambda args: {"ok": True}})
    invoker.call(tool_name="write_file", arguments={"path": "anything.py"}, invoking_skill="s", stage="implementation", run_id="r1")


def test_secret_redaction_hook_redacts_credential_shaped_arguments():
    hook = SecretRedactionHook()
    redacted = hook.redact({"api_key": "sk-live-abc123", "username": "alice", "password": "hunter2"})
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["password"] == "***REDACTED***"
    assert redacted["username"] == "alice"
