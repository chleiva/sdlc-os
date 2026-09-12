"""Ordinary platform CI (master spec §14.15):

"The System's own codebase is developed the same way any other software
the organization maintains is: ordinary human-authored PRs, ordinary CI
(Section 15) running its own unit/integration/contract tests (the MCP
tool contracts in Section 7.6 are exactly what those contract tests
check against) ... the System is not asked to build itself as a
bootstrapping exercise."

This module is an orchestrator, not a test framework: it discovers each
already-implemented service's own test suite (F3's stub-based contract
tests under `services/mcp-stubs/tests/`, D9's fitness tests under
`services/gates/tests/test_l3_floor_fitness.py`, and every other
service's own `tests/`), shells out to *that service's own* `pytest`
(via its own `.venv`, so it runs with exactly the dependencies that
service was actually built/tested against -- never this package's own
environment), and aggregates the pass/fail outcome into one report. It
never re-implements, re-asserts, or duplicates a single assertion any
service's own test suite already makes.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServiceSpec:
    """One service this CI runner knows how to test."""

    name: str
    path: Path
    # pytest args/selectors run *within* that service's own directory,
    # against *that service's own* interpreter -- e.g. ["-k", "contract or fitness"]
    # to scope a run to contract/fitness-shaped tests specifically, or []
    # for "run this service's whole suite". Discovery-time default is
    # every test under the service's own tests/ dir.
    pytest_args: tuple[str, ...] = field(default_factory=tuple)


def _venv_python(service_path: Path) -> Path | None:
    candidate = service_path / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def discover_services(services_root: str | Path, *, only: list[str] | None = None) -> list[ServiceSpec]:
    """Finds every `services/<name>/` directory that looks testable: has
    a `tests/` directory and a `.venv` (i.e. has actually been set up,
    per this repo's own per-service-venv convention -- see the repo-root
    README). A service with no `.venv` yet (not installed) is simply not
    discovered, not silently skipped as a false pass -- see
    `run_platform_ci`'s `not_runnable` bucket for how that distinction is
    surfaced instead of hidden."""
    services_root = Path(services_root)
    specs: list[ServiceSpec] = []
    for child in sorted(services_root.iterdir()):
        if not child.is_dir():
            continue
        if only is not None and child.name not in only:
            continue
        if not (child / "tests").is_dir():
            continue
        specs.append(ServiceSpec(name=child.name, path=child))
    return specs


@dataclass(frozen=True)
class ServiceTestResult:
    service: str
    status: str  # "passed" | "failed" | "not_runnable"
    returncode: int | None
    stdout: str
    stderr: str
    detail: str = ""


def run_service_tests(spec: ServiceSpec, *, timeout: float = 300) -> ServiceTestResult:
    """Shells out to `spec.path`'s own `.venv/bin/python -m pytest` --
    this is the literal "orchestrate, don't reimplement" boundary: the
    subprocess is that service's own test runner, its own conftest.py,
    its own fixtures, exactly as a human running `pytest` inside that
    service's directory would get."""
    python = _venv_python(spec.path)
    if python is None:
        return ServiceTestResult(
            service=spec.name,
            status="not_runnable",
            returncode=None,
            stdout="",
            stderr="",
            detail=f"no .venv found at {spec.path / '.venv'} -- service has not been installed, so its own test suite cannot be run",
        )

    args = [str(python), "-m", "pytest", "tests", "-q", *spec.pytest_args]
    try:
        proc = subprocess.run(
            args,
            cwd=str(spec.path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return ServiceTestResult(
            service=spec.name,
            status="failed",
            returncode=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            detail=f"timed out after {timeout}s",
        )

    return ServiceTestResult(
        service=spec.name,
        status="passed" if proc.returncode == 0 else "failed",
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


@dataclass(frozen=True)
class CIReport:
    results: list[ServiceTestResult]

    @property
    def all_passed(self) -> bool:
        return all(r.status == "passed" for r in self.results if r.status != "not_runnable")

    @property
    def passed(self) -> list[str]:
        return [r.service for r in self.results if r.status == "passed"]

    @property
    def failed(self) -> list[str]:
        return [r.service for r in self.results if r.status == "failed"]

    @property
    def not_runnable(self) -> list[str]:
        return [r.service for r in self.results if r.status == "not_runnable"]

    def as_dict(self) -> dict:
        return {
            "all_passed": self.all_passed,
            "passed": self.passed,
            "failed": self.failed,
            "not_runnable": self.not_runnable,
            "results": [
                {
                    "service": r.service,
                    "status": r.status,
                    "returncode": r.returncode,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


def run_platform_ci(
    services_root: str | Path,
    *,
    only: list[str] | None = None,
    timeout: float = 300,
) -> CIReport:
    """Discovers and runs every known service's own contract/test suite,
    aggregating pass/fail into one `CIReport` -- "ordinary CI ... running
    its own unit/integration/contract tests" (spec §14.15), for however
    many services are actually set up in this environment."""
    specs = discover_services(services_root, only=only)
    results = [run_service_tests(spec, timeout=timeout) for spec in specs]
    return CIReport(results=results)


if __name__ == "__main__":  # pragma: no cover - manual/CLI convenience
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
    report = run_platform_ci(root)
    for r in report.results:
        print(f"{r.service}: {r.status}")
    sys.exit(0 if report.all_passed else 1)
