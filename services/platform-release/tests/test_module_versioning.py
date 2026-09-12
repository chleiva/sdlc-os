from __future__ import annotations

from pathlib import Path

import pytest

from platform_release.module_versioning import (
    CellDefinition,
    CellDefinitionDenialReason,
    ModuleVersionCandidate,
    ModuleVersionRegistry,
    ValidationDenialReason,
    hash_module_dir,
    promote_module_version,
    validate_cell_definition,
    validate_module_version,
)

FIXTURES = Path(__file__).parent / "fixtures" / "candidate_modules"


def test_valid_candidate_passes_real_tofu_validate_and_plan():
    candidate = ModuleVersionCandidate(
        module_name="tenant-cell",
        version="1.0.0",
        module_dir=FIXTURES / "v1_valid",
        variables={"module_version": "1.0.0"},
    )
    outcome = validate_module_version(candidate)
    assert outcome.approved, outcome.detail
    assert outcome.denial_reason is None


def test_invalid_candidate_genuinely_fails_tofu_validate():
    candidate = ModuleVersionCandidate(
        module_name="tenant-cell",
        version="2.0.0-broken",
        module_dir=FIXTURES / "v2_invalid",
        variables={},
    )
    outcome = validate_module_version(candidate)
    assert not outcome.approved
    assert outcome.denial_reason == ValidationDenialReason.VALIDATE_FAILED
    assert "undeclared_variable" in outcome.detail or "Unsupported argument" in outcome.detail or "not declared" in outcome.detail.lower()


def test_promote_module_version_writes_a_pinned_record_only_on_success(tmp_path):
    registry = ModuleVersionRegistry(base_dir=tmp_path)
    candidate = ModuleVersionCandidate(
        module_name="tenant-cell",
        version="1.0.0",
        module_dir=FIXTURES / "v1_valid",
        variables={"module_version": "1.0.0"},
    )

    assert registry.is_promoted("tenant-cell", "1.0.0") is False

    result = promote_module_version(candidate, registry, validated_by="alice")

    assert result.approved
    assert result.record is not None
    assert result.record.module_name == "tenant-cell"
    assert result.record.version == "1.0.0"
    assert result.record.validated_by == "alice"
    assert result.record.content_hash == hash_module_dir(FIXTURES / "v1_valid")

    assert registry.is_promoted("tenant-cell", "1.0.0") is True

    # Durable: a fresh registry instance pointed at the same base_dir
    # sees the promotion too.
    reloaded = ModuleVersionRegistry(base_dir=tmp_path)
    assert reloaded.is_promoted("tenant-cell", "1.0.0") is True


def test_promote_module_version_refuses_a_failing_candidate(tmp_path):
    registry = ModuleVersionRegistry(base_dir=tmp_path)
    candidate = ModuleVersionCandidate(
        module_name="tenant-cell",
        version="2.0.0-broken",
        module_dir=FIXTURES / "v2_invalid",
        variables={},
    )

    result = promote_module_version(candidate, registry, validated_by="alice")

    assert not result.approved
    assert result.record is None
    assert result.denial_reason == ValidationDenialReason.VALIDATE_FAILED
    assert registry.is_promoted("tenant-cell", "2.0.0-broken") is False


def test_cell_definition_pointed_at_unpromoted_version_is_rejected(tmp_path):
    """The acceptance criterion, verbatim: 'a tenant's cell definition
    pointed at an unpromoted version is rejected.'"""
    registry = ModuleVersionRegistry(base_dir=tmp_path)
    # Nothing promoted yet at all.
    cell = CellDefinition(tenant_id="acme", module_name="tenant-cell", module_version="9.9.9")

    result = validate_cell_definition(cell, registry)

    assert not result.approved
    assert result.denial_reason == CellDefinitionDenialReason.MODULE_VERSION_NOT_PROMOTED


def test_cell_definition_pointed_at_a_promoted_version_is_accepted(tmp_path):
    registry = ModuleVersionRegistry(base_dir=tmp_path)
    candidate = ModuleVersionCandidate(
        module_name="tenant-cell",
        version="1.0.0",
        module_dir=FIXTURES / "v1_valid",
        variables={"module_version": "1.0.0"},
    )
    promotion = promote_module_version(candidate, registry, validated_by="alice")
    assert promotion.approved

    cell = CellDefinition(tenant_id="acme", module_name="tenant-cell", module_version="1.0.0")
    result = validate_cell_definition(cell, registry)
    assert result.approved


def test_a_module_that_failed_promotion_can_never_be_referenced_by_a_cell(tmp_path):
    """Proves the "never whatever's newest" property end to end: even
    after a promotion *attempt*, a cell referencing the version that
    failed to promote is still rejected exactly as if nothing had ever
    been attempted."""
    registry = ModuleVersionRegistry(base_dir=tmp_path)
    candidate = ModuleVersionCandidate(
        module_name="tenant-cell",
        version="2.0.0-broken",
        module_dir=FIXTURES / "v2_invalid",
        variables={},
    )
    promotion = promote_module_version(candidate, registry, validated_by="alice")
    assert not promotion.approved

    cell = CellDefinition(tenant_id="acme", module_name="tenant-cell", module_version="2.0.0-broken")
    result = validate_cell_definition(cell, registry)
    assert not result.approved
    assert result.denial_reason == CellDefinitionDenialReason.MODULE_VERSION_NOT_PROMOTED


def test_hash_module_dir_is_stable_and_content_sensitive(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "main.tf").write_text("resource \"local_file\" \"x\" {}\n")

    b = tmp_path / "b"
    b.mkdir()
    (b / "main.tf").write_text("resource \"local_file\" \"x\" {}\n")

    assert hash_module_dir(a) == hash_module_dir(b)

    (b / "main.tf").write_text("resource \"local_file\" \"y\" {}\n")
    assert hash_module_dir(a) != hash_module_dir(b)
