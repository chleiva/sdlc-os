from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from run_registry import RegistryService

from orchestrator.core import Orchestrator
from orchestrator.model_backend import ScriptedAgentBackend
from orchestrator.plan_artifact import PlanArtifactStore
from orchestrator.progress import RunProgressStore
from orchestrator.verification import ScriptedVerificationRunner


def make_orchestrator(
    *,
    registry,
    tenant_id,
    plan_store,
    progress_store,
    agent_backend=None,
    verification_runner=None,
    **kwargs,
) -> Orchestrator:
    return Orchestrator(
        registry=registry,
        tenant_id=tenant_id,
        agent_backend=agent_backend or ScriptedAgentBackend(),
        verification_runner=verification_runner or ScriptedVerificationRunner(),
        plan_store=plan_store,
        progress_store=progress_store,
        **kwargs,
    )


@pytest.fixture
def registry(tmp_path):
    svc = RegistryService(str(tmp_path / "registry.db"))
    yield svc
    svc.close()


@pytest.fixture
def tenant_id():
    return f"tenant-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def plan_store(tmp_path):
    return PlanArtifactStore(tmp_path / "plan_artifacts")


@pytest.fixture
def progress_store(tmp_path):
    return RunProgressStore(tmp_path / "progress")


@pytest.fixture
def scripted_backend():
    return ScriptedAgentBackend()


@pytest.fixture
def scripted_verifier():
    return ScriptedVerificationRunner()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def git_fixture_repo(tmp_path: Path) -> Path:
    """A real local git repository with one commit on `main` -- mirrors
    D5's own `git_fixture_repo` fixture (services/source-control/tests/conftest.py)
    so `worktree.py`'s tests exercise the identical technique against a
    real local repo, no GitHub account or network needed."""
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("# fixture\n")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "initial commit"], repo)
    return repo
