"""Shared real-orchestrator wiring for this directory's manual live-run
tools (`live_run.py`, `jira_poll_run.py`). Factored out so both scripts
share the exact same real GitHub App / Bedrock / verification / gate
wiring rather than one being a copy-pasted, silently-drifting fork of
the other.

Same real, no-shortcuts wiring `live_run.py` originally had: a real
GitHub App JWT + installation-token exchange, a real mirror clone/
fetch, a real `git worktree`-isolated branch, a real `git push` and PR;
a real Bedrock/MiniMax M2.5 planning call and a real, multi-turn,
tool-using implementation loop that actually writes files; a real
`RealVerificationRunner`.

**Real gap this file closes (New): a pause can no longer assume someone
is at a keyboard.** Originally every pause (a Section 9 gate, or a
Section 9.3 checkpoint) was resolved by blocking on a terminal `input()`
call -- fine for `live_run.py`, where a human is deliberately sitting
there having just typed the command, but structurally broken for
`jira_poll_run.py`'s whole point (an unattended, automatically-triggered
run: nobody is watching a terminal for it). `core.py`'s own state
machine was never the problem -- a paused run is already durably saved
and resumable later, from a different process, via `resume_run`
(`test_durable_resume.py` proves this). The fix is entirely in this
module: `drive()` takes a pluggable `resolve` callback instead of a
hardcoded `input()` loop. `_interactive_resolve` (used by `run_once`,
i.e. `live_run.py`) is the original terminal-prompt behavior, unchanged.
`jira_poll_run.py` instead passes a resolver that posts a real Jira
comment + a real assignment (never blocks) and returns `None` --
`drive()` then stops and hands back a `RunOutcome` with `paused=True`
plus everything (`run_id`, `branch_name`, `worktree_path`) a later,
separate invocation needs to resume the *same* run once a human replies
with a comment (see `resume_paused_run_async` and `jira_poll_run.py`).

**`jira_key` field, honestly**: `core.py`'s `start_run`/`_run_context`
have exactly one free-text field (`jira_key`) -- there is no separate
task-description field in the Registry's `Run` model at all (a real,
disclosed gap, not something this module invents a workaround around
silently). `_build_environment` embeds `real_jira_key` (when the caller
has one -- `jira_poll_run.py` does, `live_run.py`'s plain CLI usage
doesn't) as a `"<KEY>: <task text>"` prefix, so a Jira-triggered run's
real issue key stays inspectable in the Registry rather than being
replaced entirely by prose, while a manually-typed task still works
exactly as before.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data"
MIRRORS_DIR = Path(__file__).resolve().parent / "mirrors"

# Make every sibling service package importable without requiring the
# operator to have `pip install -e`'d each one into this exact venv --
# these are manual tools, not packaged services; run with a venv that
# already has each service's own dependencies installed (see this
# directory's README for the exact install sequence), and this just
# adds each package's real source to sys.path.
for _pkg in ("orchestrator", "source-control", "run-registry", "verification-pipeline", "issue-tracker", "kms-boundary", "gates"):
    sys.path.insert(0, str(REPO_ROOT / "services" / _pkg / "src"))


def load_dotenv(path: Path) -> None:
    """Minimal, dependency-free .env loader -- does not overwrite a
    variable already present in the real environment (an operator's own
    `export` wins over the file), and skips blank/comment lines. No new
    pip dependency for something this small."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"{name!r} is not set (see deploy/run-worker/README.md, and the repo root's .env)."
        )
    return value


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


@dataclass
class RunOutcome:
    """What actually happened, for a caller (`jira_poll_run.py`) that
    wants to report the real outcome back to Jira -- `live_run.py` only
    needs `.exit_code`.

    `exit_code`: 0 = finished cleanly (see `pr_url`/`final_stage`),
    1 = a gate/checkpoint was resolved negatively (rejected/stopped),
    2 = still paused, no decision was made yet (`resolve` returned
    `None` -- the async path; never happens for the interactive path,
    since `_interactive_resolve` always returns a real decision)."""

    exit_code: int
    run_id: str | None = None
    final_stage: str | None = None
    pr_url: str | None = None
    paused: bool = False
    pause_kind: str | None = None


