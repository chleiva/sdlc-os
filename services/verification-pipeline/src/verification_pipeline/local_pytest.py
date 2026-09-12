"""Real pytest execution + JUnit-XML result parsing.

F3's CI contract (services/mcp-stubs/ci) only carries aggregate
tests.total/passed/failed counts (ci.schema.json's get-run-status-result)
-- there is no per-test identity in the wire contract. D7 orchestrates and
interprets CI results rather than replacing CI execution (per the D7
brief's "explicitly not in scope"), but distinguishing a specific
*pre-existing, unrelated* failing test from a new regression, and
distinguishing a flaky test from a genuine one, both require per-test
identity and the ability to rerun one test in isolation -- detail the
aggregate CI contract does not carry. This module supplies that detail by
running pytest directly (a real subprocess, real JUnit XML output, no
canned data) against whatever target repo/scope is under verification;
`ci_client.py` remains the integration point that proves D7 speaks F3's
real CI contract. See README.md's "spec ambiguity" section for this gap,
flagged for a human to reconcile against how the real org CI eventually
reports per-test detail.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

Outcome = str  # "passed" | "failed" | "error" | "skipped"


@dataclass(frozen=True)
class TestCaseResult:
    node_id: str
    outcome: Outcome
    message: str | None = None


@dataclass(frozen=True)
class PytestRunResult:
    total: int
    passed: int
    failed: int
    errored: int
    skipped: int
    cases: list[TestCaseResult]
    returncode: int
    stdout: str

    @property
    def failing_node_ids(self) -> list[str]:
        return [c.node_id for c in self.cases if c.outcome in ("failed", "error")]


def _parse_junit_xml(xml_path: Path) -> list[TestCaseResult]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))

    cases: list[TestCaseResult] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            file_attr = case.get("file")
            name = case.get("name")
            classname = case.get("classname", "")
            if file_attr:
                node_id = f"{file_attr}::{name}"
            else:
                node_id = f"{classname}::{name}"

            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                cases.append(TestCaseResult(node_id, "failed", failure.get("message")))
            elif error is not None:
                cases.append(TestCaseResult(node_id, "error", error.get("message")))
            elif skipped is not None:
                cases.append(TestCaseResult(node_id, "skipped", skipped.get("message")))
            else:
                cases.append(TestCaseResult(node_id, "passed", None))
    return cases


def run_pytest(
    targets: list[str],
    cwd: Path,
    python_executable: str | None = None,
    extra_args: list[str] | None = None,
) -> PytestRunResult:
    """Run pytest for real against `targets` (paths or node IDs, relative
    to `cwd`) and return parsed, per-test results. No mocking: this is a
    genuine subprocess invocation of pytest, exactly what a human running
    `pytest <targets>` locally would get.
    """
    python_executable = python_executable or sys.executable
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "results.xml"
        cmd = [
            python_executable,
            "-m",
            "pytest",
            *targets,
            f"--junitxml={xml_path}",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ]
        if extra_args:
            cmd.extend(extra_args)
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        cases = _parse_junit_xml(xml_path) if xml_path.exists() else []

    passed = sum(1 for c in cases if c.outcome == "passed")
    failed = sum(1 for c in cases if c.outcome == "failed")
    errored = sum(1 for c in cases if c.outcome == "error")
    skipped = sum(1 for c in cases if c.outcome == "skipped")
    return PytestRunResult(
        total=len(cases),
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        cases=cases,
        returncode=proc.returncode,
        stdout=proc.stdout + proc.stderr,
    )
