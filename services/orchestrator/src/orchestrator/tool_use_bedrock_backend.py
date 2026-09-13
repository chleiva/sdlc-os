"""`BedrockToolUseAgentBackend` -- the real implementation-stage fix.

**The gap this closes.** Every `AgentBackend` implementation built so far
(`ollama_backend.py`, `anthropic_backend.py`, `openai_backend.py`,
`bedrock_backend.py`) implements `implement_subtask` as a single
structured-output call: the model is asked to *describe* a diff
(`files_touched`/`lines_changed`/`commit_message`), and nothing anywhere
takes that description and writes real file content to a real
repository -- `core.py`'s `_implementation_step` only ever records the
self-reported metadata as progress bookkeeping. That was fine as far as
it went (every deliverable's own brief scoped "real model call" and
"real file write" as two different, explicitly-separated concerns), but
it means no `AgentBackend` built before this one can actually produce a
real code change.

This class is the missing half: a real, multi-turn tool-calling loop
against Amazon Bedrock's Converse API (the same API `BedrockAgentBackend`
already uses for structured plan/diff output, but here with
`toolChoice: {"auto": {}}` and four real tools instead of one forced
tool), operating against a real workspace directory on disk -- normally
the real git worktree `source_control.SourceControlService.
create_branch_worktree` already creates. The model reads/writes/lists
real files there for real, turn by turn, until it calls `finish`; this
class then computes the returned `DiffOutput` from a REAL `git diff`
against that worktree, never from the model's own claim about what it
did -- so `progress.files_touched`/`lines_changed` (Section 9.3's risk
checkpoint diffs against exactly this) reflect what genuinely changed on
disk, not a self-report `core.py` has no way to verify.

**Drop-in, zero changes to `core.py`.** `implement_subtask`'s contract
(`SubTask -> DiffOutput`) is unchanged; only what happens *inside* this
implementation is real now. `author_plan`/`re_plan` are unchanged too --
delegated to a plain `BedrockAgentBackend` instance (composition, not
reimplementation), since planning has no reason to need file I/O.

**What this does NOT do (stated plainly, matching this repo's own
established discipline)**: it does not run tests/lint/security-scan
during implementation (that is D7's `VerificationRunner`'s job, called
by `core.py` at the next stage -- see `real_verification_runner.py`); it
does not execute arbitrary shell commands (no `run_command` tool is
offered -- only read/write/list, deliberately, to keep this first real
pass's blast radius to "file contents in the worktree", not "arbitrary
code execution" -- a real `run_command` tool routed through the already-
real `docker_sandbox.DockerContainerSandboxRuntime` is a natural, scoped
follow-up, not built here); and it is Bedrock-specific -- the equivalent
extension for Anthropic/OpenAI/Ollama is real, valuable follow-up work,
not done in this pass (those vendors' native tool-calling APIs are
directly analogous; this class's loop-and-tool-execution logic is not
Bedrock-specific in shape, only `_converse_with_tools`'s request/response
translation is).

**Real live-run bug found and fixed during this pass's third live run**:
the deliberate absence of a `run_command`/test-running tool (above) has
a real consequence a live run actually hit -- a plan authored a subtask
literally titled "Run tests to verify greet function works correctly".
This agent has no way to fulfill that (only read/write/list files), so
every one of its 20 tool-calling turns went by unable to make progress
and it never called `finish`, raising
`BedrockAgenticLoopExhaustedError`. The fix is not a new tool here (that
would duplicate D7's real verification pipeline inside the
implementation loop); it's upstream, in the shared plan-authoring schema
and every vendor backend's plan system prompt (`ollama_backend
._SUBTASK_SCHEMA`'s `description` field, `bedrock_backend
._PLAN_SYSTEM_PROMPT`, `openai_backend._PLAN_SYSTEM_PROMPT`,
`anthropic_backend._SUBTASK_CONSTRAINT`): a subtask must be a concrete
code-authoring action, never a "run/verify tests" step, since
verification already happens automatically, for real, in the separate
stage `core.py` runs next.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from orchestrator.bedrock_backend import (
    BedrockAccessDeniedError,
    BedrockAgentBackend,
    BedrockBackendConfig,
    BedrockInvocationError,
    BedrockMalformedOutputError,
    BedrockThrottledError,
)
from orchestrator.checkpoints import DEFAULT_BUDGETS
from orchestrator.model_backend import AgentBackend, DiffOutput, PlanOutput, SubTask

_READ_FILE_TOOL = "read_file"
_WRITE_FILE_TOOL = "write_file"
_LIST_FILES_TOOL = "list_files"
_FINISH_TOOL = "finish"

_TOOL_SPECS = [
    {
        "toolSpec": {
            "name": _READ_FILE_TOOL,
            "description": "Read the current contents of a file in the workspace. Returns an error if the file does not exist.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Path relative to the workspace root."}},
                    "required": ["path"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": _WRITE_FILE_TOOL,
            "description": "Create or overwrite a file in the workspace with the given full content. Parent directories are created automatically.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to the workspace root."},
                        "content": {"type": "string", "description": "The complete new content of the file."},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": _LIST_FILES_TOOL,
            "description": "List files and directories under a path in the workspace (non-recursive).",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Path relative to the workspace root; \".\" for the workspace root itself."}},
                    "required": ["path"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": _FINISH_TOOL,
            "description": "Call this exactly once, when (and only when) the subtask is fully implemented, to end the session and report a commit message.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "commit_message": {"type": "string", "description": "A real, conventional-commit-style message describing the change actually made."},
                        "summary": {"type": "string", "description": "A short human-readable summary of what was implemented."},
                    },
                    "required": ["commit_message", "summary"],
                    "additionalProperties": False,
                }
            },
        }
    },
]

_SYSTEM_PROMPT = (
    "You are the implementation stage of an autonomous coding agent. You "
    "have real read_file/write_file/list_files tools against a real git "
    "worktree -- use them to actually implement the subtask described "
    "below. Work in small, real steps: list files to orient yourself if "
    "needed, read a file before overwriting it if it might already have "
    "relevant content, write complete file contents (not a diff/patch "
    "fragment) with write_file. When the subtask is genuinely done, call "
    "finish exactly once with a real commit message -- never before the "
    "actual file changes are made, and never more than once.\n\n"
    "You have no tool to execute code, run a test suite, or run any "
    "shell command -- only read_file/write_file/list_files/finish. Never "
    "create a script or helper file whose purpose is to run or verify "
    "tests (e.g. a 'run_tests.sh'/'test_runner.py'-style file); doing so "
    "achieves nothing (you cannot execute it either) and, if its name "
    "matches pytest's own test-discovery pattern, actively breaks the "
    "real automated test run that happens after you finish. If you are "
    "asked to fix a previously-reported verification failure, edit "
    "exactly the file(s) the failure names to fix the real defect -- "
    "never respond by adding test-execution tooling."
)


class BedrockAgenticLoopExhaustedError(Exception):
    """The model never called `finish` within `max_turns` -- treated as a
    real failure, not silently accepted as 'probably done'."""


def _safe_join(root: Path, relative: str) -> Path:
    """Same containment discipline as `index_server.service._safe_join`
    (this repo's established pattern for "a caller-supplied relative path
    must not escape a root directory") -- resolves `relative` against
    `root` and rejects anything that would land outside it (a `../../etc/passwd`-
    style escape attempt), rather than trusting the model's path input."""
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"path {relative!r} escapes the workspace root {root_resolved} -- refused")
    return candidate


