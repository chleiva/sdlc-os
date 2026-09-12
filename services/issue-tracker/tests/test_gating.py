"""Acceptance criterion: "A story without the opt-in label, even in the
trigger status, produces no webhook." Proven here at the gating-logic
level (the function every dispatch path must go through) and again,
end to end through `webhook_relay.py`, in test_webhook_relay.py.
"""

from issue_tracker import gating
from issue_tracker.config import TenantJiraConfig

CONFIG = TenantJiraConfig(
    tenant_id="tenant-acme",
    base_url="http://unused.invalid",
    oauth_bearer_token="x",
    opt_in_label="ai-factory",
    trigger_status="Selected for Development",
)


def test_story_with_opt_in_label_is_opted_in():
    result = gating.evaluate(
        tenant_id="tenant-acme", issue_key="PROJ-1", status="Selected for Development",
        labels=["ai-factory", "backend"], issue_type="Story", config=CONFIG,
    )
    assert isinstance(result, gating.OptedInStory)
    assert result.matched_label == "ai-factory"


def test_story_in_trigger_status_without_label_is_not_opted_in():
    # This is the exact acceptance-criterion case: trigger status
    # matches, but the opt-in label is absent.
    result = gating.evaluate(
        tenant_id="tenant-acme", issue_key="PROJ-2", status="Selected for Development",
        labels=["backend"], issue_type="Story", config=CONFIG,
    )
    assert isinstance(result, gating.NotOptedIn)
    assert "PROJ-2" in result.reason
    assert "trigger status alone is never sufficient" in result.reason


def test_story_with_opt_in_issue_type_is_opted_in_even_without_label():
    config = TenantJiraConfig(
        tenant_id="tenant-acme", base_url="http://unused.invalid", oauth_bearer_token="x",
        opt_in_label=None, opt_in_issue_type="AI Factory Task",
    )
    result = gating.evaluate(
        tenant_id="tenant-acme", issue_key="PROJ-3", status="Selected for Development",
        labels=[], issue_type="AI Factory Task", config=config,
    )
    assert isinstance(result, gating.OptedInStory)
    assert result.matched_issue_type == "AI Factory Task"


def test_require_raises_not_opted_in_error_when_gate_fails():
    try:
        gating.require(
            tenant_id="tenant-acme", issue_key="PROJ-4", status="Selected for Development",
            labels=[], issue_type="Story", config=CONFIG,
        )
        assert False, "expected NotOptedInError"
    except gating.NotOptedInError as exc:
        assert "PROJ-4" in str(exc)


def test_require_returns_opted_in_story_when_gate_passes():
    story = gating.require(
        tenant_id="tenant-acme", issue_key="PROJ-5", status="Selected for Development",
        labels=["ai-factory"], issue_type="Story", config=CONFIG,
    )
    assert isinstance(story, gating.OptedInStory)
    assert story.issue_key == "PROJ-5"


def test_no_opt_in_signal_configured_at_all_never_opts_in():
    config = TenantJiraConfig(
        tenant_id="tenant-acme", base_url="http://unused.invalid", oauth_bearer_token="x",
        opt_in_label=None, opt_in_issue_type=None,
    )
    result = gating.evaluate(
        tenant_id="tenant-acme", issue_key="PROJ-6", status="Selected for Development",
        labels=["ai-factory"], issue_type="Story", config=config,
    )
    assert isinstance(result, gating.NotOptedIn)
