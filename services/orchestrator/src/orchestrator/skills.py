"""Skills and Subagents (master spec Section 7.1/7.2/8.1), built on top of
the Hook chain in `hooks.py`.

`ToolInvoker` is the single choke point every Skill and Subagent must use
to reach a tool -- it is the thing that actually owns the `HookChain` and
the map of tool_name -> implementation callable. A `Skill` or `Subagent`
never receives a raw MCP client or shell-exec function directly; it only
ever receives a `ToolInvoker`, so there is no path in this codebase from
"a Skill decided to do something" to "a tool actually ran" that skips the
Hook chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from orchestrator.hooks import HookChain, ToolCallContext


class ToolInvoker:
    """Owns the Hook chain and the registered tool implementations.
    Skills/Subagents call `invoker.call(...)`; nothing else in this
    package is a legal way to reach a tool implementation."""

    def __init__(self, hook_chain: HookChain, tools: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        self._hook_chain = hook_chain
        self._tools = dict(tools)

    def register_tool(self, name: str, fn: Callable[[dict[str, Any]], Any]) -> None:
        self._tools[name] = fn

    def call(self, *, tool_name: str, arguments: dict[str, Any], invoking_skill: str, stage: str, run_id: str) -> Any:
        if tool_name not in self._tools:
            raise KeyError(f"no tool implementation registered for '{tool_name}'")
        ctx = ToolCallContext(
            tool_name=tool_name, arguments=arguments, invoking_skill=invoking_skill, stage=stage, run_id=run_id
        )
        return self._hook_chain.dispatch(ctx, self._tools[tool_name])

    @property
    def hook_chain(self) -> HookChain:
        return self._hook_chain


@dataclass
class Skill:
    """A discoverable, reusable capability package (Section 7.1). Kept
    intentionally minimal here: a name/description pair (the SKILL.md
    convention's descriptor), a declared permitted tool set (narrower than
    the orchestrator's own), and a `run` callable that acts purely by
    calling `self.invoker.call(...)` -- never anything else."""

    name: str
    description: str
    invoker: ToolInvoker
    permitted_tools: frozenset[str]

    def call_tool(self, *, tool_name: str, arguments: dict[str, Any], stage: str, run_id: str) -> Any:
        if tool_name not in self.permitted_tools:
            raise PermissionError(f"Skill '{self.name}' is not permitted to call tool '{tool_name}'")
        return self.invoker.call(
            tool_name=tool_name, arguments=arguments, invoking_skill=self.name, stage=stage, run_id=run_id
        )


@dataclass
class Subagent:
    """An isolated-context child session (Section 7.1/8.2): its own
    `context` transcript, narrower `permitted_tools` than its parent, and
    -- the actual isolation mechanism -- constructed fresh, never sharing
    the parent's `context` list object. `spawn_reviewer_subagent` below is
    the concrete Section 8.1 "isolated-context review" case: the reviewer
    never receives the implementer's `context`."""

    name: str
    invoker: ToolInvoker
    permitted_tools: frozenset[str]
    context: list[str] = field(default_factory=list)

    def add_context(self, entry: str) -> None:
        self.context.append(entry)

    def call_tool(self, *, tool_name: str, arguments: dict[str, Any], stage: str, run_id: str) -> Any:
        if tool_name not in self.permitted_tools:
            raise PermissionError(f"Subagent '{self.name}' is not permitted to call tool '{tool_name}'")
        return self.invoker.call(
            tool_name=tool_name, arguments=arguments, invoking_skill=self.name, stage=stage, run_id=run_id
        )


READ_ONLY_REVIEW_TOOLS = frozenset(
    {"index_find_references", "index_find_definition", "index_search", "sc_get_file_contents", "sc_list_files"}
)


def spawn_implementer_subagent(*, invoker: ToolInvoker, permitted_tools: frozenset[str]) -> Subagent:
    return Subagent(name="implementer", invoker=invoker, permitted_tools=permitted_tools, context=[])


def spawn_reviewer_subagent(*, invoker: ToolInvoker, permitted_tools: frozenset[str] = READ_ONLY_REVIEW_TOOLS) -> Subagent:
    """Section 8.1 "isolated-context review": always starts from a brand
    new, empty `context` list -- the caller cannot accidentally pass the
    implementer's transcript in, because this function does not accept
    one. Tool permissions default to a read-only set, since a review pass
    diagnoses divergence, it does not write code (Section 8.4)."""
    return Subagent(name="reviewer", invoker=invoker, permitted_tools=permitted_tools, context=[])
