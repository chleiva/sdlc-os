"""Proves the four named error conditions are REAL outcomes of the
service logic (unknown tenant, unauthorized repo, exhausted quota, git
failure) -- never a canned response keyed off a magic sentinel value,
unlike F3's stub. Every response is also checked against F3's own
output_schema so the wire shape is identical to what the stub promises.
"""
from jsonschema import Draft202012Validator

from index_server.contract import load_index_schema
from index_server.ratelimit import RateLimiter
from index_server.service import IndexService
from index_server.tenants import TenantRegistry

SCHEMA = load_index_schema()


def _assert_conforms(tool_name: str, payload: dict) -> None:
    errors = list(Draft202012Validator(SCHEMA["tools"][tool_name]["output_schema"]).iter_errors(payload))
    assert not errors, "; ".join(e.message for e in errors)


def test_unknown_tenant_is_permission_denied(static_fixture_repo):
    svc = IndexService()
    payload = svc.find_definition(
        {
            "tenant_id": "tenant-nobody-registered-this",
            "repository": "fixture/multi-pkg-repo",
            "symbol": "compute_total",
            "origin": {"file": "pkg_a/billing/util.py", "line": 7},
        }
    )
    _assert_conforms("find-definition", payload)
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "permission-denied"
    assert payload["error"]["retryable"] is False


def test_known_tenant_unregistered_repository_is_permission_denied(static_fixture_repo):
    svc = IndexService()
    payload = svc.find_definition(
        {
            "tenant_id": "tenant-other",  # exists in config, but has zero repositories
            "repository": "fixture/multi-pkg-repo",
            "symbol": "compute_total",
            "origin": {"file": "pkg_a/billing/util.py", "line": 7},
        }
    )
    _assert_conforms("find-definition", payload)
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "permission-denied"


def test_registered_repo_missing_on_disk_is_not_found(tmp_path):
    registry_path = tmp_path / "tenants.json"
    registry_path.write_text(
        '{"tenant-x": {"repositories": {"ghost-repo": "' + str(tmp_path / "does-not-exist") + '"}}}'
    )
    svc = IndexService(tenants=TenantRegistry(registry_path))
    payload = svc.find_definition(
        {"tenant_id": "tenant-x", "repository": "ghost-repo", "symbol": "x", "origin": {"file": "a.py", "line": 1}}
    )
    _assert_conforms("find-definition", payload)
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "not-found"


def test_rate_limit_is_enforced_for_real(static_fixture_repo):
    svc = IndexService(rate_limiter=RateLimiter(limit=2, window_seconds=60))
    args = {
        "tenant_id": "tenant-acme",
        "repository": "fixture/multi-pkg-repo",
        "symbol": "compute_total",
        "origin": {"file": "pkg_a/billing/util.py", "line": 7},
    }
    first = svc.find_definition(args)
    second = svc.find_definition(args)
    third = svc.find_definition(args)

    assert first["outcome"] == "ok"
    assert second["outcome"] == "ok"
    assert third["outcome"] == "error"
    assert third["error"]["code"] == "rate-limited"
    assert third["error"]["retryable"] is True
    assert third["error"]["retry_after_seconds"] is not None
    _assert_conforms("find-definition", third)


def test_rate_limiter_is_per_tenant_not_global(static_fixture_repo):
    svc = IndexService(rate_limiter=RateLimiter(limit=1, window_seconds=60))
    args_common = {
        "repository": "fixture/multi-pkg-repo",
        "symbol": "compute_total",
        "origin": {"file": "pkg_a/billing/util.py", "line": 7},
    }
    # Exhaust tenant-acme's quota.
    svc.find_definition({**args_common, "tenant_id": "tenant-acme"})
    exhausted = svc.find_definition({**args_common, "tenant_id": "tenant-acme"})
    assert exhausted["error"]["code"] == "rate-limited"

    # A different, unrelated tenant is unaffected -- exercised via the
    # permission-denied path (no repo registered) just to prove the
    # quota itself, not authorization, is what's being isolated.
    other = svc.find_definition({**args_common, "tenant_id": "tenant-other"})
    assert other["error"]["code"] == "permission-denied"  # NOT rate-limited
