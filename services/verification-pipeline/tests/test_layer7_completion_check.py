"""Layer 7 -- Cross-codebase completion check. Acceptance criterion: "A
diff that renames a symbol with an unreferenced caller outside the plan's
declared scope trips the risk checkpoint -- verified with a fixture."

Reuses D3's own multi-package fixture repo
(services/index-server/tests/fixtures/multi_pkg_repo, tenant "tenant-acme",
repository "fixture/multi-pkg-repo") via D3's REAL MCP index server
(index_client.py spawns services/index-server/src/index_server/server.py
as a real subprocess and speaks real MCP over stdio to it) -- exactly the
brief's "don't rebuild a parallel [fixture], reuse D3's" instruction.

`compute_total` is defined in pkg_a/billing/util.py and called from 5
real sites, 2 of them in pkg_b (outside pkg_a) -- see
services/index-server/tests/test_find_references_exhaustive.py for the
authoritative description of this fixture.
"""
from verification_pipeline.layers.completion_check import TouchedSymbol, run_completion_check_layer

TENANT = "tenant-acme"
REPOSITORY = "fixture/multi-pkg-repo"
SYMBOL = TouchedSymbol(name="compute_total", origin_file="pkg_a/billing/util.py", origin_line=7, change_kind="renamed")

# An implementer who (correctly, per a plan declaring pkg_a/** in scope)
# only opened files inside pkg_a while renaming compute_total.
OPENED_ONLY_PKG_A = {
    "pkg_a/billing/service.py",
    "pkg_a/billing/tests/test_util.py",
    "pkg_a/billing/util.py",
}

ALL_FIVE_REFERENCE_FILES = OPENED_ONLY_PKG_A | {
    "pkg_b/reporting/report.py",
    "pkg_b/reporting/legacy.py",
}


def test_rename_with_unopened_caller_outside_declared_scope_trips_checkpoint():
    result = run_completion_check_layer(
        tenant_id=TENANT,
        repository=REPOSITORY,
        touched_symbols=[SYMBOL],
        opened_files=OPENED_ONLY_PKG_A,
        declared_in_scope=["pkg_a/**"],
    )
    assert result.status == "checkpoint"
    assert result.blocks_human_review

    unresolved = result.details["unresolved_outside_declared_scope"]["compute_total"]
    unresolved_files = {r["file"] for r in unresolved}
    assert unresolved_files == {"pkg_b/reporting/report.py", "pkg_b/reporting/legacy.py"}


def test_every_reference_opened_passes():
    result = run_completion_check_layer(
        tenant_id=TENANT,
        repository=REPOSITORY,
        touched_symbols=[SYMBOL],
        opened_files=ALL_FIVE_REFERENCE_FILES,
        declared_in_scope=["pkg_a/**"],
    )
    assert result.status == "pass"
    assert not result.blocks_human_review


def test_search_is_exhaustive_across_the_whole_index_not_just_declared_scope():
    """Confirms the search actually reached pkg_b (outside declared
    scope) at all -- i.e. this is not scoped/filtered to pkg_a/** before
    the index is even queried."""
    result = run_completion_check_layer(
        tenant_id=TENANT,
        repository=REPOSITORY,
        touched_symbols=[SYMBOL],
        opened_files=set(),  # nothing opened -- everything should be unresolved
        declared_in_scope=["pkg_a/**"],
    )
    all_refs = result.details["all_references"]["compute_total"]
    files = {r["file"] for r in all_refs}
    assert files == ALL_FIVE_REFERENCE_FILES