@dataclass
class Environment:
    """Everything one real run needs, built once by `_build_environment`
    and threaded through `drive()` -- a resolver reads `plan_store`/
    `checkpoint` details off of it to describe a pause; `resume_paused_
    run_async` rebuilds one of these pointed at an *existing* branch/
    worktree instead of creating a new one."""

    orchestrator: Any
    plan_store: Any
    branch_name: str
    worktree_path: Path
    tenant_id: str
    repository: str
    task_description: str
    real_jira_key: str | None
    jira_key_field: str
    outcome_state: dict = field(default_factory=lambda: {"pr_url": None})


# A resolver is called once per pause, and returns the decision string to
# apply ("approve"/"reject" for a gate, "continue"/"stop" for a
# checkpoint) -- or `None` to mean "no decision available right now,
# stop driving" (the async path).
Resolver = Callable[[Any, Environment], "str | None"]


def _interactive_resolve(status: Any, env: Environment) -> str | None:
    """The original `live_run.py` terminal-prompt behavior, unchanged:
    a human is assumed to be right there, so this always returns a real
    decision, never `None`."""
    if status.pause_kind == "gate":
        if status.stage == "plan_approval_gate":
            artifact = env.plan_store.load_latest(status.run_id)
            print("\n=== REAL PLAN, awaiting your real approval ===")
            print(f"outcomes: {artifact.get('outcomes')}")
            print(f"story_size: {artifact.get('risk', {}).get('story_size')}")
            print(f"subtasks: {[s['description'] for s in artifact.get('subtask_graph', {}).get('subtasks', [])]}")
            return "approve" if confirm("Approve this plan?") else "reject"
        if status.stage == "change_review_gate":
            print("\n=== REAL CHANGE, verification passed, awaiting your real review ===")
            return "approve" if confirm("Approve this change for packaging (real PR)?") else "reject"
        raise SystemExit(f"unexpected gate stage {status.stage!r}")
    if status.pause_kind == "checkpoint":
        elicitation = status.checkpoint
        print(f"\n=== REAL CHECKPOINT ({elicitation.trigger}) === {elicitation.reason}")
        return "continue" if confirm("Continue?") else "stop"
    raise SystemExit(f"unexpected pause_kind {status.pause_kind!r}")


