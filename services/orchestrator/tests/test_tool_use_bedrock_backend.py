"""Tests for `orchestrator.tool_use_bedrock_backend.BedrockToolUseAgentBackend`
against a hand-built fake `bedrock-runtime` client (`tests/fake_bedrock_client.py`,
the same one `test_bedrock_backend.py` uses) and a real local git repo
(`git_fixture_repo`) -- no network access, no real AWS account, no real
model weights.

Real gap this file closes: this class -- the one that actually writes
real files to a real worktree and computes a real `git diff` -- had zero
committed test coverage before this. Every other real integration in
this repo (`bedrock_backend.py`, `source_control`, ...) is validated
against a committed fake/mock; this one had only been exercised through
manual, uncommitted live-run smoke testing. That gap is exactly why the
turn-budget bugs surfaced only on a real live run instead of in CI.
"""

from __future__ import annotations

import subprocess

import pytest
from botocore.exceptions import ReadTimeoutError

from orchestrator.bedrock_backend import (
    BedrockBackendConfig,
    BedrockInvocationError,
    BedrockThrottledError,
)
from orchestrator.checkpoints import DEFAULT_BUDGETS
from orchestrator.model_backend import SubTask
from orchestrator.tool_use_bedrock_backend import (
    BedrockAgenticLoopExhaustedError,
    BedrockToolUseAgentBackend,
    _hard_turn_cap_for_story_size,
    _soft_turn_target_for_story_size,
)
from tests.fake_bedrock_client import (
    FakeBedrockRuntimeClient,
    converse_response_with_tool_call,
)

_FAKE_MODEL_ID = "minimax.m2-5-v1:0"


@pytest.fixture
def fake_client():
    return FakeBedrockRuntimeClient()


@pytest.fixture
def backend(fake_client, git_fixture_repo):
    return BedrockToolUseAgentBackend(
        BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1"),
        fake_client,
        workspace_root=git_fixture_repo,
    )


def test_implement_subtask_writes_a_real_file_and_reports_a_real_diff(backend, fake_client, git_fixture_repo):
    """The full real loop: one write_file turn, then finish -- the
    returned DiffOutput must reflect what is REALLY on disk (a real
    `git diff`/`git status`), never the model's own self-report."""
    fake_client.queue_response(
        converse_response_with_tool_call("write_file", {"path": "hello.py", "content": "def greet(name):\n    return f'Hello, {name}!'\n"})
    )
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add greet()", "summary": "added hello.py"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="add hello.py", parallel_group=None, depends_on=()),
    )

    assert diff.files_touched == ("hello.py",)
    assert diff.commit_message == "add greet()"
    assert (git_fixture_repo / "hello.py").read_text() == "def greet(name):\n    return f'Hello, {name}!'\n"
    assert diff.lines_changed >= 1

    # Real, severe bug this closes: nothing anywhere in this pipeline
    # ever ran a real `git commit` -- `DiffOutput.commit_message` was
    # computed and carried all the way through to a real PR body, but
    # the actual file change stayed real, UNCOMMITTED working-tree
    # state forever. `_packaging_fn`'s `git push HEAD:...` only ever
    # transmits *committed* history, so it silently pushed just the
    # worktree's original base commit every single time -- confirmed
    # against a real GitHub repo this session: every prior "real PR"
    # this session believed had succeeded was actually empty.
    log = subprocess.run(
        ["git", "log", "--oneline", "-1", "--pretty=%s"], cwd=git_fixture_repo, capture_output=True, text=True, check=True,
    )
    assert log.stdout.strip() == "add greet()"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=git_fixture_repo, capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""  # nothing left uncommitted


def test_read_then_write_then_finish_is_a_real_multi_turn_loop(backend, fake_client, git_fixture_repo):
    """Proves this is a genuine multi-turn loop (not a single call):
    read_file first (of a file that already exists from the fixture's
    own initial commit), then write_file, then finish."""
    fake_client.queue_response(converse_response_with_tool_call("read_file", {"path": "README.md"}))
    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "README.md", "content": "# fixture\nupdated\n"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "update README", "summary": "updated"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="update README", parallel_group=None, depends_on=()),
    )

    assert diff.files_touched == ("README.md",)
    assert (git_fixture_repo / "README.md").read_text() == "# fixture\nupdated\n"
    assert len(fake_client.call_log) == 3  # read, write, finish -- three real converse calls


