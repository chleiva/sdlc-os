"""D5 acceptance criterion: "Appears in every audit log as its own
distinguishable bot identity, not a human's." Combines a structural
check (the only actor shape this codebase can ever construct is a
`<app-slug>[bot]` login) with a behavioral one (every real API call made
against the mock server actually produces such an audit event)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from source_control.audit import AuditLogger, bot_actor, is_bot_actor
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

SRC = Path(__file__).resolve().parent.parent / "src" / "source_control"


def test_bot_actor_shape_matches_githubs_own_bot_login_convention():
    assert bot_actor("sdlc-auto") == "sdlc-auto[bot]"
    assert is_bot_actor("sdlc-auto[bot]")
    assert not is_bot_actor("chris-leiva")  # a human login must never pass this check
    assert not is_bot_actor("sdlc-auto")  # missing the [bot] suffix


def test_bot_actor_rejects_slugs_that_would_not_form_a_valid_github_login():
    with pytest.raises(ValueError):
        bot_actor("not a valid slug!")


def test_audit_logger_only_ever_records_a_bot_actor():
    logger = AuditLogger()
    event = logger.record(
        app_slug="sdlc-auto", installation_id="inst-1", tenant_id="tenant-x",
        action="get_ref", repository="acme/app", outcome="ok",
    )
    assert event.actor == "sdlc-auto[bot]"
    assert is_bot_actor(event.actor)


def test_audit_event_constructor_has_no_freeform_actor_parameter():
    """Structural guard: AuditLogger.record must derive `actor` from
    `bot_actor(app_slug)` internally -- it must not accept an `actor`
    keyword a caller could set to an arbitrary (human-shaped) string."""
    import inspect

    sig = inspect.signature(AuditLogger.record)
    assert "actor" not in sig.parameters


def test_real_api_calls_through_the_service_are_all_attributed_to_the_bot_actor(rsa_keypair, mock_github, tmp_path):
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40
    server.state.trees[("acme", "app", "a" * 40)] = [{"path": "f.py", "type": "blob", "size": 1}]

    installation = TenantInstallation(
        tenant_id="tenant-acme", installation_id="inst-1", app_id="918273", app_slug="sdlc-auto",
        private_key_pem=private_pem, allowed_repositories=frozenset({"acme/app"}),
        mirror_root=tmp_path / "mirrors", api_base_url=base_url,
    )
    registry = InstallationRegistry()
    registry.register(installation)
    audit = AuditLogger()
    service = SourceControlService(registry, audit_logger=audit)

    result = service.list_files(tenant_id="tenant-acme", repository="acme/app", branch="main")
    assert result["outcome"] == "ok"

    assert len(audit.events) > 0
    for event in audit.events:
        assert is_bot_actor(event.actor), f"non-bot actor recorded: {event.actor!r}"
        assert event.actor == "sdlc-auto[bot]"
        # Never a human-shaped identity, e.g. a raw username or "unknown".
        assert not re.match(r"^[a-z][a-z0-9_-]*$", event.actor.replace("[bot]", "")) or event.actor.endswith("[bot]")


def test_no_module_fabricates_an_auditevent_directly_bypassing_auditlogger():
    """AuditEvent should only ever be constructed inside audit.py itself
    (by AuditLogger.record) -- another module reaching in and building
    one directly could bypass the bot_actor() validation entirely."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "audit.py":
            continue
        if "AuditEvent(" in path.read_text():
            offenders.append(str(path))
    assert offenders == [], f"AuditEvent constructed outside audit.py: {offenders}"
