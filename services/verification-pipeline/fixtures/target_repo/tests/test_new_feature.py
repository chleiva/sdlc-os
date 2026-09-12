"""New/updated test mapped to an acceptance criterion in the Layer 2
fixture plan artifact (fixtures/plans/valid_plan.json)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sample_pkg.billing import compute_discount  # noqa: E402


def test_compute_discount_applies_rate():
    assert compute_discount(100.0, 0.1) == 90.0
