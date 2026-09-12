"""tenant_id-driven scenario dispatch, shared by every stub server and by the
contract tests that exercise them.

tenant_id is a first-class field on every request across all four servers
(spec Sec. 14.13). This stub deliberately makes it do double duty as the
scenario selector too: a caller (or a contract test) picks which canned
outcome it gets purely by which tenant_id it sends, with no other special
flag or header. That keeps the mechanism identical across all 18 tools and
all four servers, and keeps "tenant_id is load-bearing" true even in a stub.

Any tenant_id not listed below gets the happy-path ("ok") response -- so any
ordinary-looking tenant_id (e.g. "tenant-acme") exercises the success path.
"""
from typing import Literal

Scenario = Literal[
    "ok",
    "empty",
    "not-found",
    "permission-denied",
    "rate-limited",
    "upstream-unavailable",
]

TENANT_OK = "tenant-acme"
TENANT_EMPTY = "tenant-empty"
TENANT_NOT_FOUND = "tenant-error-not-found"
TENANT_PERMISSION_DENIED = "tenant-error-permission-denied"
TENANT_RATE_LIMITED = "tenant-error-rate-limited"
TENANT_UPSTREAM_UNAVAILABLE = "tenant-error-upstream-unavailable"

DEFAULT_SCENARIO: Scenario = "ok"

_TENANT_SCENARIO_MAP: dict[str, Scenario] = {
    TENANT_EMPTY: "empty",
    TENANT_NOT_FOUND: "not-found",
    TENANT_PERMISSION_DENIED: "permission-denied",
    TENANT_RATE_LIMITED: "rate-limited",
    TENANT_UPSTREAM_UNAVAILABLE: "upstream-unavailable",
}

# All named error conditions the master spec requires (Sec. 7.6), in one
# place so servers and tests both iterate the same list rather than each
# hand-copying the four strings.
ERROR_CONDITIONS: tuple[str, ...] = (
    "not-found",
    "permission-denied",
    "rate-limited",
    "upstream-unavailable",
)

# Every named scenario a contract test should exercise per tool, mapped to
# the tenant_id that triggers it.
ALL_SCENARIO_TENANTS: dict[Scenario, str] = {
    "ok": TENANT_OK,
    "empty": TENANT_EMPTY,
    "not-found": TENANT_NOT_FOUND,
    "permission-denied": TENANT_PERMISSION_DENIED,
    "rate-limited": TENANT_RATE_LIMITED,
    "upstream-unavailable": TENANT_UPSTREAM_UNAVAILABLE,
}


def scenario_for(tenant_id: str) -> Scenario:
    return _TENANT_SCENARIO_MAP.get(tenant_id, DEFAULT_SCENARIO)
