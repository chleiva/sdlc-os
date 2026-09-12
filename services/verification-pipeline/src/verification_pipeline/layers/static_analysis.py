"""Layer 3 -- Static analysis (Sec. 11.1):

"linting, type-checking, and the repository's existing convention profile
enforced automatically."

Pluggable per-language via the `StaticAnalyzer` protocol so a repo with a
different stack (e.g. TypeScript) can swap in its own analyzer without
touching the layer/pipeline code. Two real, working analyzers are wired
for Python (this repo's stack per CLAUDE.md's project-wide convention):
`RuffLinter` and `MypyTypeChecker`, both real subprocess calls to real,
pip-installed tools -- not stubs, not canned findings.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .base import LayerResult


@dataclass(frozen=True)
class Finding:
    tool: str
    file: str
    line: int | None
    code: str
    message: str
    severity: str = "error"


class StaticAnalyzer(Protocol):
    name: str

    def run(self, paths: list[str], cwd: Path) -> list[Finding]: ...


class RuffLinter:
    """Real ruff invocation (pip-installed, https://docs.astral.sh/ruff/).
    Ruff is this project's convention-profile linter of choice for Python
    (fast, single binary, no network calls at lint time)."""

    name = "ruff"

    def __init__(self, python_executable: str | None = None):
        self.python_executable = python_executable or sys.executable

    def run(self, paths: list[str], cwd: Path) -> list[Finding]:
        cmd = [self.python_executable, "-m", "ruff", "check", "--output-format=json", *paths]
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if not proc.stdout.strip():
            return []
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return [Finding(self.name, "?", None, "ruff-output-error", proc.stdout + proc.stderr)]
        findings = []
        for item in raw:
            findings.append(
                Finding(
                    tool=self.name,
                    file=item.get("filename", "?"),
                    line=(item.get("location") or {}).get("row"),
                    code=item.get("code") or "?",
                    message=item.get("message", ""),
                )
            )
        return findings


class MypyTypeChecker:
    """Real mypy invocation (pip-installed). Uses mypy's own machine-
    readable output (one diagnostic per line: file:line: severity: msg
    [code])."""

    name = "mypy"

    def __init__(self, python_executable: str | None = None):
        self.python_executable = python_executable or sys.executable

    def run(self, paths: list[str], cwd: Path) -> list[Finding]:
        cmd = [
            self.python_executable,
            "-m",
            "mypy",
            "--no-error-summary",
            "--show-error-codes",
            "--no-incremental",
            *paths,
        ]
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        findings = []
        for line in proc.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            file_, lineno, severity, rest = parts
            severity = severity.strip()
            if severity not in ("error", "warning"):
                continue
            code = "mypy"
            message = rest.strip()
            if message.endswith("]") and "[" in message:
                message, _, code_part = message.rpartition("[")
                code = code_part.rstrip("]")
                message = message.strip()
            findings.append(
                Finding(
                    tool=self.name,
                    file=file_.strip(),
                    line=int(lineno) if lineno.strip().isdigit() else None,
                    code=code,
                    message=message,
                    severity=severity,
                )
            )
        return findings


def run_static_analysis_layer(
    *, paths: list[str], cwd: Path, analyzers: list[StaticAnalyzer] | None = None
) -> LayerResult:
    analyzers = analyzers if analyzers is not None else [RuffLinter(), MypyTypeChecker()]
    all_findings: list[Finding] = []
    for analyzer in analyzers:
        all_findings.extend(analyzer.run(paths, cwd))

    if all_findings:
        by_tool: dict[str, int] = {}
        for f in all_findings:
            by_tool[f.tool] = by_tool.get(f.tool, 0) + 1
        return LayerResult(
            name="static_analysis",
            status="fail",
            summary=f"{len(all_findings)} static-analysis finding(s): {by_tool}.",
            details={"findings": [f.__dict__ for f in all_findings]},
        )

    return LayerResult(
        name="static_analysis",
        status="pass",
        summary=f"No findings from {[a.name for a in analyzers]}.",
        details={},
    )
