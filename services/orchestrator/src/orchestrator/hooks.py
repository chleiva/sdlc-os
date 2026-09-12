"""The Hook chain (master spec Section 7.1/7.2): a deterministic
interceptor bound to a lifecycle event, run as plain code, never as a
suggestion the model can choose to follow.

The actual enforcement mechanism is structural, not a convention: every
tool call in this package -- from the orchestrator itself, from a Skill,
or from a Subagent -- is required to go through `ToolInvoker.call(...)`.
`ToolInvoker` is the *only* thing that holds a reference to an
implementation function that actually does something (an MCP client call,
a sandboxed shell exec, ...); `Skill` and `Subagent` (see `skills.py`)
are constructed with a `ToolInvoker` and never with a raw client, so there
is no code path in this package that reaches a tool without passing
through the hook chain first. `tests/test_hook_chain_blocks_destructive_command.py`
proves this for two independently-defined Skills sharing one `ToolInvoker`.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable


class HookDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class ToolCallContext:
    tool_name: str
    arguments: dict[str, Any]
    invoking_skill: str
    stage: str
    run_id: str


@dataclass(frozen=True)
class HookResult:
    decision: HookDecision
    reason: str = ""


@dataclass(frozen=True)
class ToolCallRecord:
    """One structured, auditable record of a tool-call attempt -- Section
    10.1's "audit logging ... not only on the agent's self-reported
    actions" applied at the hook-chain layer."""

    ctx: ToolCallContext
    blocked_by: str | None  # hook class name that blocked it, or None
    reason: str | None
    result: Any | None
    error: str | None = None


class Hook(ABC):
    """A Hook implements whichever lifecycle methods it cares about; the
    no-op defaults let a Hook implement only PreToolUse, only PostToolUse,
    etc."""

    def pre_tool_use(self, ctx: ToolCallContext) -> HookResult:
        return HookResult(HookDecision.ALLOW)

    def post_tool_use(self, ctx: ToolCallContext, result: Any) -> None:
        return None

    def on_stop(self, ctx: ToolCallContext | None) -> None:
        return None


class HookChainBlockedError(Exception):
    def __init__(self, *, hook_name: str, reason: str, ctx: ToolCallContext):
        super().__init__(f"{hook_name} blocked tool call '{ctx.tool_name}': {reason}")
        self.hook_name = hook_name
        self.reason = reason
        self.ctx = ctx


