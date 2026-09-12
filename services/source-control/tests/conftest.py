from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from source_control.audit import AuditLogger
from source_control.github_client import AppCredentials, GitHubAppClient

from .mock_github_server import MockGitHubServer

APP_ID = "918273"
APP_SLUG = "sdlc-auto"


@pytest.fixture(scope="session")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_key, private_key.public_key(), private_pem


@pytest.fixture
def mock_github(rsa_keypair):
    _, public_key, _ = rsa_keypair
    server = MockGitHubServer(app_id=APP_ID, app_public_key=public_key)
    base_url = server.start()
    try:
        yield server, base_url
    finally:
        server.stop()


@pytest.fixture
def app_credentials(rsa_keypair):
    _, _, private_pem = rsa_keypair
    return AppCredentials(app_id=APP_ID, app_slug=APP_SLUG, private_key_pem=private_pem)


@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def github_client(mock_github, app_credentials, audit_logger):
    _, base_url = mock_github
    return GitHubAppClient(credentials=app_credentials, api_base_url=base_url, audit_logger=audit_logger)


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def git_fixture_repo(tmp_path: Path) -> Path:
    """A real local git repository with one commit on `main` -- the
    'already-cloned mirror' git_ops.py operates against. Used directly by
    tests, no GitHub account or network needed."""
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    _run(["init", "-b", "main"], repo)
    _run(["config", "user.email", "test@example.com"], repo)
    _run(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("# fixture\n")
    _run(["add", "README.md"], repo)
    _run(["commit", "-m", "initial commit"], repo)
    return repo