def _build_environment(
    *, task_description: str, real_jira_key: str | None = None,
    branch_name: str | None = None, worktree_path: Path | None = None,
) -> Environment:
    """Build a real `Orchestrator` + everything it needs. If `branch_name`/
    `worktree_path` are both given, reuses that *existing* branch/worktree
    (a resumed run continuing after a human's decision) instead of
    cloning the mirror and creating a new one -- the old worktree from
    the run's first invocation is still there on disk; nothing cleans it
    up, and nothing should re-clone/re-branch on top of it."""
    load_dotenv(REPO_ROOT / ".env")

    github_app_id = require_env("GITHUB_APP_ID")
    github_installation_id = require_env("GITHUB_APP_INSTALLATION_ID")
    private_key_path = Path(require_env("GITHUB_APP_PRIVATE_KEY_PATH"))
    if not private_key_path.is_absolute():
        private_key_path = REPO_ROOT / private_key_path
    if not private_key_path.exists():
        raise SystemExit(f"GITHUB_APP_PRIVATE_KEY_PATH does not exist: {private_key_path}")
    private_key_pem = private_key_path.read_bytes()

    bedrock_model_id = require_env("BEDROCK_MODEL_ID")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    # Real live-run finding: a persistent regional Bedrock slowdown
    # exhausted every retry in one region outright. Comma-separated
    # real region names this same model is also confirmed available in
    # (verified earlier via `aws bedrock list-foundation-models` for
    # MiniMax M2.5: us-east-1/us-west-2/us-east-2) -- see
    # `bedrock_backend.call_converse_with_retry`'s own docstring for
    # the real fallback+sticky-region mechanism this feeds.
    fallback_regions = [r.strip() for r in os.environ.get("BEDROCK_FALLBACK_REGIONS", "").split(",") if r.strip()]
    repository = os.environ.get("LIVE_RUN_REPOSITORY", "chleiva/returnby")
    tenant_id = os.environ.get("LIVE_RUN_TENANT_ID", "live-run-tenant")
    max_turns_env = os.environ.get("BEDROCK_MAX_TURNS", "").strip()
    max_turns = int(max_turns_env) if max_turns_env else None
    owner, repo_name = repository.split("/", 1)

    # -- Imports of real sibling packages (after sys.path is set up above) --
    import boto3
    from run_registry import RegistryService
    from source_control.app_auth import InstallationTokenCache
    from source_control.github_client import AppCredentials, GitHubAppClient
    from source_control.git_ops import build_authenticated_remote_url, clone_or_update_mirror
    from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

    from orchestrator.core import Orchestrator, OrchestratorError
    from orchestrator.plan_artifact import PlanArtifactStore
    from orchestrator.progress import RunProgressStore
    from orchestrator.real_verification_runner import RealVerificationRunner
    from orchestrator.tool_use_bedrock_backend import BedrockBackendConfig, BedrockToolUseAgentBackend

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MIRRORS_DIR.mkdir(parents=True, exist_ok=True)
    mirror_path = MIRRORS_DIR / owner / repo_name

    # -- Real GitHub App identity + a real installation token ---------------
    _bootstrap_client = GitHubAppClient(credentials=AppCredentials(app_id=github_app_id, app_slug="", private_key_pem=private_key_pem))
    github_app_slug = _bootstrap_client.get_app_slug()
    print(f"[run] Real GitHub App slug: {github_app_slug!r}")

    installation = TenantInstallation(
        tenant_id=tenant_id,
        installation_id=github_installation_id,
        app_id=github_app_id,
        app_slug=github_app_slug,
        private_key_pem=private_key_pem,
        allowed_repositories=frozenset({repository}),
        mirror_root=MIRRORS_DIR,
    )
    registry_sc = InstallationRegistry()
    registry_sc.register(installation)
    source_control_service = SourceControlService(registry_sc)

    token_client = GitHubAppClient(credentials=AppCredentials(app_id=github_app_id, app_slug=github_app_slug, private_key_pem=private_key_pem))
    token_cache = InstallationTokenCache(token_client)

    def _authenticated_remote_url() -> str:
        token = token_cache.get_token(github_installation_id, tenant_id=tenant_id)
        return build_authenticated_remote_url(f"https://github.com/{repository}.git", token.token)

    reusing = branch_name is not None and worktree_path is not None
    if reusing:
        print(f"[run] Reusing existing branch+worktree {branch_name!r} at {worktree_path}")
    else:
        print(f"[run] Ensuring real local mirror of {repository} at {mirror_path} ...")
        clone_or_update_mirror(remote_url=_authenticated_remote_url(), local_path=mirror_path)

        branch_name = f"sdlc-auto/live-run-{uuid.uuid4().hex[:8]}"
        session_id = str(uuid.uuid4())
        print(f"[run] Creating real branch+worktree {branch_name!r} ...")
        worktree_result = source_control_service.create_branch_worktree(
            tenant_id=tenant_id, repository=repository, base_ref="main", branch_name=branch_name, session_id=session_id,
        )
        if worktree_result.get("outcome") != "ok":
            raise SystemExit(f"create_branch_worktree failed: {worktree_result}")
        worktree_path = Path(worktree_result["data"]["worktree_path"])
        print(f"[run] Real worktree ready at {worktree_path}")

    # -- Real Bedrock client(s) + the real tool-using implementation backend ---
    bedrock_client = boto3.client("bedrock-runtime", region_name=aws_region)
    fallback_clients = [(boto3.client("bedrock-runtime", region_name=r), r) for r in fallback_regions]
    if fallback_clients:
        print(f"[run] Real Bedrock fallback regions configured: {[r for _, r in fallback_clients]}")
    agent_backend_kwargs: dict = {"fallback_clients": fallback_clients} if fallback_clients else {}
    if max_turns is not None:
        agent_backend_kwargs["max_turns"] = max_turns
    agent_backend = BedrockToolUseAgentBackend(
        BedrockBackendConfig(model_id=bedrock_model_id, region_name=aws_region),
        bedrock_client,
        workspace_root=worktree_path,
        **agent_backend_kwargs,
    )

    # -- Real Registry + real plan/progress stores ---------------------------
    registry = RegistryService(str(DATA_DIR / "registry.db"))
    plan_store = PlanArtifactStore(DATA_DIR / "plans")
    progress_store = RunProgressStore(DATA_DIR / "progress")
    verification_runner = RealVerificationRunner(
        workspace_root=worktree_path, plan_store=plan_store, progress_store=progress_store
    )

    outcome_state: dict = {"pr_url": None}

    def _packaging_fn(orchestrator: Any, run: Any, tool_invoker: Any) -> None:
        """Runs at the real PACKAGING stage (core.py's own extension
        point, unmodified): push the real branch, open a real PR."""
        print(f"[run] Pushing real branch {branch_name!r} to GitHub ...")
        push = subprocess.run(
            ["git", "push", _authenticated_remote_url(), f"HEAD:refs/heads/{branch_name}"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if push.returncode != 0:
            raise OrchestratorError(f"git push failed: {push.stderr}")

        artifact = plan_store.load_latest(run.id) or {}
        pr_title = f"[SDLC Auto live run] {task_description[:72]}"
        jira_line = f"**Jira**: {real_jira_key}\n\n" if real_jira_key else ""
        pr_body = (
            f"Opened automatically by `deploy/run-worker/` "
            f"(SDLC Auto's Docker Compose deployment mode, Bedrock/{bedrock_model_id}).\n\n"
            f"{jira_line}"
            f"**Task**: {task_description}\n\n"
            f"**Plan outcomes**: {artifact.get('outcomes', '(n/a)')}\n"
        )
        pr_result = source_control_service.open_pr(
            tenant_id=tenant_id, repository=repository, head_branch=branch_name, base_branch="main",
            title=pr_title, description=pr_body,
        )
        if pr_result.get("outcome") == "ok":
            outcome_state["pr_url"] = pr_result["data"]["url"]
            print(f"[run] REAL PR OPENED: {pr_result['data']['url']}")
        else:
            print(f"[run] open_pr did not return a new PR: {pr_result}")

    orchestrator = Orchestrator(
        registry=registry,
        tenant_id=tenant_id,
        agent_backend=agent_backend,
        verification_runner=verification_runner,
        plan_store=plan_store,
        progress_store=progress_store,
        packaging_fn=_packaging_fn,
    )

    # See this module's own docstring for why jira_key is constructed
    # this way: it is the Registry's only free-text field today.
    jira_key_field = f"{real_jira_key}: {task_description}" if real_jira_key else task_description

    return Environment(
        orchestrator=orchestrator,
        plan_store=plan_store,
        branch_name=branch_name,
        worktree_path=worktree_path,
        tenant_id=tenant_id,
        repository=repository,
        task_description=task_description,
        real_jira_key=real_jira_key,
        jira_key_field=jira_key_field,
        outcome_state=outcome_state,
    )


def _default_autonomy_level(fallback: str) -> Any:
    """Real Section 12 autonomy levels (`services/gates.autonomy`),
    wired into the drive loop below -- previously built and tested by
    D9 but never actually consulted by the orchestrator's own drive
    loop (every pause always asked a human, regardless of level). An
    explicit `AUTONOMY_LEVEL` env var wins for either script; otherwise
    each entrypoint below passes its own sensible default as `fallback`
    (`run_once`/`live_run.py`: "L1" -- ask at every gate, a human is
    right there, unchanged from before this existed; `start_run_async`/
    `resume_paused_run_async`/`jira_poll_run.py`: "L3" -- an automatic,
    unattended trigger should not stop for a routine gate, only for a
    genuine Sec. 9.3 checkpoint anomaly, exactly as Sec. 12's own text
    already specifies for L3)."""
    from gates.autonomy import parse_level

    raw = os.environ.get("AUTONOMY_LEVEL", "").strip()
    return parse_level(raw) if raw else parse_level(fallback)


def drive(env: Environment, status: Any, *, resolve: Resolver, autonomy_level: Any | None = None) -> RunOutcome:
    """The one real pause-handling loop, shared by every caller. Calls
    `resolve(status, env)` once per pause: a real decision string keeps
    driving; `None` stops immediately and returns a `paused=True`
    outcome carrying everything (`run_id`, `env.branch_name`,
    `env.worktree_path`) needed to resume this exact run later.

    Before calling `resolve` at all for a *gate* pause (never for a
    checkpoint -- Sec. 12: checkpoints are a safety net orthogonal to
    the approval-gate cadence, never traded away by any autonomy
    level), checks `services/gates.autonomy`'s real, already-tested
    `requires_plan_approval_gate`/`requires_change_review_gate`
    against the configured level; if the level doesn't require this
    gate, auto-approves immediately -- no notification, no wait, no
    question asked. This is the real "only break the glass for a
    genuine anomaly" behavior."""
    from gates.autonomy import requires_change_review_gate, requires_plan_approval_gate

    level = autonomy_level if autonomy_level is not None else _default_autonomy_level("L1")

    while status.paused:
        auto_decision: str | None = None
        if status.pause_kind == "gate":
            if status.stage == "plan_approval_gate":
                artifact = env.plan_store.load_latest(status.run_id) or {}
                high_risk = bool(artifact.get("risk", {}).get("cross_cutting_or_high_risk", False))
                if not requires_plan_approval_gate(level, high_risk=high_risk):
                    auto_decision = "approve"
            elif status.stage == "change_review_gate":
                if not requires_change_review_gate(level, pulled_from_batch=False):
                    auto_decision = "approve"

        if auto_decision is not None:
            print(f"[run] Autonomy level {level.value}: {status.stage} not required -- auto-approving, no human asked.")
            decision = auto_decision
        else:
            decision = resolve(status, env)

        if decision is None:
            return RunOutcome(exit_code=2, run_id=status.run_id, final_stage=status.stage, paused=True, pause_kind=status.pause_kind)

        if status.pause_kind == "gate":
            if status.stage == "plan_approval_gate":
                status = env.orchestrator.approve_plan(status.run_id, decision=decision)
            elif status.stage == "change_review_gate":
                status = env.orchestrator.approve_change_review(status.run_id, decision=decision)
            else:
                raise SystemExit(f"unexpected gate stage {status.stage!r}")
        elif status.pause_kind == "checkpoint":
            status = env.orchestrator.resolve_checkpoint(status.run_id, decision=decision)
        else:
            raise SystemExit(f"unexpected pause_kind {status.pause_kind!r}")

        if decision in ("reject", "stop"):
            print(f"[run] Run abandoned (decision={decision!r}).")
            return RunOutcome(exit_code=1, run_id=status.run_id, final_stage=status.stage)

    print(f"\n[run] Run finished at stage: {status.stage}")
    return RunOutcome(exit_code=0, run_id=status.run_id, final_stage=status.stage, pr_url=env.outcome_state["pr_url"])


def run_once(*, task_description: str, real_jira_key: str | None = None) -> RunOutcome:
    """`live_run.py`'s entrypoint: build a fresh environment, start a
    real run, and drive it to completion through the interactive
    terminal gates -- unchanged behavior from before this module split
    `drive()` out."""
    env = _build_environment(task_description=task_description, real_jira_key=real_jira_key)
    print(f"[run] Starting a real run for task: {task_description!r}")
    status = env.orchestrator.start_run(
        jira_key=env.jira_key_field, repo=env.repository, branch=env.branch_name, trace_id=str(uuid.uuid4()),
    )
    return drive(env, status, resolve=_interactive_resolve, autonomy_level=_default_autonomy_level("L1"))


def start_run_async(*, task_description: str, real_jira_key: str, on_pause: Callable[[Any, Environment], None]) -> RunOutcome:
    """`jira_poll_run.py`'s entrypoint for a newly-picked-up story: build
    a fresh environment, start a real run, and drive it -- but every
    pause calls `on_pause(status, env)` (real side effects: notify Jira)
    and then always stops (never blocks on `input()`)."""
    env = _build_environment(task_description=task_description, real_jira_key=real_jira_key)
    print(f"[run] Starting a real run for task: {task_description!r}")
    status = env.orchestrator.start_run(
        jira_key=env.jira_key_field, repo=env.repository, branch=env.branch_name, trace_id=str(uuid.uuid4()),
    )

    def _async_resolve(status: Any, env: Environment) -> None:
        on_pause(status, env)
        return None

    return drive(env, status, resolve=_async_resolve, autonomy_level=_default_autonomy_level("L3"))


def resume_paused_run_async(
    *, run_id: str, real_jira_key: str, task_description: str, branch_name: str, worktree_path: str,
    pause_kind: str, stage: str, decision: str, on_pause: Callable[[Any, Environment], None],
) -> RunOutcome:
    """`jira_poll_run.py`'s entrypoint once a human has replied on the
    paused issue: rebuild the environment pointed at the *same*
    branch/worktree the original invocation used, and apply `decision`
    -- but only if the run is genuinely still sitting at the exact
    pause (`pause_kind`/`stage`) the caller recorded the human's reply
    against.

    Real bug this closes: a real, transient failure (e.g. a Bedrock
    timeout that exhausts its own retries) can happen *after*
    `approve_plan`/`resolve_checkpoint` already durably applied the
    decision and `_drive()` had already moved on to a later real stage
    -- `core.py`'s state machine has no in-between "half-applied"
    state, the decision either landed for real or the call never
    happened at all. The previous version always re-applied `decision`
    unconditionally on every retry, which would double-apply it to
    whatever pause `resume_run` now finds (an `OrchestratorError`, or
    worse, silently approving a *different*, later pause the human
    never actually saw). `resume_run` alone -- with no decision applied
    -- already re-drives the run exactly as far as it can go from its
    real, durable state; this only applies `decision` when that lands
    back on the *same* pause, and otherwise lets `drive()` below react
    to whatever the run is genuinely doing now (a brand new pause is
    just handled like any other -- `on_pause` notifies Jira about it;
    a real completion just returns as one)."""
    env = _build_environment(
        task_description=task_description, real_jira_key=real_jira_key,
        branch_name=branch_name, worktree_path=Path(worktree_path),
    )
    status = env.orchestrator.resume_run(run_id)

    if status.paused and status.pause_kind == pause_kind and status.stage == stage:
        if status.pause_kind == "gate":
            if status.stage == "plan_approval_gate":
                status = env.orchestrator.approve_plan(run_id, decision=decision)
            elif status.stage == "change_review_gate":
                status = env.orchestrator.approve_change_review(run_id, decision=decision)
            else:
                raise SystemExit(f"unexpected gate stage {status.stage!r}")
        else:  # checkpoint
            status = env.orchestrator.resolve_checkpoint(run_id, decision=decision)

        if decision in ("reject", "stop"):
            print(f"[run] Run abandoned (decision={decision!r}).")
            return RunOutcome(exit_code=1, run_id=status.run_id, final_stage=status.stage)
    elif status.paused:
        print(
            f"[run] {run_id}: already moved past the recorded pause ({pause_kind}/{stage}) on an earlier "
            f"attempt -- now at a new pause ({status.pause_kind}/{status.stage}); not re-applying {decision!r}."
        )
    # else: status.paused is False -- the run already ran all the way
    # to a real completion on an earlier, partially-crashed attempt
    # (decision applied, everything since succeeded before whatever
    # crashed this process). `drive()` below sees a non-paused status
    # and returns immediately with the real outcome -- nothing further
    # to do.

    def _async_resolve(status: Any, env: Environment) -> None:
        on_pause(status, env)
        return None

    return drive(env, status, resolve=_async_resolve, autonomy_level=_default_autonomy_level("L3"))
