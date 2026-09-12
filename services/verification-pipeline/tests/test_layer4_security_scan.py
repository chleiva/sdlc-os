"""Layer 4 -- Security scanning: real bandit SAST + a real, deterministic,
offline dependency-vs-known-CVE-pattern SCA check (D7 brief: "at least
one real, working check ... rather than a pure stub")."""
from verification_pipeline.layers.security_scan import (
    BanditSASTScanner,
    KnownCVEPatternDependencyScanner,
    run_security_scan_layer,
)


def test_clean_code_and_deps_pass(target_repo):
    result = run_security_scan_layer(code_paths=["src/sample_pkg/billing.py"], dependency_files=[], cwd=target_repo)
    assert result.status == "pass"


def test_real_bandit_catches_real_shell_true_subprocess(target_repo):
    result = run_security_scan_layer(
        code_paths=["src/sample_pkg/insecure.py"],
        dependency_files=[],
        cwd=target_repo,
        scanners=[BanditSASTScanner()],
    )
    assert result.status == "fail"
    assert result.blocks_human_review
    rule_ids = {f["rule_id"] for f in result.details["blocking_findings"]}
    assert any(rid.startswith("B6") for rid in rule_ids)  # B602: subprocess w/ shell=True


def test_real_dependency_scanner_catches_known_vulnerable_pin(target_repo):
    result = run_security_scan_layer(
        code_paths=[],
        dependency_files=["requirements.txt"],
        cwd=target_repo,
        scanners=[KnownCVEPatternDependencyScanner()],
    )
    assert result.status == "fail"
    cves = {f["rule_id"] for f in result.details["blocking_findings"]}
    assert "CVE-2024-00000" in cves  # insecure-badpkg==1.0.0
    assert "CVE-2020-14343" in cves  # pyyaml==5.0


def test_dependency_scanner_does_not_flag_a_safe_pin(tmp_path):
    (tmp_path / "requirements.txt").write_text("pyyaml==6.0\nrequests==2.31.0\n")
    result = run_security_scan_layer(
        code_paths=[],
        dependency_files=["requirements.txt"],
        cwd=tmp_path,
        scanners=[KnownCVEPatternDependencyScanner()],
    )
    assert result.status == "pass"


def test_full_default_scanner_list_blocks_on_the_fixture_repo(target_repo):
    result = run_security_scan_layer(
        code_paths=["src/sample_pkg/insecure.py"], dependency_files=["requirements.txt"], cwd=target_repo
    )
    assert result.status == "fail"