#: Explicit, stated engineering assumption converting a real minutes
#: budget into a turn count -- NOT a measured constant, but a documented
#: one, which is the actual fix here: previously this cap (20, then 40)
#: was a flat guess with no real anchor at all. Real per-turn latency
#: for a Bedrock Converse call grows with conversation history (the
#: whole transcript is resent every turn), so this is deliberately a
#: conservative average across a subtask's full turn budget, not a
#: best-case number.
_ASSUMED_SECONDS_PER_TURN = 15.0

#: What fraction of a *story's* whole wall-clock budget (Sec. 9.4) one
#: *subtask* may spend inside a single `implement_subtask` call. A story
#: normally decomposes into several subtasks plus verification/retry
#: time, so letting one subtask claim the entire budget would starve
#: everything after it; 1/3 leaves headroom for at least two more
#: subtasks or retries within the same story budget.
_SUBTASK_BUDGET_FRACTION = 1.0 / 3.0

#: Used only when `story_size` is missing/unrecognized (e.g. an older
#: `run_context` without it, or "XL" -- Sec. 9.4: not run directly, so
#: DEFAULT_BUDGETS["XL"] is None) -- same order of magnitude as "S"'s
#: derived value below, not a new independent guess.
_FALLBACK_MAX_TURNS = 40


