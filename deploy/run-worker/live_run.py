#!/usr/bin/env python3
"""The real run worker: the piece that was missing entirely (CLAUDE.md's
"Known cross-deliverable gaps": "orchestrator has no real process
entrypoint"). This script is that entrypoint, for a first real,
manually-triggered live run -- job-dispatcher/Jira are deliberately not
wired yet (per the staged plan: prove the core loop for real first).

Real, for real, no shortcuts:
  - A real GitHub App JWT + installation-token exchange
    (`source_control.github_client.GitHubAppClient`), a real mirror
    clone/fetch, a real `git worktree`-isolated branch
    (`SourceControlService.create_branch_worktree`), a real `git push`,
    and a real PR (`SourceControlService.open_pr`).
  - A real Bedrock/MiniMax M2.5 call for planning
    (`BedrockAgentBackend`, via `BedrockToolUseAgentBackend`'s composed
    instance) and a real, multi-turn, tool-using implementation loop
    (`BedrockToolUseAgentBackend`) that actually writes files into the
    real worktree above.
  - A real `RealVerificationRunner` (existing_test_suite +
    static_analysis layers, for real, against the real worktree).
  - A real terminal-based human gate at both Section 9 gates (plan
    approval, change review) -- this is the one deliberately-scoped
    simplification: `GatesService`'s real approver-resolution needs a
    real Jira issue to resolve an approver from (`resolve_default_
    approver`), which does not exist yet in this manually-triggered
    pass. Wiring GatesService for real is the natural next step once
    Jira is wired (this script's own docstring section below says so
    again at the point it matters).

Usage:
    cd deploy/run-worker
    python3 live_run.py "Add a hello.py module with a greet(name) function
    that returns f'Hello, {name}!', and a matching test in test_hello.py"

Reads configuration from the repo root's `.env` (see `.env.example`):
GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY_PATH,
AWS_REGION, BEDROCK_MODEL_ID, plus LIVE_RUN_REPOSITORY /
LIVE_RUN_TENANT_ID (new, this script's own, documented in this
directory's README -- not part of the Docker Compose `.env.example`
surface, since this script is a manual live-run tool, not a compose
service).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data"
MIRRORS_DIR = Path(__file__).resolve().parent / "mirrors"

# Make every sibling service package importable without requiring the
# operator to have `pip install -e`'d each one into this exact venv --
# this script is a manual tool, not a packaged service; run it with a
# venv that already has each service's own dependencies installed (see
# this directory's README for the exact install sequence), and this
# just adds each package's real source to sys.path.
for _pkg in ("orchestrator", "source-control", "run-registry", "verification-pipeline"):
    sys.path.insert(0, str(REPO_ROOT / "services" / _pkg / "src"))


def _load_dotenv(path: Path) -> None:
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


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"live_run.py: required environment variable {name!r} is not set "
            f"(see this directory's README, and the repo root's .env)."
        )
    return value


def _confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit('usage: live_run.py "<task description>"')
    task_description = sys.argv[1]

    _load_dotenv(REPO_ROOT / ".env")

    github_app_id = _require_env("GITHUB_APP_ID")
    github_installation_id = _require_env("GITHUB_APP_INSTALLATION_ID")
    private_key_path = Path(_require_env("GITHUB_APP_PRIVATE_KEY_PATH"))
    if not private_key_path.is_absolute():
        private_key_path = REPO_ROOT / private_key_path
    if not private_key_path.exists():
        raise SystemExit(f"live_run.py: GITHUB_APP_PRIVATE_KEY_PATH does not exist: {private_key_path}")
    private_key_pem = private_key_path.read_bytes()

    bedrock_model_id = _require_env("BEDROCK_MODEL_ID")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    repository = os.environ.get("LIVE_RUN_REPOSITORY", "chleiva/returnby")
    tenant_id = os.environ.get("LIVE_RUN_TENANT_ID", "live-run-tenant")
    # Optional override of BedrockToolUseAgentBackend's per-subtask
    # tool-calling turn cap (default 40 -- see that class's own docstring
    # for why this is deliberately bounded, not unlimited). Exposed here,
    # not hardcoded, since the right value is genuinely task-dependent
    # and this is a manual live-run tool meant for exactly that kind of
    # tuning without a code edit.
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
    # Real bug found on this repo's first real `open_pr` call: app_slug
    # was hardcoded to "" here on the theory that it was "only used for
    # audit-log actor display" -- but audit.bot_actor actually *requires*
    # a well-formed, non-empty slug and raises otherwise. Fixed by
    # bootstrapping the real slug from GitHub itself (GET /app,
    # GitHubAppClient.get_app_slug) rather than guessing or hand-copying
    # it into an env var -- a throwaway client with an empty slug is
    # enough to make that one call, since it never needs its own slug.
    _bootstrap_client = GitHubAppClient(credentials=AppCredentials(app_id=github_app_id, app_slug="", private_key_pem=private_key_pem))
    github_app_slug = _bootstrap_client.get_app_slug()
    print(f"[live_run] Real GitHub App slug: {github_app_slug!r}")

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

    print(f"[live_run] Ensuring real local mirror of {repository} at {mirror_path} ...")
    clone_or_update_mirror(remote_url=_authenticated_remote_url(), local_path=mirror_path)

    # -- Real branch + real git-worktree isolation ---------------------------
    branch_name = f"sdlc-auto/live-run-{uuid.uuid4().hex[:8]}"
    session_id = str(uuid.uuid4())
    print(f"[live_run] Creating real branch+worktree {branch_name!r} ...")
    worktree_result = source_control_service.create_branch_worktree(
        tenant_id=tenant_id, repository=repository, base_ref="main", branch_name=branch_name, session_id=session_id,
    )
    if worktree_result.get("outcome") != "ok":
        raise SystemExit(f"live_run.py: create_branch_worktree failed: {worktree_result}")
    worktree_path = Path(worktree_result["data"]["worktree_path"])
    print(f"[live_run] Real worktree ready at {worktree_path}")

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

    def _packaging_fn(orchestrator: Orchestrator, run, tool_invoker) -> None:
        """Runs at the real PACKAGING stage (core.py's own extension
        point, unmodified): push the real branch, open a real PR."""
        print(f"[live_run] Pushing real branch {branch_name!r} to GitHub ...")
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
        pr_body = (
            f"Opened automatically by `deploy/run-worker/live_run.py` "
            f"(SDLC Auto's Docker Compose deployment mode, Bedrock/{bedrock_model_id}).\n\n"
            f"**Task**: {task_description}\n\n"
            f"**Plan outcomes**: {artifact.get('outcomes', '(n/a)')}\n"
        )
        pr_result = source_control_service.open_pr(
            tenant_id=tenant_id, repository=repository, head_branch=branch_name, base_branch="main",
            title=pr_title, description=pr_body,
        )
        if pr_result.get("outcome") == "ok":
            print(f"[live_run] REAL PR OPENED: {pr_result['data']['url']}")
        else:
            print(f"[live_run] open_pr did not return a new PR: {pr_result}")

    orchestrator = Orchestrator(
        registry=registry,
        tenant_id=tenant_id,
        agent_backend=agent_backend,
        verification_runner=verification_runner,
        plan_store=plan_store,
        progress_store=progress_store,
        packaging_fn=_packaging_fn,
    )

    print(f"[live_run] Starting a real run for task: {task_description!r}")
    # NOTE: `jira_key` is repurposed here to carry the free-text task
    # description -- `core.py`'s `_run_context` has no dedicated
    # task-description field today (a real, disclosed gap: in the real
    # system this would come from the Jira ticket body via `research_fn`,
    # which is not wired to enrich run_context either -- see this
    # directory's README). This is the pragmatic way to get real task
    # content to the model for this first live run without changing
    # `core.py`.
    status = orchestrator.start_run(
        jira_key=task_description, repo=repository, branch=branch_name, trace_id=str(uuid.uuid4()),
    )

    while status.paused:
        if status.pause_kind == "gate":
            if status.stage == "plan_approval_gate":
                artifact = plan_store.load_latest(status.run_id)
                print("\n=== REAL PLAN, awaiting your real approval ===")
                print(f"outcomes: {artifact.get('outcomes')}")
                print(f"story_size: {artifact.get('risk', {}).get('story_size')}")
                print(f"subtasks: {[s['description'] for s in artifact.get('subtask_graph', {}).get('subtasks', [])]}")
                if _confirm("Approve this plan?"):
                    status = orchestrator.approve_plan(status.run_id, decision="approve")
                else:
                    status = orchestrator.approve_plan(status.run_id, decision="reject")
                    print("[live_run] Plan rejected; run abandoned.")
                    return 1
            elif status.stage == "change_review_gate":
                print("\n=== REAL CHANGE, verification passed, awaiting your real review ===")
                if _confirm("Approve this change for packaging (real PR)?"):
                    status = orchestrator.approve_change_review(status.run_id, decision="approve")
                else:
                    status = orchestrator.approve_change_review(status.run_id, decision="reject")
                    print("[live_run] Change rejected; run abandoned.")
                    return 1
            else:
                raise SystemExit(f"live_run.py: unexpected gate stage {status.stage!r}")
        elif status.pause_kind == "checkpoint":
            elicitation: Elicitation = status.checkpoint
            print(f"\n=== REAL CHECKPOINT ({elicitation.trigger}) === {elicitation.reason}")
            if _confirm("Continue?"):
                status = orchestrator.resolve_checkpoint(status.run_id, decision="continue")
            else:
                status = orchestrator.resolve_checkpoint(status.run_id, decision="stop")
                print("[live_run] Checkpoint not cleared; run abandoned.")
                return 1
        else:
            raise SystemExit(f"live_run.py: unexpected pause_kind {status.pause_kind!r}")

    print(f"\n[live_run] Run finished at stage: {status.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
