import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "multi_pkg_repo"

_GIT_ENV_BASE = {
    "GIT_AUTHOR_NAME": "Fixture Bot",
    "GIT_AUTHOR_EMAIL": "fixture-bot@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Bot",
    "GIT_COMMITTER_EMAIL": "fixture-bot@example.invalid",
}


def _git(cwd: Path, *args: str, env_extra: dict | None = None) -> None:
    import os

    env = {**os.environ, **_GIT_ENV_BASE, **(env_extra or {})}
    subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True, text=True)


@pytest.fixture
def static_fixture_repo() -> Path:
    """The checked-in fixture directory, with no git history. Good for
    everything that doesn't need change-frequency / commit-diffing."""
    return FIXTURE_REPO


@pytest.fixture
def git_fixture_repo(tmp_path):
    """A throwaway git-initialized COPY of the fixture repo, with a
    small, deliberately backdated commit history so change-frequency
    (git log --since=90.days) has something real to compute over.
    Returns (repo_root, commits) where commits is
    {"old": <sha>, "recent": <sha>} for tests that want to diff against
    a specific point.
    """
    import datetime

    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, dest)
    _git(dest, "init", "-q")
    _git(dest, "config", "user.name", "Fixture Bot")
    _git(dest, "config", "user.email", "fixture-bot@example.invalid")

    now = datetime.datetime.now(datetime.timezone.utc)
    old_date = (now - datetime.timedelta(days=200)).isoformat()
    recent_date = (now - datetime.timedelta(days=5)).isoformat()

    _git(dest, "add", "-A")
    _git(
        dest,
        "commit",
        "-q",
        "-m",
        "initial import",
        env_extra={"GIT_AUTHOR_DATE": old_date, "GIT_COMMITTER_DATE": old_date, "GIT_AUTHOR_NAME": "Ada Old"},
    )
    old_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dest), capture_output=True, text=True, check=True
    ).stdout.strip()

    # A recent, in-90-day-window touch to pkg_a/billing/util.py so
    # change-frequency has a non-zero commits_last_90d to report.
    util_path = dest / "pkg_a" / "billing" / "util.py"
    util_path.write_text(util_path.read_text() + "\n# touched for change-frequency test\n")
    _git(dest, "add", "-A")
    _git(
        dest,
        "commit",
        "-q",
        "-m",
        "touch util.py",
        env_extra={"GIT_AUTHOR_DATE": recent_date, "GIT_COMMITTER_DATE": recent_date, "GIT_AUTHOR_NAME": "Bea Recent"},
    )
    recent_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dest), capture_output=True, text=True, check=True
    ).stdout.strip()

    return dest, {"old": old_commit, "recent": recent_commit}
