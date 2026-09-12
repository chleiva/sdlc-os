"""Convention profile (spec Sec. 6.1 / 6.2): the repository convention
file as the TOP layer, plus lint/format config and test-framework
detection as lower, inferred layers underneath it. The convention
file's content is never overridden by anything inferred here -- it is
read verbatim and returned as its own field, separate from the inferred
signals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONVENTION_FILE_CANDIDATES = ("CONVENTIONS.md", "AGENTS.md", "CONTRIBUTING.md")

LINT_FORMAT_MARKERS = (
    ".ruff.toml", "ruff.toml", ".flake8", ".pylintrc", "pyproject.toml",
    ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".prettierrc", ".prettierrc.json",
)

TEST_FRAMEWORK_MARKERS = {
    "pytest.ini": "pytest",
    "pyproject.toml": "pytest (via pyproject.toml, if configured)",
    "jest.config.js": "jest",
    "jest.config.ts": "jest",
    "vitest.config.ts": "vitest",
}


@dataclass
class ConventionProfile:
    convention_file_path: str | None
    convention_file_content: str | None  # top layer, read verbatim -- never overridden
    lint_format_configs: list[str] = field(default_factory=list)
    test_frameworks: list[str] = field(default_factory=list)
    directory_conventions: list[str] = field(default_factory=list)


def read_convention_profile(repo_root: Path) -> ConventionProfile:
    convention_path = None
    convention_content = None
    for name in CONVENTION_FILE_CANDIDATES:
        candidate = repo_root / name
        if candidate.is_file():
            convention_path = name
            convention_content = candidate.read_text(encoding="utf-8", errors="replace")
            break  # Sec. 6.1: one convention file at the repo root, first match wins.

    lint_format = sorted(
        {m for m in LINT_FORMAT_MARKERS if (repo_root / m).is_file() and m != "pyproject.toml"}
        | ({"pyproject.toml"} if (repo_root / "pyproject.toml").is_file() else set())
    )
    test_frameworks = sorted(
        {label for marker, label in TEST_FRAMEWORK_MARKERS.items() if (repo_root / marker).is_file()}
    )
    directory_conventions = sorted(
        d.name for d in repo_root.iterdir() if d.is_dir() and d.name in ("tests", "test", "src", "docs")
    )

    return ConventionProfile(
        convention_file_path=convention_path,
        convention_file_content=convention_content,
        lint_format_configs=lint_format,
        test_frameworks=test_frameworks,
        directory_conventions=directory_conventions,
    )
