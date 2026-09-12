"""Layer 7 -- Cross-codebase completion check (Sec. 11.1, Sec. 6.6):

"an exhaustive, deterministic reference search for every symbol the diff
touched, renamed, or removed, across the full index."

"Any reference the implementing agent never opened is treated as an
unresolved impact, not a false positive: the run pauses at the Sec. 9.3
risk checkpoint" (Sec. 6.6).

This calls D3's real, already-built index MCP server (`index_client.py`)
-- not a stub, not an in-process re-implementation -- for every symbol
under test, across the *entire* index (never limited to the plan's
declared scope, per the brief).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from ..index_client import IndexClient
from .base import LayerResult


@dataclass(frozen=True)
class TouchedSymbol:
    name: str
    origin_file: str
    origin_line: int
    change_kind: str  # "renamed" | "changed" | "removed" | "defined"


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def run_completion_check_layer(
    *,
    tenant_id: str,
    repository: str,
    touched_symbols: list[TouchedSymbol],
    opened_files: set[str],
    declared_in_scope: list[str],
    index_client: IndexClient | None = None,
) -> LayerResult:
    index_client = index_client or IndexClient()

    unresolved: dict[str, list[dict]] = {}
    all_references: dict[str, list[dict]] = {}

    for symbol in touched_symbols:
        result = index_client.find_references(
            tenant_id=tenant_id,
            repository=repository,
            symbol=symbol.name,
            origin={"file": symbol.origin_file, "line": symbol.origin_line},
        )
        if result.get("outcome") == "error":
            return LayerResult(
                name="cross_codebase_completion_check",
                status="error",
                summary=f"index server error while resolving '{symbol.name}': {result['error']}",
                details={"symbol": symbol.name, "error": result["error"]},
            )
        if result.get("outcome") == "empty":
            all_references[symbol.name] = []
            continue

        refs = result["data"]["references"]
        all_references[symbol.name] = refs

        # "Any reference the implementing agent never opened is treated as
        # an unresolved impact" -- Sec. 6.6. This is checked against the
        # actual set of files opened during implementation, NOT merely
        # against the plan's declared scope: a file can be in-scope on
        # paper and still never actually opened/reviewed.
        for ref in refs:
            ref_file = ref["file"]
            if ref_file not in opened_files:
                unresolved.setdefault(symbol.name, []).append(ref)

    if unresolved:
        # Distinguish (for reporting only, not for whether the checkpoint
        # trips) references that at least fall within the plan's declared
        # scope from ones entirely outside it -- the brief's acceptance
        # criterion is specifically about the latter.
        outside_declared_scope = {
            sym: [r for r in refs if not _matches_any(r["file"], declared_in_scope)]
            for sym, refs in unresolved.items()
        }
        return LayerResult(
            name="cross_codebase_completion_check",
            status="checkpoint",
            summary=(
                f"{sum(len(v) for v in unresolved.values())} reference(s) to changed symbol(s) were never opened "
                "by the implementing agent -- Sec. 9.3 risk checkpoint tripped."
            ),
            details={
                "unresolved_references": unresolved,
                "unresolved_outside_declared_scope": outside_declared_scope,
                "all_references": all_references,
            },
        )

    return LayerResult(
        name="cross_codebase_completion_check",
        status="pass",
        summary="Every reference to every touched symbol, across the full index, was opened by the implementing agent.",
        details={"all_references": all_references},
    )
