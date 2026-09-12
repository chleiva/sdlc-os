"""Monorepo scope resolution (spec Sec. 6.4): before deep searching,
resolve which package a target path belongs to."""
from index_server.engine.scope import all_packages, resolve_package


def test_resolve_package_for_file_in_pkg_a(static_fixture_repo):
    assert resolve_package(static_fixture_repo, "pkg_a/billing/util.py") == "pkg_a"


def test_resolve_package_for_file_in_pkg_b(static_fixture_repo):
    assert resolve_package(static_fixture_repo, "pkg_b/reporting/report.py") == "pkg_b"


def test_resolve_package_for_nested_directory_still_resolves_to_owning_package(static_fixture_repo):
    assert resolve_package(static_fixture_repo, "pkg_a/billing/tests/test_util.py") == "pkg_a"


def test_path_with_no_package_marker_resolves_to_none(static_fixture_repo):
    assert resolve_package(static_fixture_repo, "unowned/scratch.py") is None


def test_all_packages_enumerates_every_package_in_the_repo(static_fixture_repo):
    assert all_packages(static_fixture_repo) == ["pkg_a", "pkg_b"]