def test_edit_file_makes_a_real_targeted_change_without_rewriting_the_rest(backend, fake_client, git_fixture_repo):
    """The real live-run finding this tool closes: a targeted edit_file
    call must change only the matched text, leaving the rest of the
    file's real content on disk untouched -- never a full regeneration."""
    (git_fixture_repo / "README.md").write_text("# fixture\nline one\nline two\nline three\n")
    # Committed as this subtask's real starting point (HEAD) -- so the
    # diff `implement_subtask` reports afterward isolates exactly what
    # edit_file changed, not this setup write too.
    subprocess.run(["git", "add", "README.md"], cwd=git_fixture_repo, check=True)
    subprocess.run(["git", "commit", "-m", "test setup: four-line README"], cwd=git_fixture_repo, check=True)
    fake_client.queue_response(converse_response_with_tool_call("edit_file", {"path": "README.md", "old_str": "line two", "new_str": "line TWO edited"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "edit line two", "summary": "s"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="edit README", parallel_group=None, depends_on=()),
    )

    assert (git_fixture_repo / "README.md").read_text() == "# fixture\nline one\nline TWO edited\nline three\n"
    assert diff.files_touched == ("README.md",)
    assert diff.lines_changed == 2  # one real line removed, one real line added -- not a whole-file rewrite


def test_edit_file_missing_file_reports_a_real_tool_error_not_a_crash(backend, fake_client, git_fixture_repo):
    """edit_file against a nonexistent file must not raise out of the
    loop -- it comes back as a real tool-result error (like every other
    `_execute_tool` failure mode), giving the model a real chance to
    recover within its own turn budget (here: falling back to
    write_file for the new file), never crashing the whole subtask."""
    fake_client.queue_response(converse_response_with_tool_call("edit_file", {"path": "does_not_exist.py", "old_str": "x", "new_str": "y"}))
    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "does_not_exist.py", "content": "y\n"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "create it instead", "summary": "s"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="create a file", parallel_group=None, depends_on=()),
    )
    assert diff.files_touched == ("does_not_exist.py",)
    assert (git_fixture_repo / "does_not_exist.py").read_text() == "y\n"
    # three real turns happened (failed edit_file, recovering write_file,
    # finish) -- proof the failed edit_file was recoverable, not fatal.
    assert len(fake_client.call_log) == 3


def test_edit_file_requires_old_str_to_be_unique_unless_replace_all(backend, fake_client, git_fixture_repo):
    (git_fixture_repo / "README.md").write_text("dup\ndup\n")
    fake_client.queue_response(converse_response_with_tool_call("edit_file", {"path": "README.md", "old_str": "dup", "new_str": "single"}))
    fake_client.queue_response(converse_response_with_tool_call("edit_file", {"path": "README.md", "old_str": "dup", "new_str": "both", "replace_all": True}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "replace all", "summary": "s"}))

    backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="dedupe", parallel_group=None, depends_on=()),
    )
    assert (git_fixture_repo / "README.md").read_text() == "both\nboth\n"


def test_edit_file_rejects_a_no_op_edit(backend, fake_client, git_fixture_repo):
    fake_client.queue_response(converse_response_with_tool_call("edit_file", {"path": "README.md", "old_str": "fixture", "new_str": "fixture"}))
    fake_client.queue_response(converse_response_with_tool_call("edit_file", {"path": "README.md", "old_str": "fixture", "new_str": "real edit"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "real edit", "summary": "s"}))

    backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="edit README", parallel_group=None, depends_on=()),
    )
    assert (git_fixture_repo / "README.md").read_text() == "# real edit\n"


