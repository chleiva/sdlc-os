from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORM_RELEASE_ROOT = Path(__file__).resolve().parents[1]
INFRA_ROOT = PLATFORM_RELEASE_ROOT / "infra"

# A shared `tofu` provider plugin cache, outside the repo, so the many
# `tofu init` calls this test suite makes (rollback test, module-
# versioning-gate tests) don't each re-download the same tiny
# `hashicorp/local` provider from scratch. Purely a speed optimization --
# every test still runs a real, uncached-by-us `tofu init`/`validate`/
# `plan`/`apply` subprocess; only the provider *binary* download is
# cached, the same way a developer's own machine would cache it.
_PLUGIN_CACHE_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "platform-release-tofu-plugin-cache"


@pytest.fixture(autouse=True, scope="session")
def _tofu_plugin_cache():
    _PLUGIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["TF_PLUGIN_CACHE_DIR"] = str(_PLUGIN_CACHE_DIR)
    yield


@pytest.fixture()
def infra_tree_copy(tmp_path: Path) -> Path:
    """A throwaway copy of this deliverable's own infra/ tree (modules/ +
    compositions/), so a real `tofu init`/`apply` run never writes state,
    `.terraform/`, or lock-file drift into the actual source tree --
    exactly the same "never apply-test in the tracked tree" discipline
    F1/D6 followed."""
    dest = tmp_path / "infra"
    shutil.copytree(INFRA_ROOT, dest)
    return dest


@pytest.fixture()
def platform_deployment_dir(infra_tree_copy: Path) -> Path:
    return infra_tree_copy / "compositions" / "platform-deployment"
