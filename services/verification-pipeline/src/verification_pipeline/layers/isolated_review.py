"""Layer 5 -- Isolated-context review pass (Sec. 11.1, Sec. 8.4, Sec. 13.4):

"a Subagent with no memory of the implementation conversation re-reads
the diff against the plan and flags correctness, edge-case, and security
concerns. For higher-risk stories this is the adversarial verifier of
Sec. 8.4, not just a second look by a similar agent."

There is no live LLM in this environment (same as D2's own "real
orchestration, mocked model call" discipline, which this deliberately
mirrors). What IS real here, and genuinely tested rather than merely
asserted, is the isolation property itself:

  * `ReviewContext` is a plain, self-contained value object holding only
    the diff text and the plan summary text -- nothing else. It is built
    fresh, from scratch, for every review call.

  * `ReviewerBackend.review`'s signature structurally cannot accept the
    implementer's conversation object -- there is no parameter for it.
    `test_layer5_isolated_review.py` asserts this via `inspect.signature`,
    not just by convention.

  * `build_isolated_review_context` takes the implementer's own mutable
    conversation transcript, copies only the two fields the reviewer is
    allowed to see into a brand-new object, and returns it. The test
    mutates the implementer's transcript *after* building the context and
    asserts the reviewer's context is unaffected (no shared reference) and
    is a distinct object (`is not`) -- i.e. it is not the same list/dict
    the implementer holds, and holds none of the implementer's messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .base import LayerResult


@dataclass(frozen=True)
class ReviewContext:
    """Everything -- and ONLY everything -- an isolated reviewer is allowed
    to see. Deliberately has no field for "prior conversation", "implementer
    scratchpad", or any other shared state with whatever produced the diff.
    """

    diff_text: str
    plan_summary: str


@dataclass
class ReviewFinding:
    severity: str  # "info" | "concern" | "blocking"
    message: str


@dataclass
class ReviewResult:
    backend_name: str
    model_family: str
    findings: list[ReviewFinding] = field(default_factory=list)

    @property
    def has_blocking_finding(self) -> bool:
        return any(f.severity == "blocking" for f in self.findings)


class ReviewerBackend(Protocol):
    """Note the signature: `review(context: ReviewContext) -> ReviewResult`.
    There is structurally no parameter through which an implementer's
    conversation history, memory, or shared mutable state could pass --
    the isolation property is enforced by the interface shape, not left to
    an implementation's good behavior.
    """

    name: str
    model_family: str

    def review(self, context: ReviewContext) -> ReviewResult: ...


class MockReviewerBackend:
    """Deterministic mock reviewer for tests -- same "real orchestration,
    mocked model call" discipline as D2. Flags a fixed, deterministic set
    of textual markers in the diff (e.g. a deliberately-planted bug marker
    in a test fixture) rather than anything resembling real model
    judgment.
    """

    name = "mock-reviewer"
    model_family = "deterministic-mock"

    def __init__(self, blocking_markers: tuple[str, ...] = ("BUG:", "SECURITY-ISSUE:")):
        self.blocking_markers = blocking_markers

    def review(self, context: ReviewContext) -> ReviewResult:
        findings = []
        for marker in self.blocking_markers:
            if marker in context.diff_text:
                findings.append(ReviewFinding("blocking", f"Diff contains marker '{marker}' -- flagged for review."))
        if not findings:
            findings.append(ReviewFinding("info", "No planted concern markers found in diff."))
        return ReviewResult(backend_name=self.name, model_family=self.model_family, findings=findings)


class AdversarialReviewerBackend(MockReviewerBackend):
    """Sec. 8.4 / Sec. 13.4: for higher-risk stories, the reviewer must be
    architecturally distinct from the implementer -- not merely "a second
    look by a similar agent". This subclass exists to be a structurally
    different backend (different `name`/`model_family`) that the pipeline
    selects for high-risk/cross-cutting stories, standing in for a real
    deployment's second, architecturally-distinct model or frontier-
    escalation path (Sec. 13.4). It is still a deterministic mock -- no
    live model here either -- but it is wired as a genuinely separate
    backend class, not a flag on the same one, so the model-diversity
    *selection logic* is real even though both backends are mocks.
    """

    name = "adversarial-reviewer"
    model_family = "architecturally-distinct-mock"

    def __init__(self, blocking_markers: tuple[str, ...] = ("BUG:", "SECURITY-ISSUE:", "SUBTLE:")):
        super().__init__(blocking_markers)


def select_reviewer_backend(risk_tier: str) -> ReviewerBackend:
    if risk_tier in ("high", "cross-cutting"):
        return AdversarialReviewerBackend()
    return MockReviewerBackend()


def build_isolated_review_context(diff_text: str, plan_summary: str) -> ReviewContext:
    """Build a brand-new ReviewContext from plain strings. Callers must
    never pass an implementer's own conversation/session object here --
    the type signature only accepts str, which is itself part of the
    isolation guarantee (a str is copied by value into the frozen
    dataclass, so there is no way for a caller's later mutation of some
    other object to reach back into this context).
    """
    return ReviewContext(diff_text=str(diff_text), plan_summary=str(plan_summary))


def run_isolated_review_layer(
    *,
    diff_text: str,
    plan_summary: str,
    risk_tier: str,
    backend: ReviewerBackend | None = None,
) -> LayerResult:
    backend = backend or select_reviewer_backend(risk_tier)
    context = build_isolated_review_context(diff_text, plan_summary)
    result = backend.review(context)

    status = "fail" if result.has_blocking_finding else "pass"
    return LayerResult(
        name="isolated_context_review",
        status=status,
        summary=(
            f"Reviewed by '{result.backend_name}' (model_family={result.model_family}); "
            f"{len(result.findings)} finding(s), blocking={result.has_blocking_finding}."
        ),
        details={
            "backend_name": result.backend_name,
            "model_family": result.model_family,
            "findings": [f.__dict__ for f in result.findings],
        },
    )
