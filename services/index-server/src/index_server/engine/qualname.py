"""Dotted-module-path helpers.

Every definition's fully-qualified name (FQN) is built from the file's
module path plus its symbol path within the file (e.g. a class-qualified
method name). Using one deterministic scheme repo-wide is what lets
find-references resolve a call in one package to a definition in another
package by name, not by accident (see engine/repo_index.py).
"""
from __future__ import annotations

from pathlib import PurePosixPath

SOURCE_SUFFIXES = (".py", ".ts", ".tsx")


def to_posix(path: str) -> str:
    return str(PurePosixPath(path))


def module_dotted_path(relative_path: str) -> str:
    """'pkg_a/billing/util.py' -> 'pkg_a.billing.util'
    'pkg_a/billing/__init__.py' -> 'pkg_a.billing'
    'pkg_b/web/formatter.ts' -> 'pkg_b.web.formatter'
    """
    p = PurePosixPath(to_posix(relative_path))
    parts = list(p.parts)
    if not parts:
        return ""
    stem = p.stem
    if stem == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1] + [stem]
    return ".".join(parts)


def fqn(module_dotted: str, *symbol_path: str) -> str:
    parts = [module_dotted, *[s for s in symbol_path if s]]
    return ".".join(p for p in parts if p)


def resolve_relative_ts_import(current_module_dotted: str, spec: str) -> str | None:
    """Resolve a TS relative import specifier ('./formatter', '../a/b') to
    a module dotted path, relative to the importing module's own package
    directory. Returns None for non-relative (bare package) specifiers --
    those are external dependencies, out of this index's scope.
    """
    if not spec.startswith("."):
        return None
    current_parts = current_module_dotted.split(".")
    # current module's directory is all-but-last dotted segment
    base_parts = current_parts[:-1]
    spec = spec[:-len(".ts")] if spec.endswith(".ts") else spec
    spec = spec[:-len(".tsx")] if spec.endswith(".tsx") else spec
    for segment in spec.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if base_parts:
                base_parts.pop()
            continue
        base_parts.append(segment)
    return ".".join(base_parts)
