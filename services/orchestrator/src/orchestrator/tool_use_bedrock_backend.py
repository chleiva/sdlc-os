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

**A second real live-run bug, same shape, fixed the same lockstep way**:
a plan honestly sized "L" still produced a 2083-line diff across 2 files
against L's 800-line budget, because no `AgentBackend` anywhere told the
model what its own `story_size` choice actually costs -- see
`checkpoints.size_budget_prompt_text` (spliced into every vendor's plan
prompt/tool-description, same four files as above) for the planning-side
fix, and `_SIZE_WARNING_FRACTION`/`implement_subtask` below for this
class's own implementation-side half: a real, running `git diff` check
that nudges the model mid-loop as it approaches this story's budget,
instead of only finding out from the Section 9.3 checkpoint after the
whole diff is already written.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from orchestrator.bedrock_backend import (
    BedrockAccessDeniedError,
    BedrockAgentBackend,
    BedrockBackendConfig,
    BedrockInvocationError,
    BedrockMalformedOutputError,
    BedrockThrottledError,
    call_converse_with_retry,
)
from orchestrator.checkpoints import DEFAULT_BUDGETS, SINGLE_FILE_LINE_MULTIPLIER
from orchestrator.model_backend import AgentBackend, DiffOutput, PlanOutput, SubTask
from orchestrator.worktree import (
    WorktreeHandle,
    create_agent_worktree,
    remove_agent_worktree,
)

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


#: Real live-run bug this closes, the implementation-stage half of the
#: same fix `checkpoints.size_budget_prompt_text` makes on the planning
#: side: a story plan can honestly pick "L" and still overshoot its
#: budget (a real run's diff reached 2083 lines across 2 files against
#: an 800-line ceiling) because nothing mid-implementation ever told the
#: model how much of that budget its own subtask had already spent --
#: the real Section 9.3 size checkpoint only fires *after* the whole
#: diff is already written. This is the fraction of the resolved line
#: budget at which the loop starts nudging the model to wrap up.
#: Deliberately lower than `checkpoints.TIME_COST_WARNING_FRACTION`'s
#: 0.8: a single write_file call can add hundreds of lines in one turn,
#: so this leaves more real room to react before the hard ceiling than a
#: slower-moving time/cost budget needs.
_SIZE_WARNING_FRACTION = 0.7


