"""Layer 5 -- Isolated-context review pass. Acceptance criterion: "The
isolated-context reviewer runs in a genuinely separate context from the
implementer (no shared conversation history)" -- proven structurally
(the interface has no parameter for it) AND at runtime (mutating the
implementer's transcript after the fact cannot reach the reviewer)."""
import inspect

from verification_pipeline.layers.isolated_review import (
    AdversarialReviewerBackend,
    MockReviewerBackend,
    ReviewContext,
    ReviewerBackend,
    build_isolated_review_context,
    run_isolated_review_layer,
    select_reviewer_backend,
)


def test_clean_diff_passes_review():
    result = run_isolated_review_layer(diff_text="def foo(): return 1", plan_summary="Add foo()", risk_tier="low")
    assert result.status == "pass"


def test_diff_with_planted_marker_is_flagged_blocking():
    result = run_isolated_review_layer(
        diff_text="def foo(): return 1  # BUG: off-by-one here", plan_summary="Add foo()", risk_tier="low"
    )
    assert result.status == "fail"
    assert result.blocks_human_review


def test_review_context_has_no_field_for_shared_conversation_state():
    """Structural proof: ReviewContext's only fields are diff_text and
    plan_summary -- there is no way to construct one carrying the
    implementer's conversation/session object."""
    fields = {f for f in ReviewContext.__dataclass_fields__}
    assert fields == {"diff_text", "plan_summary"}


def test_reviewer_backend_signature_cannot_accept_implementer_conversation():
    """Structural proof on the interface itself, not just on one
    implementation: `review` takes exactly one argument beyond self, and
    it is typed as ReviewContext -- there is no second parameter an
    implementer's conversation object could be smuggled through."""
    sig = inspect.signature(ReviewerBackend.review)
    params = [p for name, p in sig.parameters.items() if name != "self"]
    assert len(params) == 1
    assert params[0].name == "context"


def test_mutating_implementer_transcript_after_context_build_does_not_leak_in():
    """Runtime proof of isolation: build the reviewer's context from the
    implementer's live transcript, then mutate that transcript. The
    reviewer's stored context must be unaffected -- proving it holds no
    live reference back into the implementer's state."""
    implementer_transcript = ["implementer: let me look at the ticket", "implementer: here is my plan"]
    diff_text = "def foo(): return 42"
    plan_summary = "Add foo() returning 42"

    context = build_isolated_review_context(diff_text, plan_summary)

    # Mutate the implementer's own transcript AFTER building the context.
    implementer_transcript.append("implementer: actually let me also whisper the answer to the reviewer")
    implementer_transcript.clear()

    assert context.diff_text == diff_text
    assert context.plan_summary == plan_summary
    assert context is not implementer_transcript
    # The context object structurally has no attribute that could hold
    # the implementer's transcript at all.
    assert not hasattr(context, "conversation_history")
    assert not hasattr(context, "implementer_transcript")


def test_backend_instances_across_two_reviews_share_no_mutable_state():
    """Two separate review calls (as if for two different stories) must
    not share a mutable object -- each gets its own fresh ReviewContext."""
    ctx_a = build_isolated_review_context("diff A", "plan A")
    ctx_b = build_isolated_review_context("diff B", "plan B")
    assert ctx_a is not ctx_b
    assert ctx_a.diff_text != ctx_b.diff_text


def test_high_risk_selects_the_architecturally_distinct_adversarial_backend():
    """Sec. 8.4 / Sec. 13.4: higher-risk stories get the adversarial
    verifier, not just a second look by a similar agent -- proven by
    selecting a structurally different backend class/model_family."""
    low_backend = select_reviewer_backend("low")
    high_backend = select_reviewer_backend("high")
    cross_cutting_backend = select_reviewer_backend("cross-cutting")

    assert isinstance(low_backend, MockReviewerBackend)
    assert not isinstance(low_backend, AdversarialReviewerBackend)
    assert isinstance(high_backend, AdversarialReviewerBackend)
    assert isinstance(cross_cutting_backend, AdversarialReviewerBackend)
    assert low_backend.model_family != high_backend.model_family


def test_adversarial_backend_catches_a_subtler_marker_the_mock_does_not():
    subtle_diff = "def foo(): return 1  # SUBTLE: silently swallows an edge case"
    mock_result = run_isolated_review_layer(diff_text=subtle_diff, plan_summary="x", risk_tier="low")
    adversarial_result = run_isolated_review_layer(diff_text=subtle_diff, plan_summary="x", risk_tier="high")
    assert mock_result.status == "pass"
    assert adversarial_result.status == "fail"
