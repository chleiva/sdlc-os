"""Infra module versioning gate (master spec §14.15's last bullet):

"Infrastructure modules (Section 14.3) are versioned and tested the same
way: a module change is validated against a non-production environment
composition before being promoted to any tenant's Section 14.13 cell
definition, and a tenant's cell is never auto-upgraded to a new module
version without that tenant's change being tracked the same way any
other infrastructure change is."

Two real, subprocess-driven `tofu` steps do the actual validation
(`validate_module_version` runs `tofu init` + `tofu validate` + `tofu
plan` against a throwaway composition that wraps the candidate module --
a genuinely non-production, disposable environment per apply); a
candidate module version is `promote_module_version`-d into a durable
`ModuleVersionRegistry` only if that real validation passed. A
`CellDefinition` is then only accepted by `validate_cell_definition` if
it names a version already present in that registry -- referencing
"whatever's newest" is not an option this API exposes at all, since
nothing here ever resolves a cell definition's module_version from
anything but the registry's own promoted-versions set.

This mirrors `services/tenant-cell/src/tenant_cell/model_version_governance.py`'s
`promote_model_version` gate almost exactly (candidate -> validation
result -> approved/denied PromotionResult with a typed denial reason) --
deliberately: it is the same "pin, validate, promote, never a background
update" discipline (spec §13.5), applied to infra modules instead of
model weights. Per this deliverable's own instructions this is a fresh,
independent implementation of that pattern for the module-versioning
domain, not an import of tenant_cell's module.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from platform_release import tofu_runner


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hcl_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)  # HCL string literal syntax matches JSON string syntax
    raise TypeError(f"unsupported HCL variable value type: {type(value)!r}")


def hash_module_dir(module_dir: str | Path) -> str:
    """A content hash over every file in `module_dir` (sorted by relative
    path, path + content both hashed) -- a pinned-version record is tied
    to the exact bytes that were validated, so a module directory edited
    after promotion (without bumping its version) no longer matches its
    own pinned record's hash. This is the same "pinned by checksum"
    discipline spec §13.5 applies to model weights, applied here to a
    module's own source tree."""
    module_dir = Path(module_dir)
    hasher = hashlib.sha256()
    for path in sorted(p for p in module_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(module_dir).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


@dataclass(frozen=True)
class ModuleVersionCandidate:
    """A proposed module version change awaiting validation/promotion."""

    module_name: str
    version: str
    module_dir: Path
    variables: dict = field(default_factory=dict)


class ValidationDenialReason(str, Enum):
    INIT_FAILED = "init-failed"
    VALIDATE_FAILED = "validate-failed"
    PLAN_FAILED = "plan-failed"


@dataclass(frozen=True)
class ValidationOutcome:
    approved: bool
    candidate: ModuleVersionCandidate
    detail: str
    denial_reason: ValidationDenialReason | None = None


def validate_module_version(
    candidate: ModuleVersionCandidate,
    *,
    tofu_bin: str = "tofu",
    workdir: str | Path | None = None,
) -> ValidationOutcome:
    """Renders a throwaway wrapper composition around
    `candidate.module_dir` and runs a real `tofu init` + `tofu validate`
    + `tofu plan` against it -- the "non-production environment
    composition" the spec requires. Returns an outcome that is only
    `approved=True` if all three genuinely succeeded."""
    own_tmp = workdir is None
    work = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="platform-release-module-gate-"))
    try:
        module_source = Path(candidate.module_dir).resolve().as_posix()
        var_lines = "\n".join(f"  {name} = {_hcl_literal(value)}" for name, value in candidate.variables.items())
        (work / "main.tf").write_text(
            "terraform {\n"
            '  required_version = ">= 1.6.0"\n'
            "}\n\n"
            'module "candidate" {\n'
            f'  source = "{module_source}"\n'
            f"{var_lines}\n"
            "}\n"
        )

        init_result = tofu_runner.init(work, tofu_bin=tofu_bin)
        if not init_result.ok:
            return ValidationOutcome(
                approved=False,
                candidate=candidate,
                detail=f"tofu init failed:\n{init_result.stdout}\n{init_result.stderr}",
                denial_reason=ValidationDenialReason.INIT_FAILED,
            )

        validate_result = tofu_runner.validate(work, tofu_bin=tofu_bin)
        if not validate_result.ok:
            return ValidationOutcome(
                approved=False,
                candidate=candidate,
                detail=f"tofu validate failed:\n{validate_result.stdout}\n{validate_result.stderr}",
                denial_reason=ValidationDenialReason.VALIDATE_FAILED,
            )

        plan_result = tofu_runner.plan(work, tofu_bin=tofu_bin)
        if not plan_result.ok:
            return ValidationOutcome(
                approved=False,
                candidate=candidate,
                detail=f"tofu plan failed:\n{plan_result.stdout}\n{plan_result.stderr}",
                denial_reason=ValidationDenialReason.PLAN_FAILED,
            )

        return ValidationOutcome(
            approved=True,
            candidate=candidate,
            detail=f"tofu init + validate + plan all succeeded against a non-production composition:\n{plan_result.stdout}",
        )
    finally:
        if own_tmp:
            shutil.rmtree(work, ignore_errors=True)