def test_a_malformed_tool_call_missing_a_required_field_does_not_crash_the_run(backend, fake_client, git_fixture_repo):
    """Real live-run bug this closes: a `write_file` tool call with no
    "content" field (Bedrock's own toolConfig `required` list is a hint
    to the model, never a real guarantee it complies) used to raise a
    bare `KeyError('content')` straight out of the implementation loop,
    crashing the entire subtask -- confirmed on a real live run, where
    it surfaced as "resuming run ... failed with a real error
    (KeyError('content'))" and killed the whole resume attempt. It must
    instead come back to the model as a real tool error, giving it a
    chance to retry within its own turn budget."""
    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "hello.py"}))  # no "content"
    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "hello.py", "content": "x = 1\n"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add hello.py", "summary": "s"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="add hello.py", parallel_group=None, depends_on=()),
    )

    assert diff.files_touched == ("hello.py",)
    assert (git_fixture_repo / "hello.py").read_text() == "x = 1\n"
    # The malformed call really was reported back as a tool error, not
    # silently ignored or retried transparently -- the model saw it and
    # made real progress on its own very next turn. `call_log[i]
    # ["messages"]` is a live reference to the one growing conversation
    # list (not a per-call snapshot), so index by real transcript
    # position: [0]=initial prompt, [1]=assistant's first (malformed)
    # tool call, [2]=the user-role tool-result turn reporting it back.
    tool_result = fake_client.call_log[-1]["messages"][2]["content"][0]["toolResult"]["content"][0]["json"]
    assert "missing required field" in tool_result["error"]
    assert "content" in tool_result["error"]


def test_each_subtask_gets_its_own_real_commit_with_a_distinct_bot_identity(backend, fake_client, git_fixture_repo):
    """Two real subtasks in the same backend instance must produce two
    real, separate commits (not one combined commit, and not zero) --
    and neither commit may be attributed to whatever this machine's own
    `~/.gitconfig` says (a real human operator's identity, or nothing
    at all in a fresh environment) -- Sec. 15/17.1's real non-human-
    identity principle applies to commit authorship."""
    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "a.py", "content": "a = 1\n"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add a.py", "summary": "s"}))
    backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="add a.py", parallel_group=None, depends_on=()),
    )

    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "b.py", "content": "b = 2\n"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add b.py", "summary": "s"}))
    backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t2", description="add b.py", parallel_group=None, depends_on=()),
    )

    log = subprocess.run(
        ["git", "log", "--pretty=%s|%an|%ae", "-2"], cwd=git_fixture_repo, capture_output=True, text=True, check=True,
    )
    lines = log.stdout.strip().splitlines()
    assert lines[0] == "add b.py|SDLC Auto|sdlc-auto@users.noreply.github.com"
    assert lines[1] == "add a.py|SDLC Auto|sdlc-auto@users.noreply.github.com"


def test_a_transient_read_timeout_is_retried_then_the_turn_succeeds(fake_client, git_fixture_repo):
    """Real live-run bug this closes: `_converse_with_tools` used to
    hit the real boto3 client directly with no retry at all, so a
    single transient `ReadTimeoutError` (a real `botocore.exceptions
    .BotoCoreError` subclass -- hit for real on a live run generating a
    large file) crashed the entire run outright. It must now retry via
    `call_converse_with_retry` (the same real logic
    `BedrockAgentBackend._converse`, the planning call, already had) and
    succeed once the transient condition clears -- proven here with a
    tiny real backoff so the test itself stays fast."""
    config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1", max_retries=2, retry_backoff_seconds=0.01)
    backend = BedrockToolUseAgentBackend(config, fake_client, workspace_root=git_fixture_repo)

    fake_client.queue_response(ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/fake/converse"))
    fake_client.queue_response(converse_response_with_tool_call("write_file", {"path": "hello.py", "content": "x = 1\n"}))
    fake_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add hello.py", "summary": "added"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="add hello.py", parallel_group=None, depends_on=()),
    )

    assert diff.files_touched == ("hello.py",)
    assert (git_fixture_repo / "hello.py").read_text() == "x = 1\n"
    assert len(fake_client.call_log) == 3  # 1 failed attempt (retried) + write + finish


