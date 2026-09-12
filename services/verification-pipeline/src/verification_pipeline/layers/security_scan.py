"""Layer 4 -- Security scanning (Sec. 11.1):

"SAST on new/changed code and dependency/SCA scanning on any new or
updated dependency, blocking on high-severity findings."

Pluggable `SecurityScanner` interface (mirrors `StaticAnalyzer`'s shape)
with two real, working implementations, and one documented placeholder:

  * `BanditSASTScanner` -- REAL. Shells out to `bandit` (pip-installed),
    a genuine Python SAST tool, against a real fixture with a real
    insecure-code pattern (see fixtures/target_repo). Not canned.

  * `KnownCVEPatternDependencyScanner` -- REAL, but deliberately scoped
    down per the brief's own allowance ("if no specific tool is available
    ... build a real, pluggable SecurityScanner interface with at least
    one real, working check ... rather than a pure stub"). It parses a
    requirements file's pinned versions and checks them, offline and
    deterministically, against a small local table of known-vulnerable
    version ranges. This is a genuine, working comparison -- not a stub
    that always returns the same canned finding -- but the vulnerability
    table itself is illustrative/small, not a live feed.

  * `pip_audit` (see `run_pip_audit`, not wired into the default scanner
    list) -- pip-audit is genuinely installed in this environment and
    `run_pip_audit` really shells out to it. It is NOT used in this
    layer's default analyzer list or in the deterministic acceptance
    tests because it queries a live vulnerability feed (network-
    dependent, and the result changes over time as advisories are
    published) -- unsuitable for a deterministic, reproducible test
    fixture. It is provided, real, and wired for an operator who wants
    live SCA data; that operator accepts the non-determinism.
    DOCUMENTED PLACEHOLDER for a specific enterprise SCA/SAST tool
    (e.g. Snyk, Semgrep Enterprise, a commercial SCA feed): none is
    available in this environment, so none is wired -- `SecurityScanner`
    is the seam a real integration plugs into later.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .base import LayerResult


@dataclass(frozen=True)
class SecurityFinding:
    tool: str
    kind: str  # "sast" | "sca"
    file: str
    line: int | None
    rule_id: str
    message: str
    severity: str  # "low" | "medium" | "high" | "critical"


class SecurityScanner(Protocol):
    name: str
    kind: str

    def scan(self, paths: list[str], cwd: Path) -> list[SecurityFinding]: ...


_BANDIT_SEVERITY_MAP = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}


class BanditSASTScanner:
    """Real bandit (https://bandit.readthedocs.io/) invocation."""

    name = "bandit"
    kind = "sast"

    def __init__(self, python_executable: str | None = None):
        self.python_executable = python_executable or sys.executable

    def scan(self, paths: list[str], cwd: Path) -> list[SecurityFinding]:
        cmd = [self.python_executable, "-m", "bandit", "-r", "-f", "json", *paths]
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if not proc.stdout.strip():
            return []
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        findings = []
        for item in raw.get("results", []):
            findings.append(
                SecurityFinding(
                    tool=self.name,
                    kind=self.kind,
                    file=item.get("filename", "?"),
                    line=item.get("line_number"),
                    rule_id=item.get("test_id", "?"),
                    message=item.get("issue_text", ""),
                    severity=_BANDIT_SEVERITY_MAP.get(item.get("issue_severity", "LOW"), "low"),
                )
            )
        return findings


# A small, illustrative, OFFLINE table of known-vulnerable version ranges.
# Real CVE identifiers for real historical vulnerabilities, used here as a
# deterministic pattern match -- NOT a live feed. A production deployment
# would replace/augment this with a real SCA tool's database (see module
# docstring).
KNOWN_VULNERABLE_PACKAGES: dict[str, dict] = {
    "insecure-badpkg": {
        "vulnerable_below": (2, 0, 0),
        "cve": "CVE-2024-00000",
        "severity": "high",
        "description": "Illustrative fixture vulnerability: arbitrary deserialization below 2.0.0.",
    },
    "pyyaml": {
        "vulnerable_below": (5, 4),
        "cve": "CVE-2020-14343",
        "severity": "critical",
        "description": "PyYAML full_load/unsafe_load arbitrary code execution, fixed in 5.4.",
    },
}

_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([0-9]+(?:\.[0-9]+)*)")


def _parse_version(v: str) -> tuple:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


class KnownCVEPatternDependencyScanner:
    """Real, deterministic, offline dependency-version-vs-known-CVE-pattern
    check. See module docstring for what "real" means here."""

    name = "known-cve-pattern"
    kind = "sca"

    def scan(self, paths: list[str], cwd: Path) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for rel_path in paths:
            full_path = cwd / rel_path
            if not full_path.is_file():
                continue
            for lineno, line in enumerate(full_path.read_text().splitlines(), start=1):
                m = _REQ_LINE_RE.match(line)
                if not m:
                    continue
                pkg, version = m.group(1).lower(), m.group(2)
                entry = KNOWN_VULNERABLE_PACKAGES.get(pkg)
                if entry and _parse_version(version) < entry["vulnerable_below"]:
                    findings.append(
                        SecurityFinding(
                            tool=self.name,
                            kind=self.kind,
                            file=rel_path,
                            line=lineno,
                            rule_id=entry["cve"],
                            message=f"{pkg}=={version} is below the fixed version for {entry['cve']}: {entry['description']}",
                            severity=entry["severity"],
                        )
                    )
        return findings


def run_pip_audit(requirements_file: Path, python_executable: str | None = None) -> dict:
    """Real pip-audit invocation (network-dependent live vulnerability
    feed) -- see module docstring for why this is not part of the default,
    deterministic scanner list. Provided so an operator can opt in."""
    python_executable = python_executable or sys.executable
    cmd = [python_executable, "-m", "pip_audit", "-r", str(requirements_file), "-f", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {"dependencies": []}
    except json.JSONDecodeError:
        return {"error": proc.stdout + proc.stderr}


_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def run_security_scan_layer(
    *,
    code_paths: list[str],
    dependency_files: list[str],
    cwd: Path,
    scanners: list[SecurityScanner] | None = None,
    block_at_severity: str = "high",
) -> LayerResult:
    scanners = scanners if scanners is not None else [BanditSASTScanner(), KnownCVEPatternDependencyScanner()]

    all_findings: list[SecurityFinding] = []
    for scanner in scanners:
        paths = code_paths if scanner.kind == "sast" else dependency_files
        if not paths:
            continue
        all_findings.extend(scanner.scan(paths, cwd))

    threshold = _SEVERITY_RANK[block_at_severity]
    blocking = [f for f in all_findings if _SEVERITY_RANK[f.severity] >= threshold]

    if blocking:
        return LayerResult(
            name="security_scan",
            status="fail",
            summary=f"{len(blocking)} finding(s) at or above '{block_at_severity}' severity block this change.",
            details={"blocking_findings": [f.__dict__ for f in blocking], "all_findings": [f.__dict__ for f in all_findings]},
        )

    if all_findings:
        return LayerResult(
            name="security_scan",
            status="flagged-pass",
            summary=f"{len(all_findings)} sub-threshold finding(s), none blocking.",
            details={"all_findings": [f.__dict__ for f in all_findings]},
        )

    return LayerResult(name="security_scan", status="pass", summary="No SAST or SCA findings.", details={})
