"""Core data model for the deterministic index (spec Sec. 6.2).

Everything here is plain, serialization-friendly dataclasses -- no
tree-sitter objects escape the `adapters/` layer that produces these.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Definition:
    """One definition site of a symbol."""

    fqn: str  # globally unique dotted name, e.g. "pkg_a.billing.util.compute_total"
    name: str  # bare symbol name, e.g. "compute_total"
    kind: str  # function | method | class | interface | variable | constant | type | module
    file: str  # posix path, relative to repo root
    line: int  # 1-based
    column: int  # 1-based
    end_line: int
    snippet: str


@dataclass(frozen=True)
class Reference:
    """One occurrence of a symbol being used (not defined)."""

    file: str
    line: int
    column: int
    context_snippet: str
    bare_name: str
    resolved_fqn: str | None  # None => could not be attributed to one definition
    is_test_file: bool
    ref_kind: str = "use"  # use | call | import


@dataclass(frozen=True)
class CallSite:
    """One call expression, resolved (where possible) to a callee FQN."""

    file: str
    line: int
    column: int
    callee_bare_name: str
    callee_fqn: str | None
    containing_symbol_fqn: str | None
    containing_symbol_name: str
    is_test_file: bool


@dataclass(frozen=True)
class RawRef:
    """An unresolved occurrence found during per-file parsing (adapters/),
    before cross-file linking (engine/resolve.py) turns it into a
    Reference/CallSite. `chain` is the dotted-token sequence as written,
    e.g. ('pkg_a', 'billing', 'util', 'compute_total') for a fully
    qualified attribute access, or ('compute_total',) for a bare name.
    """

    kind: str  # call | attr_read | bare_read | import_symbol | import_module
    chain: tuple[str, ...]
    line: int
    column: int
    class_context: tuple[str, ...]  # enclosing class-name path, for self/cls resolution


@dataclass(frozen=True)
class ImportBinding:
    """One local name bound by an import statement in a file."""

    local_name: str
    kind: str  # 'symbol' (from X import Y) or 'module' (import X as Y)
    target: str  # dotted target: 'pkg.mod.Y' for symbol, 'pkg.mod' for module


@dataclass
class FileRecord:
    """Everything the index knows about one source file, so an
    incremental refresh can drop and rebuild exactly this file's
    contribution without touching any other file's data (Sec. 6.2/6.4
    incremental-refresh requirement)."""

    path: str  # posix, relative to repo root
    language: str
    content_hash: str
    module_dotted: str
    definitions: list[Definition] = field(default_factory=list)
    raw_refs: list[RawRef] = field(default_factory=list)
    import_bindings: dict[str, ImportBinding] = field(default_factory=dict)
    wildcard_import_modules: list[str] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list, repr=False)
    is_test_file: bool = False
    is_generated: bool = False
    line_count: int = 0