def _effective_line_budget(story_size: str | None, *, single_file: bool) -> int | None:
    """Same effective-threshold arithmetic as `checkpoints.check_size`
    (single-file multiplier included), so the running nudge below warns
    against the exact number the real size checkpoint will enforce --
    never a separately-guessed threshold that could drift from it.
    Returns None when `story_size` is missing/unrecognized or resolves
    to XL (`DEFAULT_BUDGETS["XL"]` is None) -- there is nothing sized to
    warn against in that case."""
    budget = DEFAULT_BUDGETS.get(story_size or "") if story_size else None
    if budget is None:
        return None
    return budget.size_checkpoint_lines * (SINGLE_FILE_LINE_MULTIPLIER if single_file else 1)


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
        # (New) real (client, region_name) pairs to fail over to if
        # `client`/`config.region_name` exhausts its own retry budget --
        # see `bedrock_backend.call_converse_with_retry`. Passed through
        # to the composed planning backend too, so a plan/re-plan call
        # gets the exact same regional resilience as implementation.
        fallback_clients: list[tuple[Any, str]] | None = None,
        # (New) real inputs for `implement_subtasks_parallel`'s genuine
        # concurrency: the real repo `worktree.create_agent_worktree`
        # branches new per-subtask worktrees off of, the directory they
        # live under, and the ref (this run's own branch head) each one
        # starts from. All three are required for real parallel
        # execution; any missing means "can't safely create isolated
        # worktrees here" and `implement_subtasks_parallel` falls back
        # to the inherited sequential behavior rather than guessing.
        repo_path: Path | None = None,
        worktrees_root: Path | None = None,
        base_ref: str | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._workspace_root = Path(workspace_root)
        self._max_turns_override = max_turns
        self._fallback_clients = fallback_clients or []
        self._repo_path = Path(repo_path) if repo_path is not None else None
        self._worktrees_root = Path(worktrees_root) if worktrees_root is not None else None
        self._base_ref = base_ref
        # One instance, one sticky region preference (see
        # `bedrock_backend.call_converse_with_retry`'s own docstring) --
        # separate from the composed planning backend's own, since
        # planning and implementation are different real Converse call
        # sites that may legitimately land on different regions.
        self._sticky_state: dict = {"index": 0}
        # Planning delegates to the existing, already-real structured-output
        # backend -- composition, not reimplementation (see module docstring).
        self._plan_backend = BedrockAgentBackend(config, client, fallback_clients=fallback_clients)

    # -- AgentBackend interface -------------------------------------------------

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        return self._plan_backend.author_plan(run_context=run_context)

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        return self._plan_backend.re_plan(run_context=run_context, feedback=feedback)

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        story_size = run_context.get("story_size")
        # Real live-run bug this closes: see `_SIZE_WARNING_FRACTION`'s own
        # comment -- a plan that honestly picked "L" could still overshoot
        # its 800-line budget because nothing here ever told the model
        # what that budget actually was. Told once, up front, so it can
        # size this subtask's own work proportionately from the start
        # (a story budget is shared across every subtask in the plan, not
        # granted fresh to each one).
        system_text = _SYSTEM_PROMPT
        if story_size:
            budget = DEFAULT_BUDGETS.get(story_size)
            if budget is not None:
                system_text = system_text + (
                    f"\n\nThis story is sized {story_size!r}: its WHOLE diff, across every subtask "
                    f"combined, must stay at or under {budget.size_checkpoint_lines} changed lines "
                    f"across at most {budget.size_checkpoint_files} files (more headroom, up to "
                    f"{budget.size_checkpoint_lines * SINGLE_FILE_LINE_MULTIPLIER} lines, if this "
                    "subtask's changes land in a single file). That budget is shared with every "
                    "other subtask in this plan, not reset for each one -- keep this subtask's own "
                    "footprint no larger than its fair share, and call finish as soon as it is "
                    "genuinely done rather than continuing to add content beyond what the subtask "
                    "actually requires."
                )

        user_prompt = (
            "Implement the following subtask for real, using the tools "
            "provided.\n\n"
            f"run_context:\n{json.dumps(run_context, default=str, indent=2)}\n\n"
            "subtask:\n"
            f"{json.dumps({'task_id': subtask.task_id, 'description': subtask.description, 'interface_contract': subtask.interface_contract}, indent=2)}"
        )
        messages: list[dict] = [{"role": "user", "content": [{"text": user_prompt}]}]

        max_turns = self._max_turns_override or _max_turns_for_story_size(story_size)

        commit_message = ""
        for _turn in range(max_turns):
            response = self._converse_with_tools(messages, system_text=system_text)
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

            # Real live-run bug this closes (see `_SIZE_WARNING_FRACTION`):
            # a running, real `git diff` check -- not a model self-report
            # -- so the model finds out it is approaching this subtask's
            # share of the story budget *while it can still stop*, instead
            # of only after the real Section 9.3 size checkpoint pauses
            # the whole run post-hoc. Cheap (local subprocesses only) and
            # bounded by the same `max_turns` cap every other turn already
            # pays for.
            running_touched, running_lines = self._real_git_diff_stats()
            threshold = _effective_line_budget(story_size, single_file=len(running_touched) <= 1)
            if threshold is not None:
                if running_lines >= threshold * _SIZE_WARNING_FRACTION:
                    tool_result_blocks.append(
                        {
                            "text": (
                                f"[size budget] This subtask's diff so far is {running_lines} lines across "
                                f"{len(running_touched)} file(s), approaching or over the "
                                f"{threshold}-line ceiling this story's size class allows for its "
                                "WHOLE diff (shared across every subtask, not just this one). Wrap "
                                "this subtask up with the minimum remaining changes and call finish "
                                "-- do not keep expanding it."
                            )
                        }
                    )

            messages.append({"role": "user", "content": tool_result_blocks})
        else:
            raise BedrockAgenticLoopExhaustedError(
                f"subtask {subtask.task_id!r} did not finish within {max_turns} tool-calling turns"
            )

        files_touched, lines_changed = self._real_git_diff_stats()
        # Real bug this closes -- and a severe one: nothing anywhere in
        # this whole pipeline ever ran a real `git commit`. `DiffOutput
        # .commit_message` existed and was carried all the way through
        # to the real PR body, but the actual file changes underneath
        # it were only ever real, UNCOMMITTED working-tree edits.
        # `_packaging_fn`'s `git push HEAD:refs/heads/<branch>` only
        # ever transmits *committed* history -- it silently pushed just
        # the worktree's original base commit, every single time,
        # producing a real branch/PR with none of the actual generated
        # code in it. Confirmed against a real repo: every prior "real
        # PR" this session believed had succeeded would have been
        # empty. Committing here, once per subtask (using the model's
        # own real commit message, exactly what it's for), is also what
        # makes each subtask's own diff stats above correct in
        # isolation -- the next subtask's `git diff --numstat HEAD`
        # starts clean instead of accumulating every prior subtask's
        # changes into one undifferentiated blob.
        self._real_git_commit(commit_message or f"Implement {subtask.task_id}")
        return DiffOutput(
            files_touched=tuple(files_touched),
            lines_changed=lines_changed,
            commit_message=commit_message,
            subtask_id=subtask.task_id,
        )

    # -- Real parallel execution (§8.1/§9.5 `parallel_group`) ----------------

    def implement_subtasks_parallel(self, *, run_context: dict, subtasks: list[SubTask]) -> list[DiffOutput]:
        """Real concurrency lever: `worktree.py`'s `create_agent_worktree`/
        `remove_agent_worktree` already implemented real git-worktree-per-
        agent isolation (§8.1), but its own docstring flagged "a full
        concurrent scheduler that actually runs those agents at the same
        time is out of this deliverable's scope" -- this closes that gap.

        For each ready subtask in `subtasks` (`core.py` has already
        verified they share one `parallel_group` and their dependencies
        are satisfied): create a real, isolated worktree branched off
        this run's own branch head, hand it to a transient
        `BedrockToolUseAgentBackend` pinned to one region (round-robined
        across `[primary] + fallback_clients` -- one *starting* region
        per worker for real load distribution, while each worker still
        gets the *full* region list as its own retry/fail-over resilience
        list, unchanged from the sequential case), and run
        `implement_subtask` for real, concurrently, on a real OS thread
        per subtask (these are network-bound Converse calls plus local
        file I/O, not CPU-bound -- a thread pool gives real concurrency
        here without multiprocessing's IPC cost).

        Falls back to the inherited sequential behavior (one subtask at a
        time, this instance's own single worktree/region) if this
        instance wasn't constructed with real worktree-creation inputs,
        or if there's nothing to parallelize.
        """
        # Local, narrowed copies: mypy can't carry a None-check on a
        # `self.` attribute across the closure boundary below (another
        # thread could in principle reassign it), and these three are
        # genuinely required non-None for every use inside `_run_one`.
        repo_path, worktrees_root, base_ref = self._repo_path, self._worktrees_root, self._base_ref
        if len(subtasks) < 2 or repo_path is None or worktrees_root is None or base_ref is None:
            return super().implement_subtasks_parallel(run_context=run_context, subtasks=subtasks)

        regions: list[tuple[Any, str]] = [(self._client, self._config.region_name)] + list(self._fallback_clients)
        batch_id = uuid.uuid4().hex[:8]
        run_id = run_context.get("run_id", "run")

        def _run_one(index: int, subtask: SubTask) -> tuple[DiffOutput | None, WorktreeHandle, BaseException | None]:
            session_id = f"{run_id}-{subtask.task_id}-{batch_id}"
            handle = create_agent_worktree(
                repo_path=repo_path,
                base_ref=base_ref,
                session_id=session_id,
                worktrees_root=worktrees_root,
            )
            worker_client, worker_region = regions[index % len(regions)]
            worker_config = (
                self._config if worker_region == self._config.region_name
                else dataclasses.replace(self._config, region_name=worker_region)
            )
            # Full resilience list minus the worker's own starting
            # region -- a worker that starts in a struggling region can
            # still fail over through every other one, exactly like the
            # sequential path.
            worker_fallbacks = [(c, r) for c, r in regions if r != worker_region] or None
            worker_backend = BedrockToolUseAgentBackend(
                worker_config, worker_client,
                workspace_root=Path(handle.worktree_path),
                max_turns=self._max_turns_override,
                fallback_clients=worker_fallbacks,
            )
            try:
                diff = worker_backend.implement_subtask(run_context=run_context, subtask=subtask)
                return diff, handle, None
            except BaseException as exc:  # noqa: BLE001 -- collected, not swallowed; see below
                return None, handle, exc

        results: dict[str, tuple[DiffOutput | None, WorktreeHandle, BaseException | None]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(subtasks)) as pool:
            futures = {pool.submit(_run_one, i, st): st for i, st in enumerate(subtasks)}
            for future in concurrent.futures.as_completed(futures):
                subtask = futures[future]
                results[subtask.task_id] = future.result()

        errors = [exc for _diff, _handle, exc in results.values() if exc is not None]
        if errors:
            # Real, disclosed limitation: no partial merge is attempted --
            # merging a half-failed subtask's incomplete worktree into the
            # run's real branch would corrupt it. Every worktree (failed
            # or succeeded) is cleaned up and the first real failure is
            # re-raised so `core.py`'s existing implementation-failure
            # handling applies unchanged.
            for _diff, handle, _exc in results.values():
                remove_agent_worktree(repo_path=repo_path, worktree_path=Path(handle.worktree_path))
            raise errors[0]

        # Merge every worktree branch into the run's real main worktree,
        # one at a time, in the caller's own dependency order -- never
        # all at once, so a real merge conflict on a later branch is
        # attributed to that specific subtask and never silently
        # clobbers an earlier, already-merged one. Worktrees of the same
        # repo share one object database/ref namespace, so each branch
        # is directly mergeable from `self._workspace_root` with no
        # fetch needed.
        diffs: list[DiffOutput] = []
        for subtask in subtasks:
            diff, handle, _exc = results[subtask.task_id]
            merge = subprocess.run(
                ["git", "merge", "--no-ff", "-m", f"Merge parallel subtask {subtask.task_id}", handle.branch_name],
                cwd=self._workspace_root, capture_output=True, text=True, check=False,
            )
            if merge.returncode != 0:
                for _d, h, _e in results.values():
                    remove_agent_worktree(repo_path=repo_path, worktree_path=Path(h.worktree_path))
                raise BedrockInvocationError(
                    f"real merge conflict bringing parallel subtask {subtask.task_id!r} "
                    f"(branch {handle.branch_name!r}) into the main worktree -- not "
                    f"auto-resolved, needs a human: {merge.stderr.strip()}"
                )
            assert diff is not None  # guaranteed: `errors` was empty, so no subtask failed
            diffs.append(diff)

        for _diff, handle, _exc in results.values():
            remove_agent_worktree(repo_path=repo_path, worktree_path=Path(handle.worktree_path))

        return diffs

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
        except (ValueError, KeyError, TypeError) as exc:
            # A path-escape attempt (_safe_join, ValueError) -- or a real
            # live-run bug this now also closes: a `KeyError` (e.g. the
            # model called write_file with no "content" field) used to
            # propagate straight out of this method, out of the whole
            # implement_subtask loop, and crash the entire subtask (and
            # on resume, the whole run: confirmed on a real live run --
            # "resuming run ... failed with a real error
            # (KeyError('content'))"). The tool's declared `inputSchema`
            # `required` list is a hint to the model via Bedrock's
            # toolConfig, never a real guarantee it complies -- so any
            # missing/malformed field is reported back to the model as a
            # real tool error (same as the path-escape case) and never
            # raised out of the loop, giving a misbehaving-but-not-
            # malicious model a chance to correct itself within its own
            # turn budget instead of aborting the run.
            if isinstance(exc, KeyError):
                return {"error": f"{name}: missing required field {exc.args[0]!r} in the tool call input"}
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

    def _real_git_commit(self, message: str) -> None:
        """Real `git add -A` + `git commit` against the real worktree --
        see `implement_subtask`'s own comment for the real bug this
        closes (nothing ever committed before this, so nothing ever
        really got pushed). A no-op, not an error, if there is nothing
        to commit (e.g. a subtask that made no real file change, or --
        defensively -- if this is somehow called twice in a row with no
        new changes in between)."""
        add = subprocess.run(["git", "add", "-A"], cwd=self._workspace_root, capture_output=True, text=True, check=False)
        if add.returncode != 0:
            raise BedrockInvocationError(f"real 'git add -A' failed in {self._workspace_root}: {add.stderr}")

        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=self._workspace_root, capture_output=True, check=False,
        )
        if staged.returncode == 0:
            # Real `git diff --cached --quiet` exit code convention:
            # 0 means nothing is staged -- nothing to commit, not a
            # failure (a subtask can legitimately make no real change,
            # e.g. one that only reads files to orient itself).
            return

        # Real author identity, not whatever this machine's own
        # `~/.gitconfig` happens to say (which could be missing
        # entirely in a fresh environment, or could be a real human
        # operator's own identity -- Sec. 15/17.1's "named, not
        # anonymous" non-human-identity principle applies to commit
        # authorship exactly as it already does to the GitHub bot actor
        # `source_control.audit.bot_actor` computes). `-c` scopes this
        # to just this one command, never touching the real repo's or
        # this machine's own git config.
        commit = subprocess.run(
            [
                "git",
                "-c", "user.name=SDLC Auto",
                "-c", "user.email=sdlc-auto@users.noreply.github.com",
                "commit", "-m", message,
            ],
            cwd=self._workspace_root, capture_output=True, text=True, check=False,
        )
        if commit.returncode != 0:
            raise BedrockInvocationError(f"real 'git commit' failed in {self._workspace_root}: {commit.stderr}")

    # -- Converse plumbing (auto tool choice, multiple real tools) ---------

    def _converse_with_tools(self, messages: list[dict], *, system_text: str | None = None) -> dict:
        """Real live-run bug this closes: this call used to hit the real
        `boto3` client directly with no retry at all -- a single
        transient `ReadTimeoutError` (a real `BotoCoreError` subclass,
        hit for real generating a large file) crashed the whole run
        outright. `call_converse_with_retry` is the exact same retry-
        with-backoff logic `BedrockAgentBackend._converse` (the planning
        call) already had and was already tested -- shared here so both
        real Converse call sites are equally resilient to the same real
        failure modes, not just one of them.

        `system_text` defaults to the module-level `_SYSTEM_PROMPT` --
        `implement_subtask` passes a per-call variant with this story's
        own resolved size budget spliced in (see `_SIZE_WARNING_FRACTION`'s
        comment) so every turn's system message actually reflects it."""
        request_kwargs = {
            "modelId": self._config.model_id,
            "system": [{"text": system_text if system_text is not None else _SYSTEM_PROMPT}],
            "messages": messages,
            "inferenceConfig": {"maxTokens": self._config.max_tokens, "temperature": self._config.temperature},
            "toolConfig": {"tools": _TOOL_SPECS, "toolChoice": {"auto": {}}},
        }
        try:
            return call_converse_with_retry(
                client=self._client, request_kwargs=request_kwargs, config=self._config,
                fallback_clients=self._fallback_clients, sticky_state=self._sticky_state,
            )
        except (BedrockThrottledError, BedrockAccessDeniedError, BedrockInvocationError, BedrockMalformedOutputError):
            raise
        except Exception as exc:  # pragma: no cover - defensive: surface any other real boto3/ClientError as-is
            raise BedrockInvocationError(f"real tool-use converse call failed: {exc}") from exc