def test_persistent_connection_errors_exhaust_retries_and_raise_bedrock_throttled_error(fake_client, git_fixture_repo):
    """A connection error that never clears must surface as
    `BedrockThrottledError` (retryable-but-exhausted, matching
    `BedrockAgentBackend`'s own classification) -- not silently accepted,
    and not the old bare `BedrockInvocationError` a zero-retry call
    would have produced."""
    config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1", max_retries=2, retry_backoff_seconds=0.01)
    backend = BedrockToolUseAgentBackend(config, fake_client, workspace_root=git_fixture_repo)

    for _ in range(2):
        fake_client.queue_response(ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/fake/converse"))

    with pytest.raises(BedrockThrottledError):
        backend.implement_subtask(
            run_context={"run_id": "r1", "story_size": "S"},
            subtask=SubTask(task_id="t1", description="add hello.py", parallel_group=None, depends_on=()),
        )
    assert len(fake_client.call_log) == 2  # both retry attempts consumed, then gave up


def test_implementation_loop_falls_over_to_a_fallback_region_too(fake_client, git_fixture_repo):
    """The same real region-fallback resilience `bedrock_backend
    .call_converse_with_retry` gives planning must also cover the
    multi-turn implementation loop -- both are real Converse call sites
    hitting the exact same real regional failure mode."""
    fallback_client = FakeBedrockRuntimeClient()
    config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1", max_retries=2, retry_backoff_seconds=0.01)
    backend = BedrockToolUseAgentBackend(
        config, fake_client, workspace_root=git_fixture_repo, fallback_clients=[(fallback_client, "us-west-2")],
    )

    for _ in range(2):
        fake_client.queue_response(ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/fake/converse"))
    fallback_client.queue_response(converse_response_with_tool_call("write_file", {"path": "hello.py", "content": "x = 1\n"}))
    fallback_client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add hello.py", "summary": "added"}))

    diff = backend.implement_subtask(
        run_context={"run_id": "r1", "story_size": "S"},
        subtask=SubTask(task_id="t1", description="add hello.py", parallel_group=None, depends_on=()),
    )
    assert diff.files_touched == ("hello.py",)
    assert len(fake_client.call_log) == 2  # primary region's full budget spent first
    assert len(fallback_client.call_log) == 2  # write + finish, both against the fallback region


def test_never_calling_finish_raises_within_the_derived_turn_budget(fake_client, git_fixture_repo):
    """An "S"-sized story derives a small, real hard turn cap (see
    `_hard_turn_cap_for_story_size` -- the loop's actual bound, looser
    than the soft target the model is told, per `_HARD_CAP_MULTIPLIER`'s
    own comment) -- queue one more list_files response than that cap
    allows and confirm the loop stops there, raising rather than looping
    forever or silently accepting non-completion."""
    expected_max_turns = _hard_turn_cap_for_story_size("S")
    for _ in range(expected_max_turns + 2):  # more than enough queued responses
        fake_client.queue_response(converse_response_with_tool_call("list_files", {"path": "."}))

    backend = BedrockToolUseAgentBackend(
        BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1"),
        fake_client,
        workspace_root=git_fixture_repo,
    )

    with pytest.raises(BedrockAgenticLoopExhaustedError, match=str(expected_max_turns)):
        backend.implement_subtask(
            run_context={"run_id": "r1", "story_size": "S"},
            subtask=SubTask(task_id="t1", description="never finishes", parallel_group=None, depends_on=()),
        )
    assert len(fake_client.call_log) == expected_max_turns


def test_explicit_max_turns_override_takes_precedence_over_story_size(fake_client, git_fixture_repo):
    """The BEDROCK_MAX_TURNS-style explicit override (live_run.py) must
    win over the size-derived default, even for a size that would derive
    a much larger budget."""
    backend = BedrockToolUseAgentBackend(
        BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1"),
        fake_client,
        workspace_root=git_fixture_repo,
        max_turns=2,
    )
    for _ in range(5):
        fake_client.queue_response(converse_response_with_tool_call("list_files", {"path": "."}))

    with pytest.raises(BedrockAgenticLoopExhaustedError, match="2 tool-calling turns"):
        backend.implement_subtask(
            run_context={"run_id": "r1", "story_size": "L"},  # L would derive a much bigger budget
            subtask=SubTask(task_id="t1", description="never finishes", parallel_group=None, depends_on=()),
        )
    assert len(fake_client.call_log) == 2


class TestImplementSubtasksParallel:
    """Real concurrency for `SubTask.parallel_group` (§8.1/§9.5):
    `worktree.py`'s `create_agent_worktree`/`remove_agent_worktree` were
    real but nothing ever drove a real concurrent scheduler through them
    before this -- see that module's own docstring ("out of this
    deliverable's scope"). These tests use a real local git repo
    (`git_fixture_repo`), real `git worktree add`/`merge` calls, and real
    OS threads -- only the Bedrock Converse call itself is faked."""

    def test_two_independent_subtasks_run_concurrently_and_both_merge_in(self, git_fixture_repo, tmp_path):
        client_a = FakeBedrockRuntimeClient()
        client_b = FakeBedrockRuntimeClient()
        client_a.queue_response(converse_response_with_tool_call("write_file", {"path": "a.py", "content": "a = 1\n"}))
        client_a.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add a.py", "summary": "added a"}))
        client_b.queue_response(converse_response_with_tool_call("write_file", {"path": "b.py", "content": "b = 1\n"}))
        client_b.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add b.py", "summary": "added b"}))

        config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1")
        backend = BedrockToolUseAgentBackend(
            config, client_a,
            workspace_root=git_fixture_repo,
            fallback_clients=[(client_b, "us-west-2")],
            repo_path=git_fixture_repo,
            worktrees_root=tmp_path / "worktrees",
            base_ref="main",
        )

        diffs = backend.implement_subtasks_parallel(
            run_context={"run_id": "r1", "story_size": "S"},
            subtasks=[
                SubTask(task_id="ta", description="add a.py", parallel_group="g1", depends_on=()),
                SubTask(task_id="tb", description="add b.py", parallel_group="g1", depends_on=()),
            ],
        )

        assert {d.subtask_id for d in diffs} == {"ta", "tb"}
        assert (git_fixture_repo / "a.py").read_text() == "a = 1\n"
        assert (git_fixture_repo / "b.py").read_text() == "b = 1\n"

        # Real round-robin distribution: each subtask really ran against
        # a *different* region's client, not both on the primary.
        assert len(client_a.call_log) == 2
        assert len(client_b.call_log) == 2

        # Both real merge commits landed on the run's real main branch.
        log = subprocess.run(["git", "log", "--oneline"], cwd=git_fixture_repo, capture_output=True, text=True, check=True)
        assert "Merge parallel subtask ta" in log.stdout
        assert "Merge parallel subtask tb" in log.stdout

        # Per-subtask worktrees are real and then really cleaned up
        # afterward, not leaked.
        wt_list = subprocess.run(["git", "worktree", "list"], cwd=git_fixture_repo, capture_output=True, text=True, check=True)
        assert wt_list.stdout.count("\n") == 1  # only the main worktree remains registered

    def test_a_real_conflicting_merge_raises_rather_than_silently_resolving(self, git_fixture_repo, tmp_path):
        """Two subtasks in the same group writing the same file is
        exactly the failure mode `plan_artifact.py`'s `interface_contract`
        requirement is meant to prevent upstream -- but nothing enforces
        the plan actually avoided it, so this must be a real, loud
        failure (a human decision), never a silent last-writer-wins."""
        client_a = FakeBedrockRuntimeClient()
        client_b = FakeBedrockRuntimeClient()
        client_a.queue_response(converse_response_with_tool_call("write_file", {"path": "same.py", "content": "a = 1\n"}))
        client_a.queue_response(converse_response_with_tool_call("finish", {"commit_message": "write same.py from a", "summary": "a"}))
        client_b.queue_response(converse_response_with_tool_call("write_file", {"path": "same.py", "content": "b = 2\n"}))
        client_b.queue_response(converse_response_with_tool_call("finish", {"commit_message": "write same.py from b", "summary": "b"}))

        config = BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1")
        backend = BedrockToolUseAgentBackend(
            config, client_a,
            workspace_root=git_fixture_repo,
            fallback_clients=[(client_b, "us-west-2")],
            repo_path=git_fixture_repo,
            worktrees_root=tmp_path / "worktrees",
            base_ref="main",
        )

        with pytest.raises(BedrockInvocationError, match="real merge conflict"):
            backend.implement_subtasks_parallel(
                run_context={"run_id": "r1", "story_size": "S"},
                subtasks=[
                    SubTask(task_id="ta", description="write same.py", parallel_group="g1", depends_on=()),
                    SubTask(task_id="tb", description="write same.py differently", parallel_group="g1", depends_on=()),
                ],
            )

        # Cleaned up even on failure -- no leaked worktree.
        wt_list = subprocess.run(["git", "worktree", "list"], cwd=git_fixture_repo, capture_output=True, text=True, check=True)
        assert wt_list.stdout.count("\n") == 1

    def test_missing_worktree_inputs_falls_back_to_sequential(self, git_fixture_repo):
        """No `repo_path`/`worktrees_root`/`base_ref` given -- must use
        the inherited sequential default (one worktree, one region, one
        subtask at a time), never crash trying to create a real worktree
        with no real repo to create it from."""
        client = FakeBedrockRuntimeClient()
        client.queue_response(converse_response_with_tool_call("write_file", {"path": "a.py", "content": "a = 1\n"}))
        client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add a.py", "summary": "a"}))
        client.queue_response(converse_response_with_tool_call("write_file", {"path": "b.py", "content": "b = 1\n"}))
        client.queue_response(converse_response_with_tool_call("finish", {"commit_message": "add b.py", "summary": "b"}))

        backend = BedrockToolUseAgentBackend(
            BedrockBackendConfig(model_id=_FAKE_MODEL_ID, region_name="us-east-1"),
            client,
            workspace_root=git_fixture_repo,
        )
        diffs = backend.implement_subtasks_parallel(
            run_context={"run_id": "r1", "story_size": "S"},
            subtasks=[
                SubTask(task_id="ta", description="add a.py", parallel_group="g1", depends_on=()),
                SubTask(task_id="tb", description="add b.py", parallel_group="g1", depends_on=()),
            ],
        )
        assert [d.subtask_id for d in diffs] == ["ta", "tb"]


class TestMaxTurnsForStorySize:
    """`_soft_turn_target_for_story_size` (see its own docstring for the
    real-anchor rationale): larger declared story sizes derive
    proportionally larger turn targets, and an unknown/missing size
    falls back to a fixed, documented default rather than raising."""

    def test_larger_sizes_derive_larger_budgets(self):
        s, m, l = (_soft_turn_target_for_story_size(size) for size in ("S", "M", "L"))
        assert s < m < l

    def test_derivation_is_proportional_to_the_real_wall_clock_budget(self):
        # Same seconds-per-turn/fraction assumptions apply to every size,
        # so the ratio between derived turn counts must match the ratio
        # between the real Sec. 9.4 wall_clock_minutes budgets exactly.
        s_turns = _soft_turn_target_for_story_size("S")
        m_turns = _soft_turn_target_for_story_size("M")
        s_minutes = DEFAULT_BUDGETS["S"].wall_clock_minutes
        m_minutes = DEFAULT_BUDGETS["M"].wall_clock_minutes
        assert m_turns / s_turns == pytest.approx(m_minutes / s_minutes, rel=0.05)

    def test_unknown_or_missing_size_falls_back_rather_than_raising(self):
        assert _soft_turn_target_for_story_size(None) > 0
        assert _soft_turn_target_for_story_size("XL") > 0  # DEFAULT_BUDGETS["XL"] is None -- must not KeyError/crash
        assert _soft_turn_target_for_story_size("not-a-real-size") > 0


class TestHardTurnCapForStorySize:
    """`_hard_turn_cap_for_story_size` -- the loop's actual bound, a
    fixed multiple of the soft target above (see `_HARD_CAP_MULTIPLIER`'s
    own comment for why the two are deliberately different numbers)."""

    def test_hard_cap_is_the_multiplier_times_the_soft_target(self):
        from orchestrator.tool_use_bedrock_backend import _HARD_CAP_MULTIPLIER

        for size in ("S", "M", "L", "XL", None, "not-a-real-size"):
            soft = _soft_turn_target_for_story_size(size)
            hard = _hard_turn_cap_for_story_size(size)
            assert hard == max(1, int(soft * _HARD_CAP_MULTIPLIER))
            assert hard >= soft
