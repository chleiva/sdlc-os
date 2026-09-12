"""Cross-file linking: turns every file's RawRefs into resolved
References/CallSites against the whole repo's Definition table.

This is deliberately the *cheap* half of indexing (dict lookups over
already-parsed data) -- engine/repo_index.py reruns this over the full
corpus on every refresh, while the expensive half (tree-sitter parsing,
adapters/) only reruns for files that actually changed. That split is
what makes "incremental refresh" real rather than nominal: the O(files)
cost that dominates on a large repo (parsing) is avoided for unchanged
files; the O(files) cost that's cheap (linking dict lookups) is not
worth the bookkeeping to also make incremental.

Resolution strategy per RawRef chain (documented approximation -- see
services/index-server's acceptance-criteria report for the honest scope
of this vs. a full type-checker):

  1. self/cls in a method body -> the enclosing class's own member.
  2. The chain's head is a local import binding (alias or `from X import
     Y`) -> combine with the binding's target.
  3. The chain, taken as `<this file's module>.<chain...>`, is a real
     definition (same-file / same-module reference without an import).
  4. The chain's longest prefix matches a known module's dotted path
     anywhere in the index -> combine prefix-as-module with the
     remaining suffix (handles a fully-qualified reference such as
     `pkg_a.billing.util.compute_total(...)` even without an alias).
  5. Otherwise unresolved -- kept as a bare-name occurrence rather than
     silently dropped (see link_references' fallback bucket), so
     find-references still surfaces it instead of under-counting.
"""
from __future__ import annotations

from collections import defaultdict

from .model import CallSite, Definition, FileRecord, Reference


def build_definition_indices(
    file_records: dict[str, FileRecord]
) -> tuple[dict[str, Definition], dict[str, list[Definition]], set[str]]:
    defs_by_fqn: dict[str, Definition] = {}
    defs_by_name: dict[str, list[Definition]] = defaultdict(list)
    module_dotted_set: set[str] = set()
    for rec in file_records.values():
        module_dotted_set.add(rec.module_dotted)
        for d in rec.definitions:
            defs_by_fqn[d.fqn] = d
            defs_by_name[d.name].append(d)
    return defs_by_fqn, dict(defs_by_name), module_dotted_set


def resolve_chain(
    chain: tuple[str, ...],
    class_context: tuple[str, ...],
    rec: FileRecord,
    defs_by_fqn: dict[str, Definition],
    module_dotted_set: set[str],
) -> str | None:
    if not chain:
        return None

    # 1. self/cls member access.
    if chain[0] in ("self", "cls") and len(chain) >= 2 and class_context:
        candidate = ".".join([rec.module_dotted, *class_context, chain[1]])
        if candidate in defs_by_fqn:
            return candidate

    # 2. Local import binding.
    binding = rec.import_bindings.get(chain[0])
    if binding is not None:
        if binding.kind == "symbol":
            candidate = binding.target if len(chain) == 1 else ".".join([binding.target, *chain[1:]])
            if candidate in defs_by_fqn:
                return candidate
        elif binding.kind == "module":
            if len(chain) > 1:
                candidate = ".".join([binding.target, *chain[1:]])
                if candidate in defs_by_fqn:
                    return candidate

    # 3. Same-module reference (chain relative to this file's own module).
    candidate = ".".join([rec.module_dotted, *chain])
    if candidate in defs_by_fqn:
        return candidate

    # 4. Longest module-dotted-path prefix match, anywhere in the index.
    for split in range(len(chain) - 1, 0, -1):
        prefix = ".".join(chain[:split])
        if prefix in module_dotted_set:
            candidate = ".".join([prefix, *chain[split:]])
            if candidate in defs_by_fqn:
                return candidate

    return None


def _containing_definition(rec: FileRecord, line: int) -> Definition | None:
    best: Definition | None = None
    for d in rec.definitions:
        if d.kind not in ("function", "method"):
            continue
        if d.line <= line <= d.end_line:
            if best is None or d.line > best.line:
                best = d
    return best


def link_references(
    file_records: dict[str, FileRecord],
    defs_by_fqn: dict[str, Definition],
    module_dotted_set: set[str],
) -> tuple[dict[str, list[Reference]], dict[str, list[Reference]], dict[str, list[CallSite]]]:
    """Returns (by_fqn, by_name, calls_by_fqn).

    by_fqn:  resolved references, keyed by the definition's FQN.
    by_name: EVERY reference (resolved or not), keyed by bare name --
             find_references uses this as the exhaustiveness safety net
             for occurrences the resolver above could not confidently
             attribute (wildcard imports, dynamic access, etc.), while
             still never merging in a reference that resolved cleanly to
             a *different* symbol of the same name (see repo_index.py).
    calls_by_fqn: resolved call sites only, keyed by callee FQN, each
             carrying its containing symbol -- this is find-callers'
             one-hop call graph.
    """
    by_fqn: dict[str, list[Reference]] = defaultdict(list)
    by_name: dict[str, list[Reference]] = defaultdict(list)
    calls_by_fqn: dict[str, list[CallSite]] = defaultdict(list)

    for rec in file_records.values():
        for raw in rec.raw_refs:
            if raw.kind not in ("call", "attr_read", "bare_read"):
                continue
            resolved = resolve_chain(raw.chain, raw.class_context, rec, defs_by_fqn, module_dotted_set)
            bare_name = raw.chain[-1]
            ref = Reference(
                file=rec.path,
                line=raw.line,
                column=raw.column,
                context_snippet=_snippet(rec, raw.line),
                bare_name=bare_name,
                resolved_fqn=resolved,
                is_test_file=rec.is_test_file,
                ref_kind="call" if raw.kind == "call" else "use",
            )
            by_name[bare_name].append(ref)
            if resolved is not None:
                by_fqn[resolved].append(ref)
                if raw.kind == "call":
                    containing = _containing_definition(rec, raw.line)
                    calls_by_fqn[resolved].append(
                        CallSite(
                            file=rec.path,
                            line=raw.line,
                            column=raw.column,
                            callee_bare_name=bare_name,
                            callee_fqn=resolved,
                            containing_symbol_fqn=containing.fqn if containing else None,
                            containing_symbol_name=containing.name if containing else "<module>",
                            is_test_file=rec.is_test_file,
                        )
                    )
    return dict(by_fqn), dict(by_name), dict(calls_by_fqn)


def _snippet(rec: FileRecord, line: int) -> str:
    idx = line - 1
    return rec.source_lines[idx].strip() if 0 <= idx < len(rec.source_lines) else ""
