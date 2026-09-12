"""Thin, shared subprocess wrapper around the real `tofu` CLI.

Nothing here fakes or mocks OpenTofu -- every call in this module shells
out to a real `tofu` binary (matching F1/D6's own convention: OpenTofu,
never the Terraform CLI, spec §14.2). It exists only so
`module_versioning.py` and `rollback.py` don't each re-implement
subprocess plumbing (timeouts, working-directory handling, capturing
stdout/stderr) separately.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class TofuResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_tofu(
    args: list[str],
    *,
    cwd: str | Path,
    tofu_bin: str = "tofu",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    extra_env: dict[str, str] | None = None,
) -> TofuResult:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [tofu_bin, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return TofuResult(args=tuple(args), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def init(cwd: str | Path, *, tofu_bin: str = "tofu", extra_env: dict[str, str] | None = None) -> TofuResult:
    return run_tofu(["init", "-input=false", "-no-color"], cwd=cwd, tofu_bin=tofu_bin, extra_env=extra_env)


def validate(cwd: str | Path, *, tofu_bin: str = "tofu") -> TofuResult:
    return run_tofu(["validate", "-no-color"], cwd=cwd, tofu_bin=tofu_bin)


def plan(
    cwd: str | Path,
    *,
    var_args: list[str] | None = None,
    tofu_bin: str = "tofu",
) -> TofuResult:
    return run_tofu(["plan", "-input=false", "-no-color", *(var_args or [])], cwd=cwd, tofu_bin=tofu_bin)


def apply(
    cwd: str | Path,
    *,
    var_args: list[str] | None = None,
    tofu_bin: str = "tofu",
) -> TofuResult:
    return run_tofu(
        ["apply", "-input=false", "-auto-approve", "-no-color", *(var_args or [])],
        cwd=cwd,
        tofu_bin=tofu_bin,
    )


def output(cwd: str | Path, *, tofu_bin: str = "tofu") -> TofuResult:
    return run_tofu(["output", "-json", "-no-color"], cwd=cwd, tofu_bin=tofu_bin)