class HookChain:
    """Runs every registered Hook's PreToolUse before the tool executes,
    and every Hook's PostToolUse after -- in registration order, first
    BLOCK wins (later hooks are not even consulted, mirroring a real
    guardrail short-circuit)."""

    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self._hooks: list[Hook] = list(hooks or [])
        self.audit_log: list[ToolCallRecord] = []

    def register(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def _redacted_ctx(self, ctx: ToolCallContext) -> ToolCallContext:
        """Apply any registered `SecretRedactionHook`'s `.redact()` to a
        copy of `ctx` for audit-log storage ONLY -- `tool_fn` above still
        receives the real, unredacted `ctx.arguments` (secrets must still
        reach the point of use), only the object appended to
        `self.audit_log` is redacted.

        SECURITY FIX (D10 security-hardening pass, real finding):
        `SecretRedactionHook` was defined with a working `.redact()`
        method and its own docstring claimed it "redacts any argument
        value ... before it is recorded in the audit log" (Section 10.2 /
        17.2: secrets "redacted everywhere [outside point of use]"), but
        `dispatch()` never actually called it -- registering the hook had
        zero effect, and a secret-shaped tool argument (e.g. an api_key/
        token/password passed to a real tool call) was recorded verbatim
        in `audit_log`, the one place explicitly meant to be safe to
        inspect/export. Fixed by invoking `.redact()` here, at record-
        build time, for every branch that appends to `audit_log`.
        """
        redacted_args = ctx.arguments
        for hook in self._hooks:
            if isinstance(hook, SecretRedactionHook):
                redacted_args = hook.redact(redacted_args)
        if redacted_args is ctx.arguments:
            return ctx
        return replace(ctx, arguments=redacted_args)

    def dispatch(self, ctx: ToolCallContext, tool_fn: Callable[[dict[str, Any]], Any]) -> Any:
        for hook in self._hooks:
            pre = hook.pre_tool_use(ctx)
            if pre.decision is HookDecision.BLOCK:
                record = ToolCallRecord(
                    ctx=self._redacted_ctx(ctx), blocked_by=type(hook).__name__, reason=pre.reason, result=None
                )
                self.audit_log.append(record)
                raise HookChainBlockedError(hook_name=type(hook).__name__, reason=pre.reason, ctx=ctx)

        try:
            result = tool_fn(ctx.arguments)
        except Exception as exc:
            record = ToolCallRecord(ctx=self._redacted_ctx(ctx), blocked_by=None, reason=None, result=None, error=str(exc))
            self.audit_log.append(record)
            raise

        for hook in self._hooks:
            hook.post_tool_use(ctx, result)

        record = ToolCallRecord(ctx=self._redacted_ctx(ctx), blocked_by=None, reason=None, result=result)
        self.audit_log.append(record)
        return result

    def on_stop(self, ctx: ToolCallContext | None = None) -> None:
        for hook in self._hooks:
            hook.on_stop(ctx)


# ---------------------------------------------------------------------
# A concrete guardrail Hook: Section 10.2's destructive-command block.
# ---------------------------------------------------------------------

_DESTRUCTIVE_SHELL_PATTERNS = (
    "rm -rf",
    "rm -r -f",
    "git push --force",
    "git push -f",
    "git reset --hard",
    "drop table",
    "drop database",
)

_DESTRUCTIVE_TOOL_FLAGS = {
    # tool_name -> argument keys whose truthy value marks the call destructive
    "git_push": ("force",),
}


class DestructiveCommandHook(Hook):
    """Section 10.2: "Destructive commands (force-push, history rewrite,
    dropping databases/tables, deleting files outside the task's declared
    scope, rm -rf-class operations) require an explicit human-approved
    escalation ... blocked at the Hook layer, not merely by instruction."

    Blocks unconditionally (Phase-0 default: no escalation path wired up
    in this deliverable -- D9 owns the human-escalation UX per the D2
    brief's non-goals) any tool call whose shell-style `command` argument
    matches a known destructive pattern, or whose declared destructive
    flag (e.g. `force` on a push tool) is set.
    """

    def pre_tool_use(self, ctx: ToolCallContext) -> HookResult:
        command = str(ctx.arguments.get("command", "")).lower()
        for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
            if pattern in command:
                return HookResult(
                    HookDecision.BLOCK,
                    reason=f"destructive command pattern {pattern!r} requires human-approved escalation (Section 10.2)",
                )
        for flag in _DESTRUCTIVE_TOOL_FLAGS.get(ctx.tool_name, ()):
            if ctx.arguments.get(flag):
                return HookResult(
                    HookDecision.BLOCK,
                    reason=f"tool '{ctx.tool_name}' invoked with destructive flag {flag!r} (Section 10.2)",
                )
        return HookResult(HookDecision.ALLOW)


class ScopeBoundaryHook(Hook):
    """Blocks a write-shaped tool call (one that carries a `path` or
    `files` argument this run intends to *modify*) when the target falls
    outside an explicitly out-of-scope list -- a second, structural line
    of defense alongside the Section 9.3 risk *checkpoint* (which pauses
    for a human decision rather than blocking outright). Only active when
    constructed with a non-empty `forbidden_paths` set; otherwise a no-op.
    """

    def __init__(self, forbidden_paths: frozenset[str] = frozenset()) -> None:
        self._forbidden = forbidden_paths

    def pre_tool_use(self, ctx: ToolCallContext) -> HookResult:
        path = ctx.arguments.get("path")
        if path and path in self._forbidden:
            return HookResult(HookDecision.BLOCK, reason=f"'{path}' is explicitly out of scope for this run")
        return HookResult(HookDecision.ALLOW)


class SecretRedactionHook(Hook):
    """Section 10.2 / Section 17: secrets are never placed into agent
    context, prompts, logs, or commit history -- redacted everywhere,
    enforced by the same Hook chain. This Hook redacts any argument value
    whose key looks credential-shaped before it is recorded in the audit
    log (it does not alter what's actually sent to the tool -- redaction
    is for anything downstream that reads the audit trail/logs)."""

    _SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "credential")

    def post_tool_use(self, ctx: ToolCallContext, result: Any) -> None:
        return None

    def redact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for k, v in arguments.items():
            if any(marker in k.lower() for marker in self._SECRET_KEY_MARKERS):
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = v
        return redacted
