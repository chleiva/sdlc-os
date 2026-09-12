"""Layer 6 -- Behavioral/regression check (Sec. 11.1):

"for services with existing performance or contract baselines, a
lightweight check that the change does not regress them."

Deliberately measures a deterministic *operation count* rather than
wall-clock time: real code is executed for a real measurement, but the
metric itself does not depend on machine load/timing noise, so the test
proving regression-detection is reproducible rather than flaky by
construction (wall-clock benchmarking would undermine the very
flaky-vs-genuine distinction Sec. 11.3 asks D7 to get right elsewhere).

Where no baseline is recorded for a given scope, the layer is `skipped`
(not silently treated as a pass, and not a failure either) -- Sec. 11.1
only requires this check "where applicable".
"""
from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .base import LayerResult


@dataclass(frozen=True)
class Baseline:
    metric: str
    value: float
    direction: str  # "lower_is_better" | "higher_is_better"
    tolerance_pct: float


def load_baseline(path: Path) -> Baseline:
    data = json.loads(path.read_text())
    return Baseline(
        metric=data["metric"],
        value=float(data["value"]),
        direction=data["direction"],
        tolerance_pct=float(data["tolerance_pct"]),
    )


def _load_callable(module_path: Path, attr: str) -> Callable:
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, attr)


def measure(module_path: Path, attr: str, *args, **kwargs) -> float:
    """Actually import and execute the given callable (a real fixture
    implementation, not a hardcoded number) and return the metric value it
    reports."""
    fn = _load_callable(module_path, attr)
    return float(fn(*args, **kwargs))


def is_regression(baseline: Baseline, current_value: float) -> bool:
    if baseline.direction == "lower_is_better":
        allowed_max = baseline.value * (1 + baseline.tolerance_pct / 100)
        return current_value > allowed_max
    elif baseline.direction == "higher_is_better":
        allowed_min = baseline.value * (1 - baseline.tolerance_pct / 100)
        return current_value < allowed_min
    raise ValueError(f"unknown baseline direction: {baseline.direction!r}")


def run_regression_check_layer(
    *,
    baseline_path: Path | None,
    module_path: Path,
    attr: str,
    measure_args: tuple = (),
) -> LayerResult:
    if baseline_path is None or not baseline_path.is_file():
        return LayerResult(
            name="behavioral_regression_check",
            status="skipped",
            summary="No recorded baseline for this scope; Sec. 11.1 only requires this check where applicable.",
            details={},
        )

    baseline = load_baseline(baseline_path)
    current_value = measure(module_path, attr, *measure_args)
    regressed = is_regression(baseline, current_value)

    details = {
        "metric": baseline.metric,
        "baseline_value": baseline.value,
        "current_value": current_value,
        "direction": baseline.direction,
        "tolerance_pct": baseline.tolerance_pct,
    }

    if regressed:
        return LayerResult(
            name="behavioral_regression_check",
            status="fail",
            summary=(
                f"Regression on '{baseline.metric}': baseline={baseline.value}, current={current_value} "
                f"({baseline.direction}, tolerance={baseline.tolerance_pct}%)."
            ),
            details=details,
        )

    return LayerResult(
        name="behavioral_regression_check",
        status="pass",
        summary=f"'{baseline.metric}' within tolerance: baseline={baseline.value}, current={current_value}.",
        details=details,
    )