def _max_turns_for_story_size(story_size: str | None) -> int:
    """Derive the per-subtask tool-calling turn cap from the plan's own
    declared Sec. 9.4 budget for its story size, instead of one flat
    constant applied to every size alike. Real live-run finding this
    replaces: turn caps used to be picked as a "reasonable-sounding"
    integer (20, then 40) with no real justification for that specific
    number -- an "S" one-file change and an "L" multi-file change got
    the identical allowance. This ties the cap to the one number in this
    codebase that already reflects real, agreed-upon per-size resource
    limits (`checkpoints.DEFAULT_BUDGETS`'s wall_clock_minutes), via the
    two explicit assumptions above (`_ASSUMED_SECONDS_PER_TURN`,
    `_SUBTASK_BUDGET_FRACTION`) -- still a heuristic (real per-turn
    latency is genuinely variable), but now an anchored, size-proportional
    one rather than an arbitrary guess repeated for every size."""
    budget = DEFAULT_BUDGETS.get(story_size or "") if story_size else None
    if budget is None:
        return _FALLBACK_MAX_TURNS
    subtask_seconds = budget.wall_clock_minutes * 60 * _SUBTASK_BUDGET_FRACTION
    return max(1, int(subtask_seconds // _ASSUMED_SECONDS_PER_TURN))


class BedrockToolUseAgentBackend(AgentBackend):
    """Real `AgentBackend` for Bedrock/MiniMax M2.5 with a genuine
    multi-turn, tool-using implementation stage. See module docstring."""

    def __init__(
        self,
        config: BedrockBackendConfig,
        client: Any,
        *,
        workspace_root: Path,
        # Explicit override (e.g. BEDROCK_MAX_TURNS in live_run.py) --
        # takes precedence over the size-derived value below when set.
        # None (the default) means "derive it per-call from run_context
        # ['story_size']" -- see `_max_turns_for_story_size`.
        max_turns: int | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._workspace_root = Path(workspace_root)
        self._max_turns_override = max_turns
        # Planning delegates to the existing, already-real structured-output
        # backend -- composition, not reimplementation (see module docstring).
        self._plan_backend = BedrockAgentBackend(config, client)

    # -- AgentBackend interface -------------------------------------------------

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        return self._plan_backend.author_plan(run_context=run_context)

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        return self._plan_backend.re_plan(run_context=run_context, feedback=feedback)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        user_prompt = (
            "Implement the following subtask for real, using the tools "
            "provided.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            "subtask:\n"
            f"{json.dumps({'task_id': subtask.task_id, 'description': subtask.description, 'interface_contract': subtask.interface_contract}, indent=2)}"
        )
        messages: list[dict] = [{"role": "user", "content": [{"text": user_prompt}]}]

        max_turns = self._max_turns_override or _max_turns_for_story_size(run_context.get("story_size"))

        commit_message = ""
        for _turn in range(max_turns):
            response = self._converse_with_tools(messages)
            output_message = response["output"]["message"]
            messages.append(output_message)

            tool_uses = [b["toolUse"] for b in output_message.get("content", []) if isinstance(b, dict) and "toolUse" in b]
            if not tool_uses:
                # The model replied with plain text instead of a tool call
                # -- nudge it back toward using a tool rather than silently
                # treating prose as completion.
                messages.append(
                    {
                        "role": "user",
                        "content": [{"text": "Please continue by calling one of the provided tools (read_file/write_file/list_files/finish)."}],
                    }
                )
                continue

            tool_result_blocks = []
            finished = False
            for tool_use in tool_uses:
                name = tool_use.get("name")
                tool_input = tool_use.get("input") or {}
                tool_use_id = tool_use.get("toolUseId")
                if name == _FINISH_TOOL:
                    commit_message = tool_input.get("commit_message") or f"Implement {subtask.task_id}"
                    finished = True
                    tool_result_blocks.append(self._tool_result(tool_use_id, {"acknowledged": True}))
                    continue
                result_payload = self._execute_tool(name, tool_input)
                tool_result_blocks.append(self._tool_result(tool_use_id, result_payload))

            if finished:
                break

            messages.append({"role": "user", "content": tool_result_blocks})
        else:
            raise BedrockAgenticLoopExhaustedError(
                f"subtask {subtask.task_id!r} did not finish within {max_turns} tool-calling turns"
            )

        files_touched, lines_changed = self._real_git_diff_stats()
        return DiffOutput(
            files_touched=tuple(files_touched),
            lines_changed=lines_changed,
            commit_message=commit_message,
            subtask_id=subtask.task_id,
        )

    # -- Real tool execution, scoped to the real workspace ------------------

    def _execute_tool(self, name: str, tool_input: dict) -> dict:
        try:
            if name == _READ_FILE_TOOL:
                path = _safe_join(self._workspace_root, tool_input["path"])
                if not path.is_file():
                    return {"error": f"no such file: {tool_input['path']}"}
                return {"content": path.read_text(errors="replace")}
            if name == _WRITE_FILE_TOOL:
                path = _safe_join(self._workspace_root, tool_input["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(tool_input["content"])
                return {"written": True, "path": tool_input["path"], "bytes": len(tool_input["content"])}
            if name == _LIST_FILES_TOOL:
                path = _safe_join(self._workspace_root, tool_input.get("path", "."))
                if not path.is_dir():
                    return {"error": f"no such directory: {tool_input.get('path', '.')}"}
                entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir() if p.name != ".git")
                return {"entries": entries}
            return {"error": f"unknown tool {name!r}"}
        except ValueError as exc:
            # A path-escape attempt (_safe_join) -- reported back to the
            # model as a tool error, not raised out of the loop, so a
            # misbehaving-but-not-malicious model gets a chance to correct
            # itself rather than the whole subtask aborting.
            return {"error": str(exc)}

    @staticmethod
    def _tool_result(tool_use_id: str | None, payload: dict) -> dict:
        return {"toolResult": {"toolUseId": tool_use_id, "content": [{"json": payload}]}}

    def _real_git_diff_stats(self) -> tuple[list[str], int]:
        """Real `git status`/`git diff --numstat` against the real
        workspace -- what actually changed on disk, never the model's own
        claim. Includes untracked new files (git diff --numstat alone
        would miss those) via `git add -A --dry-run`-free `git status
        --porcelain` for the file list, and `git diff --numstat HEAD --
        -- ` for line counts on tracked changes plus a line count for new
        files computed directly."""
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self._workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        files_touched = sorted({line[3:].strip() for line in status.stdout.splitlines() if line.strip()})

        lines_changed = 0
        numstat = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=self._workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for line in numstat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                lines_changed += int(parts[0]) + int(parts[1])

        # New (untracked) files don't appear in `git diff --numstat HEAD`
        # at all -- count their real line count directly so a
        # brand-new-file subtask doesn't report 0 lines changed.
        for rel_path in files_touched:
            candidate = self._workspace_root / rel_path
            if candidate.is_file():
                tracked = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", rel_path],
                    cwd=self._workspace_root,
                    capture_output=True,
                    check=False,
                )
                if tracked.returncode != 0:  # untracked -- not counted by the numstat call above
                    lines_changed += sum(1 for _ in candidate.open(errors="replace"))

        return files_touched, lines_changed

    # -- Converse plumbing (auto tool choice, multiple real tools) ---------

    def _converse_with_tools(self, messages: list[dict]) -> dict:
        try:
            return self._client.converse(
                modelId=self._config.model_id,
                system=[{"text": _SYSTEM_PROMPT}],
                messages=messages,
                inferenceConfig={"maxTokens": self._config.max_tokens, "temperature": self._config.temperature},
                toolConfig={"tools": _TOOL_SPECS, "toolChoice": {"auto": {}}},
            )
        except (BedrockThrottledError, BedrockAccessDeniedError, BedrockInvocationError, BedrockMalformedOutputError):
            raise
        except Exception as exc:  # pragma: no cover - defensive: surface any other real boto3/ClientError as-is
            raise BedrockInvocationError(f"real tool-use converse call failed: {exc}") from exc
