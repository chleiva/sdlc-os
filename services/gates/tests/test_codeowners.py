"""Change-review's CODEOWNERS requirement, backed by D3's real
ownership-parsing engine (index_server.engine.repo_index.RepoIndex),
never a reimplementation of CODEOWNERS matching."""
from __future__ import annotations

from gates.codeowners import CodeownersResolver


def _resolver(codeowners_repo):
    return CodeownersResolver(repo_root_resolver=lambda repo: codeowners_repo)


def test_requirement_covers_every_touched_file(codeowners_repo):
    resolver = _resolver(codeowners_repo)
    req = resolver.requirement_for("acme/app", ["billing/service.py", "billing/util.py", "reporting/report.py"])
    assert set(req.touched_files) == {"billing/service.py", "billing/util.py", "reporting/report.py"}
    assert req.owners_by_file["billing/service.py"] == frozenset({"@billing-team"})
    # more specific, later CODEOWNERS rule wins for util.py
    assert req.owners_by_file["billing/util.py"] == frozenset({"@billing-team", "@jane-doe"})
    assert req.owners_by_file["reporting/report.py"] == frozenset({"@reporting-team"})


def test_is_satisfied_by_when_reviewer_owns_every_touched_file(codeowners_repo):
    resolver = _resolver(codeowners_repo)
    req = resolver.requirement_for("acme/app", ["billing/service.py", "billing/util.py"])
    # @billing-team owns both: the general billing/*.py rule for
    # service.py, and it's also still listed on util.py's more specific
    # rule.
    assert req.is_satisfied_by(frozenset({"@billing-team"})) is True
    # @jane-doe is only on util.py's specific rule, not on service.py's
    # -- does not satisfy both files.
    assert req.is_satisfied_by(frozenset({"@jane-doe"})) is False


def test_not_satisfied_when_reviewer_missing_for_one_file(codeowners_repo):
    resolver = _resolver(codeowners_repo)
    req = resolver.requirement_for("acme/app", ["billing/service.py", "reporting/report.py"])
    # @jane-doe only appears on the util.py-specific rule, not on billing/*.py
    # generally or on reporting/*.py -- does not satisfy either file here.
    assert req.is_satisfied_by(frozenset({"@jane-doe"})) is False
    assert set(req.unsatisfied_files(frozenset({"@jane-doe"}))) == {"billing/service.py", "reporting/report.py"}
    # @billing-team satisfies the billing file but not the reporting one.
    assert req.is_satisfied_by(frozenset({"@billing-team"})) is False
    assert req.unsatisfied_files(frozenset({"@billing-team"})) == ("reporting/report.py",)


def test_unowned_file_can_never_be_satisfied(codeowners_repo):
    resolver = _resolver(codeowners_repo)
    req = resolver.requirement_for("acme/app", ["unowned/scratch.py"])
    assert req.owners_by_file["unowned/scratch.py"] == frozenset()
    # No reviewer, however broadly privileged, satisfies a file with no
    # CODEOWNERS entry at all -- an empty owners set can never intersect
    # with any non-empty reviewer-handles set.
    assert req.is_satisfied_by(frozenset({"@billing-team", "@jane-doe", "@reporting-team"})) is False
