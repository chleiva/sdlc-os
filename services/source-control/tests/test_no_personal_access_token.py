"""D5 acceptance criterion: "No personal access token appears anywhere in
the implementation or its config." A structural/grep-based fitness test,
in the same spirit as F2's `test_no_direct_db_access.py`: walks the
actual source tree rather than trusting a comment or convention.

Checks, concretely:
  1. No literal PAT-shaped token or PAT-suggesting identifier/env var
     name appears anywhere in `src/`.
  2. The only `Authorization` header values this codebase ever
     constructs are `Bearer <jwt>` (App-level, JWT-signing path) or
     `Bearer <installation token>` (obtained exclusively through
     `InstallationTokenCache`/`GitHubAppClient.create_installation_token`)
     -- there is no second, parallel code path that builds an
     `Authorization` header from something else (e.g. an env var read
     directly into a header).
  3. `github_client.py` never imports/reads an env var that looks like a
     PAT source (`GITHUB_TOKEN`, `GH_TOKEN`, `PAT`, etc.).
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "source_control"

# Real GitHub PAT prefixes (classic + fine-grained) -- if a literal
# string matching one of these shapes ever showed up in source or
# config, that would itself be a smoking gun.
_PAT_LITERAL_RE = re.compile(r"\b(ghp_|github_pat_)[A-Za-z0-9_]{10,}\b")

# Identifier / env-var names that suggest a personal-access-token code
# path exists, distinct from this service's own installation-token
# vocabulary (which always says "installation" somewhere).
_PAT_NAME_RE = re.compile(
    r"\b(GITHUB_TOKEN|GH_TOKEN|PERSONAL_ACCESS_TOKEN|GITHUB_PAT|"
    r"personal_access_token|access_token_env|pat_token)\b"
)


def _all_source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_no_pat_shaped_literal_anywhere_in_source():
    offenders = []
    for path in _all_source_files():
        text = path.read_text()
        if _PAT_LITERAL_RE.search(text):
            offenders.append(str(path))
    assert offenders == [], f"PAT-shaped literal token found in: {offenders}"


def test_no_pat_suggestive_identifier_or_env_var_anywhere_in_source():
    offenders = []
    for path in _all_source_files():
        text = path.read_text()
        for m in _PAT_NAME_RE.finditer(text):
            offenders.append(f"{path}: {m.group(0)}")
    assert offenders == [], f"PAT-suggestive identifier found: {offenders}"


def test_authorization_header_is_constructed_in_exactly_one_module():
    """Every place that sets an `Authorization` header lives in
    github_client.py, and every value assigned to it is either the App
    JWT (`f"Bearer {jwt}"`) or an installation token
    (`f"Bearer {token.token}"`) -- never a bare env-var-sourced string."""
    offenders = []
    for path in _all_source_files():
        if path.name == "github_client.py":
            continue
        if "Authorization" in path.read_text():
            offenders.append(str(path))
    assert offenders == [], f"'Authorization' header referenced outside github_client.py: {offenders}"

    client_text = (SRC / "github_client.py").read_text()
    auth_header_assignments = re.findall(r'add_header\(\s*"Authorization"\s*,\s*([^)]+)\)', client_text)
    assert auth_header_assignments, "expected at least one Authorization header assignment"
    for expr in auth_header_assignments:
        assert "auth_header" in expr, f"unexpected Authorization header expression: {expr}"

    # And the only two call sites that ever produce that `auth_header`
    # value are the JWT path and the installation-token path.
    assert 'auth_header=f"Bearer {jwt}"' in client_text
    assert 'auth_header=f"Bearer {token.token}"' in client_text


def test_installation_token_cache_is_the_only_source_of_bearer_installation_tokens():
    """No module other than app_auth.py/github_client.py ever constructs
    an InstallationToken or reads a raw token string out of a response
    body -- app_auth.InstallationTokenCache is the sole point tokens are
    minted and handed out from."""
    offenders = []
    for path in _all_source_files():
        if path.name in ("app_auth.py", "github_client.py"):
            continue
        if "InstallationToken(" in path.read_text():
            offenders.append(str(path))
    assert offenders == [], f"InstallationToken constructed outside app_auth.py/github_client.py: {offenders}"


def test_no_env_var_is_read_anywhere_for_credential_material():
    """This service's credential material (App private key, per-tenant
    installation registration) is expected to arrive already resolved
    from the tenant's own KMS-wrapped secret store (spec Section 17.3) at
    the point of use -- not read directly out of process environment
    variables, which is exactly the shape a long-lived PAT/token would
    take in a naive implementation."""
    offenders = []
    for path in _all_source_files():
        text = path.read_text()
        if "os.environ" in text or "os.getenv" in text:
            offenders.append(str(path))
    assert offenders == [], (
        "credential/config material must not be read from process env vars in the "
        f"service implementation: {offenders}"
    )
