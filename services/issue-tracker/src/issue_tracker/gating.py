"""Opt-in gating: makes it structurally impossible for a story lacking
the opt-in label/type to produce a dispatch.

Master spec Sec. 4.4: "A story is opted into autonomous handling by an
explicit, narrow signal ... rather than by every story in a status
implicitly qualifying." The brief requires this be a real, testable
function, not a doc statement -- so this module is the single choke
point every dispatch-producing path (the Automation-rule payload
contract in `webhook_relay.py`) must pass through, and it is built so
that path *cannot* construct a dispatchable payload without going
through `evaluate`.

Two layers of enforcement, both present here:

1. A nominal type (`OptedInStory`) that can only be constructed via
   `evaluate`/`require` -- there is no public constructor that skips
   the check, so a caller cannot fabricate "an opted-in story" by hand.
2. `evaluate` re-derives the decision from the raw issue fields every
   time, rather than trusting a caller-supplied boolean flag -- so even
   Jira Automation's own condition step (which we do not control the
   internals of) is never the sole gate; D4's own code re-checks it.
"""

from __future__ import annotations

from dataclasses import dataclass

from issue_tracker.config import TenantJiraConfig


@dataclass(frozen=True)
class OptedInStory:
    """Evidence that a specific issue satisfied the opt-in gate at the
    moment it was checked. The only way to obtain one is `evaluate`
    returning it; there is no other constructor path in this module or
    exported from it, and dataclass equality/repr make a fabricated
    instance visibly inspectable in tests (see test_gating.py) rather
    than an opaque bool.
    """

    issue_key: str
    tenant_id: str
    matched_label: str | None
    matched_issue_type: str | None


@dataclass(frozen=True)
class NotOptedIn:
    """The negative outcome, carrying *why* -- never silently `None`,
    so a caller (and a test) can assert on the reason, not just a
    falsy value."""

    issue_key: str
    reason: str


def evaluate(*, tenant_id: str, issue_key: str, status: str, labels: list[str], issue_type: str,
             config: TenantJiraConfig) -> OptedInStory | NotOptedIn:
    """The single source of truth for "should the factory pick this
    story up". Mirrors exactly what the Jira Automation rule's own
    condition step is configured to check (see automation-rule.json),
    so the two can never quietly diverge -- this function *is* the
    condition, expressed in code so it is independently testable and
    independently re-checked by `webhook_relay.py` before it will ever
    sign and forward a dispatch.

    Deliberately status-independent: per Sec. 4.4, "never treat 'in
    the trigger status' alone as sufficient" -- so this function does
    not even take the trigger status as a pass/fail input, only as
    data a caller may separately choose to also check. Status
    matching the Automation rule's trigger condition is necessary but
    is checked by the Automation rule trigger itself firing this
    call at all; what `evaluate` guards is the *label/type* condition,
    which is the one a caller could otherwise forget.
    """
    label_match = config.opt_in_label if config.opt_in_label and config.opt_in_label in labels else None
    type_match = config.opt_in_issue_type if config.opt_in_issue_type and config.opt_in_issue_type == issue_type else None

    if label_match is None and type_match is None:
        configured = []
        if config.opt_in_label:
            configured.append(f"label {config.opt_in_label!r}")
        if config.opt_in_issue_type:
            configured.append(f"issue type {config.opt_in_issue_type!r}")
        configured_desc = " or ".join(configured) if configured else "no opt-in signal configured"
        return NotOptedIn(
            issue_key=issue_key,
            reason=(
                f"Issue {issue_key} carries neither the opt-in {configured_desc}; even though its status "
                f"is {status!r}, it will not be dispatched. Being in the trigger status alone is never "
                "sufficient (master spec Sec. 4.4)."
            ),
        )

    return OptedInStory(
        issue_key=issue_key,
        tenant_id=tenant_id,
        matched_label=label_match,
        matched_issue_type=type_match,
    )


def require(*, tenant_id: str, issue_key: str, status: str, labels: list[str], issue_type: str,
            config: TenantJiraConfig) -> OptedInStory:
    """Same check as `evaluate`, but raises instead of returning the
    negative branch -- used at the one call site (`webhook_relay.py`)
    where "not opted in" must abort dispatch entirely rather than be a
    value a caller could accidentally ignore.
    """
    result = evaluate(tenant_id=tenant_id, issue_key=issue_key, status=status, labels=labels,
                       issue_type=issue_type, config=config)
    if isinstance(result, NotOptedIn):
        raise NotOptedInError(result.reason)
    return result


class NotOptedInError(Exception):
    """Raised by `require` when a story lacks the opt-in signal. The
    only exception type `webhook_relay.py` accepts as a legitimate
    "no dispatch" outcome for this reason -- any other exception is a
    real failure, not a gating decision.
    """
