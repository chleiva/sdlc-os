"""tree-sitter grammar loading.

Deterministic-search-first (spec Sec. 6.2) means definitions/references/
calls come from real language-aware parsers, not regex. This server
parses two languages for real:

  * Python   -- the primary language, via tree-sitter-python.
  * TypeScript -- the second language (chosen over Go because the
    fixture repo and most of this repo's own future services are
    Python+TypeScript-shaped web/service code; the adapter pattern in
    adapters/ makes adding a third grammar, e.g. Go, an isolated,
    additive change, not a rewrite).

Both grammar packages ship prebuilt wheels (no C toolchain needed at
install time), which is why they -- rather than compiling grammars from
source -- were picked for a real, run-anywhere implementation.
"""
from __future__ import annotations

from functools import lru_cache

import tree_sitter_python as _tspython
import tree_sitter_typescript as _tstypescript
from tree_sitter import Language, Parser

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@lru_cache(maxsize=None)
def _language(name: str) -> Language:
    if name == "python":
        return Language(_tspython.language())
    if name == "typescript":
        return Language(_tstypescript.language_typescript())
    if name == "tsx":
        return Language(_tstypescript.language_tsx())
    raise ValueError(f"Unsupported language: {name}")


@lru_cache(maxsize=None)
def parser_for(name: str) -> Parser:
    return Parser(_language(name))


def language_for_path(path: str) -> str | None:
    for suffix, lang in EXTENSION_TO_LANGUAGE.items():
        if path.endswith(suffix):
            return lang
    return None


def parse_source(language: str, source: bytes):
    return parser_for(language).parse(source)
