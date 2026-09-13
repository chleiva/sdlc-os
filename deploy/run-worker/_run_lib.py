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
`RealVerificationRunner`; a real terminal-based human gate at both
Section 9 gates (see `live_run.py`'s own original docstring for why
`GatesService`'s real approver-resolution isn't wired here yet).

**`jira_key` field, honestly**: `core.py`'s `start_run`/`_run_context`
have exactly one free-text field (`jira_key`) -- there is no separate
task-description field in the Registry's `Run` model at all (a real,
disclosed gap, not something this module invents a workaround around
silently). `run_once` below embeds `real_jira_key` (when the caller has
one -- `jira_poll_run.py` does, `live_run.py`'s plain CLI usage doesn't)
as a `"<KEY>: <task text>"` prefix, so a Jira-triggered run's real issue
key stays inspectable in the Registry rather than being replaced
entirely by prose, while a manually-typed task still works exactly as
before.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data"
MIRRORS_DIR = Path(__file__).resolve().parent / "mirrors"

# Make every sibling service package importable without requiring the
# operator to have `pip install -e`'d each one into this exact venv --
# these are manual tools, not packaged services; run with a venv that
# already has each service's own dependencies installed (see this
# directory's README for the exact install sequence), and this just
# adds each package's real source to sys.path.
for _pkg in ("orchestrator", "source-control", "run-registry", "verification-pipeline", "issue-tracker", "kms-boundary"):
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
    needs `.exit_code`."""

    exit_code: int
    run_id: str | None = None
    final_stage: str | None = None
    pr_url: str | None = None


def run_once(*, task_description: str, real_jira_key: str | None = None) -> RunOutcome:
    """Build a real Orchestrator for one task, start a real run, and
    drive it to completion through the terminal-based gates -- the
    exact logic `live_run.py`'s `main()` originally had, unchanged in
    behavior, now shared with `jira_poll_run.py`."""
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

    from orchestrator.core import Elicitation, Orchestrator, OrchestratorError
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

    print(f"[run] Ensuring real local mirror of {repository} at {mirror_path} ...")
    clone_or_update_mirror(remote_url=_authenticated_remote_url(), local_path=mirror_path)

    # -- Real branch + real git-worktree isolation ---------------------------
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

    # -- Real Bedrock client + the real tool-using implementation backend ---
    bedrock_client = boto3.client("bedrock-runtime", region_name=aws_region)
    agent_backend_kwargs: dict = {}
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

    def _packaging_fn(orchestrator: Orchestrator, run, tool_invoker) -> None:
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

    print(f"[run] Starting a real run for task: {task_description!r}")
    status = orchestrator.start_run(
        jira_key=jira_key_field, repo=repository, branch=branch_name, trace_id=str(uuid.uuid4()),
    )

    while status.paused:
        if status.pause_kind == "gate":
            if status.stage == "plan_approval_gate":
                artifact = plan_store.load_latest(status.run_id)
                print("\n=== REAL PLAN, awaiting your real approval ===")
                print(f"outcomes: {artifact.get('outcomes')}")
                print(f"story_size: {artifact.get('risk', {}).get('story_size')}")
                print(f"subtasks: {[s['description'] for s in artifact.get('subtask_graph', {}).get('subtasks', [])]}")
                if confirm("Approve this plan?"):
                    status = orchestrator.approve_plan(status.run_id, decision="approve")
                else:
                    status = orchestrator.approve_plan(status.run_id, decision="reject")
                    print("[run] Plan rejected; run abandoned.")
                    return RunOutcome(exit_code=1, run_id=status.run_id, final_stage=status.stage)
            elif status.stage == "change_review_gate":
                print("\n=== REAL CHANGE, verification passed, awaiting your real review ===")
                if confirm("Approve this change for packaging (real PR)?"):
                    status = orchestrator.approve_change_review(status.run_id, decision="approve")
                else:
                    status = orchestrator.approve_change_review(status.run_id, decision="reject")
                    print("[run] Change rejected; run abandoned.")
                    return RunOutcome(exit_code=1, run_id=status.run_id, final_stage=status.stage)
            else:
                raise SystemExit(f"unexpected gate stage {status.stage!r}")
        elif status.pause_kind == "checkpoint":
            elicitation: Elicitation = status.checkpoint
            print(f"\n=== REAL CHECKPOINT ({elicitation.trigger}) === {elicitation.reason}")
            if confirm("Continue?"):
                status = orchestrator.resolve_checkpoint(status.run_id, decision="continue")
            else:
                status = orchestrator.resolve_checkpoint(status.run_id, decision="stop")
                print("[run] Checkpoint not cleared; run abandoned.")
                return RunOutcome(exit_code=1, run_id=status.run_id, final_stage=status.stage)
        else:
            raise SystemExit(f"unexpected pause_kind {status.pause_kind!r}")

    print(f"\n[run] Run finished at stage: {status.stage}")
    return RunOutcome(exit_code=0, run_id=status.run_id, final_stage=status.stage, pr_url=outcome_state["pr_url"])
