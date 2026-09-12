"""Cross-cutting D10 check (Sec. 17.2 / Sec. 10.2 / Sec. 7.3): "Dependency
and license compliance: new dependencies and Skills are checked against
the organization's license policy and a vulnerability database before
being introduced" -- and the D10 brief's own cross-cutting wording:
"Dependency/Skill license and vulnerability checks actually block on a
policy violation rather than warn-and-continue."

D7 (verification-pipeline) owns the real implementation
(`layers/security_scan.py::run_security_scan_layer`, a real, deterministic,
offline known-CVE-pattern dependency scanner -- not a mock). This is a
light, targeted confirmation that a policy violation actually flips the
layer's status to a blocking outcome ("fail"), never merely a
warn-and-continue status, using the real scanner against a real
requirements file fixture with a genuinely known-vulnerable pinned
version.
"""
from __future__ import annotations

from verification_pipeline.layers.security_scan import (
    KnownCVEPatternDependencyScanner,
    run_security_scan_layer,
)


def test_a_known_vulnerable_pinned_dependency_blocks_not_warns(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pyyaml==5.3\n")  # below the 5.4 fixed version, per the scanner's own table

    result = run_security_scan_layer(
        code_paths=[], dependency_files=["requirements.txt"], cwd=tmp_path,
        scanners=[KnownCVEPatternDependencyScanner()],
    )
    assert result.status == "fail", (
        f"a critical-severity known-vulnerable dependency must BLOCK (status='fail'), "
        f"got status={result.status!r} -- this would be a real warn-and-continue regression"
    )
    assert "CVE-2020-14343" in str(result.details)


def test_a_clean_dependency_file_does_not_block(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pyyaml==6.0\nrequests==2.31.0\n")

    result = run_security_scan_layer(
        code_paths=[], dependency_files=["requirements.txt"], cwd=tmp_path,
        scanners=[KnownCVEPatternDependencyScanner()],
    )
    assert result.status != "fail"