@dataclass(frozen=True)
class PinnedModuleVersion:
    module_name: str
    version: str
    content_hash: str
    validated_by: str
    validated_at: str
    validation_detail: str


class ModuleVersionRegistry:
    """The durable "pinned-version record" store -- the thing a tenant's
    cell definition is required to reference explicitly. Backed by a
    JSON file (`base_dir/pinned_module_versions.json`) so promotion
    survives a process restart, same durability discipline as this
    package's `audit.AuditLog` and `gates/audit.py`'s own JSON-file-backed
    store."""

    _FILENAME = "pinned_module_versions.json"

    def __init__(self, base_dir: str | Path):
        self._lock = threading.Lock()
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._path = self._base / self._FILENAME
        self._records: dict[str, dict[str, dict]] = {}
        if self._path.exists():
            self._records = json.loads(self._path.read_text())

    def _persist(self) -> None:
        self._path.write_text(json.dumps(self._records, indent=2, sort_keys=True))

    def promote(self, record: PinnedModuleVersion) -> None:
        with self._lock:
            self._records.setdefault(record.module_name, {})[record.version] = asdict(record)
            self._persist()

    def get(self, module_name: str, version: str) -> PinnedModuleVersion | None:
        with self._lock:
            raw = self._records.get(module_name, {}).get(version)
            return PinnedModuleVersion(**raw) if raw is not None else None

    def is_promoted(self, module_name: str, version: str) -> bool:
        return self.get(module_name, version) is not None

    def all_versions(self, module_name: str) -> list[str]:
        with self._lock:
            return sorted(self._records.get(module_name, {}))


@dataclass(frozen=True)
class PromotionResult:
    approved: bool
    candidate: ModuleVersionCandidate
    reason: str
    record: PinnedModuleVersion | None = None
    denial_reason: ValidationDenialReason | None = None


def promote_module_version(
    candidate: ModuleVersionCandidate,
    registry: ModuleVersionRegistry,
    *,
    validated_by: str,
    tofu_bin: str = "tofu",
) -> PromotionResult:
    """The gate: only ever writes a pinned-version record into `registry`
    if a real `validate_module_version` run against a non-production
    composition genuinely passed. Never promotes on the caller's say-so
    alone."""
    outcome = validate_module_version(candidate, tofu_bin=tofu_bin)
    if not outcome.approved:
        return PromotionResult(
            approved=False,
            candidate=candidate,
            reason=outcome.detail,
            denial_reason=outcome.denial_reason,
        )

    record = PinnedModuleVersion(
        module_name=candidate.module_name,
        version=candidate.version,
        content_hash=hash_module_dir(candidate.module_dir),
        validated_by=validated_by,
        validated_at=_now_iso(),
        validation_detail=outcome.detail,
    )
    registry.promote(record)
    return PromotionResult(approved=True, candidate=candidate, reason=outcome.detail, record=record)


class CellDefinitionDenialReason(str, Enum):
    MODULE_VERSION_NOT_PROMOTED = "module-version-not-promoted"


@dataclass(frozen=True)
class CellDefinition:
    """A tenant's compute-cell definition (spec §14.13) as far as this
    gate is concerned: which module, at which version, for which tenant.
    Deliberately has no "latest"/"newest" option -- `module_version` is
    always a specific, explicit string, and it is only ever accepted if
    that exact string is already promoted in the registry."""

    tenant_id: str
    module_name: str
    module_version: str


@dataclass(frozen=True)
class CellDefinitionResult:
    approved: bool
    cell: CellDefinition
    reason: str
    denial_reason: CellDefinitionDenialReason | None = None


def validate_cell_definition(cell: CellDefinition, registry: ModuleVersionRegistry) -> CellDefinitionResult:
    """Refuses a cell definition that points at any module_version not
    already promoted in `registry` -- this is the enforcement point for
    "a tenant's cell is never auto-upgraded to a new module version
    without that change being tracked" (spec §14.15): there is no code
    path in this function (or anywhere else in this package) that
    resolves "newest" or "latest" on the tenant's behalf."""
    record = registry.get(cell.module_name, cell.module_version)
    if record is None:
        return CellDefinitionResult(
            approved=False,
            cell=cell,
            reason=(
                f"module '{cell.module_name}' version '{cell.module_version}' is not a promoted, "
                "pinned version in the module-version registry -- a tenant's cell definition must "
                "reference an already-validated, explicitly-promoted version, never whatever is "
                "newest (spec §14.15)"
            ),
            denial_reason=CellDefinitionDenialReason.MODULE_VERSION_NOT_PROMOTED,
        )
    return CellDefinitionResult(
        approved=True,
        cell=cell,
        reason=f"module '{cell.module_name}' version '{cell.module_version}' is promoted (validated by {record.validated_by} at {record.validated_at})",
    )
