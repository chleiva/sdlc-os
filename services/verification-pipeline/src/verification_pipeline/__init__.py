"""D7 -- Verification Pipeline (SDLC Auto, Wave 1).

Implements master-spec Sec. 11's seven verification layers plus the
flaky-test / retry-budget / "tests green is not sufficient" behaviors
named in the D7 brief (docs/deliverables/wave1-D7-verification-pipeline.md).
"""
from .pipeline import VerificationPipeline

__all__ = ["VerificationPipeline"]
