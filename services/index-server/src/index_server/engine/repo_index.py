"""RepoIndex: the deterministic index for one repository (spec Sec.
6.2). Ties together the per-language adapters (parsing), resolve.py
(cross-file linking), search.py (the semantic discovery layer),
conventions.py/ownership.py (the profile layers), and scope.py
(monorepo package resolution) behind the query methods the MCP tools
call (service.py).

Incremental refresh (spec: index refresh is incremental on commit, not
a full rebuild): `refresh()` only reparses files whose content actually
changed since the last indexed commit (via git diff when the repo is a
git repository, or content-hash comparison otherwise) -- see
`parse_count`, which every incremental-refresh test asserts against, to
prove unchanged files are never reparsed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import git_utils, resolve
from .conventions import ConventionProfile, read_convention_profile
from .languages import language_for_path
from .model import CallSite, Definition, FileRecord, Reference
from .ownership import owners_for_path, parse_codeowners
from .qualname import module_dotted_path
from .scope import resolve_package
from .search import BM25Index, SearchDoc
from .summary import is_generated, summarize

IGNORED_DIR_NAMES = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}


def discover_source_files(root: Path) -> list[str]:
    out = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        if language_for_path(path.name) is None:
            continue
        out.append(path.relative_to(root).as_posix())
    return sorted(out)


def _is_test_file(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    name = Path(relative_path).name
    return "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RefreshResult:
    reparsed: list[str]
    deleted: list[str]
    commit: str | None
    mode: str  # initial | incremental | hash-diff | noop


class RepoIndex:
    def __init__(self, root: Path, repository_name: str):
        self.root = root
        self.repository_name = repository_name
        self.file_records: dict[str, FileRecord] = {}
        self.defs_by_fqn: dict[str, Definition] = {}
        self.defs_by_name: dict[str, list[Definition]] = {}
        self.module_dotted_set: set[str] = set()
        self.by_fqn_refs: dict[str, list[Reference]] = {}
        self.by_name_refs: dict[str, list[Reference]] = {}
        self.calls_by_fqn: dict[str, list[CallSite]] = {}
        self.last_indexed_commit: str | None = None
        self._bm25: BM25Index | None = None
        self.parse_count = 0  # test instrumentation: files actually reparsed, ever

    # -- parsing --------------------------------------------------------
    def _parse_file(self, relative_path: str) -> FileRecord:
        self.parse_count += 1
        text = (self.root / relative_path).read_text(encoding="utf-8", errors="replace")
        source_bytes = text.encode("utf-8", errors="replace")
        language = language_for_path(relative_path)
        if language == "python":
            from index_server.adapters import python_adapter as adapter
        else:
            from index_server.adapters import typescript_adapter as adapter
        definitions, raw_refs, import_bindings, wildcards = adapter.extract(relative_path, source_bytes)
        return FileRecord(
            path=relative_path,
            language=language,
            content_hash=_hash_text(text),
            module_dotted=module_dotted_path(relative_path),
            definitions=definitions,
            raw_refs=raw_refs,
            import_bindings=import_bindings,
            wildcard_import_modules=wildcards,
            source_lines=text.splitlines(),
            is_test_file=_is_test_file(relative_path),
            is_generated=is_generated(relative_path, text[:2000]),
            line_count=len(text.splitlines()),
        )

    def _relink(self) -> None:
        self.defs_by_fqn, self.defs_by_name, self.module_dotted_set = resolve.build_definition_indices(
            self.file_records
        )
        self.by_fqn_refs, self.by_name_refs, self.calls_by_fqn = resolve.link_references(
            self.file_records, self.defs_by_fqn, self.module_dotted_set
        )
        self._bm25 = None  # rebuilt lazily on next search() call

    def build(self) -> None:
        self.file_records = {}
        for rel in discover_source_files(self.root):
            self.file_records[rel] = self._parse_file(rel)
        self._relink()
        if git_utils.is_git_repo(self.root):
            try:
                self.last_indexed_commit = git_utils.current_commit(self.root)
            except git_utils.GitUnavailable:
                self.last_indexed_commit = None

    # -- incremental refresh ---------------------------------------------
    def refresh(self) -> RefreshResult:
        if not git_utils.is_git_repo(self.root):
            return self._refresh_by_hash()

        try:
            new_commit = git_utils.current_commit(self.root)
        except git_utils.GitUnavailable:
            return self._refresh_by_hash()

        if self.last_indexed_commit is None:
            self.build()
            return RefreshResult(sorted(self.file_records), [], self.last_indexed_commit, "initial")

        if new_commit == self.last_indexed_commit:
            return RefreshResult([], [], new_commit, "noop")

        changed, deleted = git_utils.changed_files_since(self.root, self.last_indexed_commit)
        reparsed: list[str] = []
        for rel in sorted(changed):
            if language_for_path(rel) is None:
                continue
            full = self.root / rel
            if not full.exists():
                deleted.add(rel)
                continue
            self.file_records[rel] = self._parse_file(rel)
            reparsed.append(rel)
        for rel in deleted:
            self.file_records.pop(rel, None)

        self._relink()
        self.last_indexed_commit = new_commit
        return RefreshResult(reparsed, sorted(deleted), new_commit, "incremental")

    def _refresh_by_hash(self) -> RefreshResult:
        current_files = set(discover_source_files(self.root))
        deleted = set(self.file_records) - current_files
        for rel in deleted:
            self.file_records.pop(rel, None)
        reparsed: list[str] = []
        for rel in sorted(current_files):
            text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            h = _hash_text(text)
            existing = self.file_records.get(rel)
            if existing is not None and existing.content_hash == h:
                continue
            self.file_records[rel] = self._parse_file(rel)
            reparsed.append(rel)
        self._relink()
        return RefreshResult(reparsed, sorted(deleted), None, "hash-diff")

    # -- symbol resolution -------------------------------------------------
    def _class_context_at(self, rec: FileRecord, line: int) -> tuple[str, ...]:
        containing = resolve._containing_definition(rec, line)
        if containing is None or containing.kind != "method":
            return ()
        module_parts = rec.module_dotted.split(".") if rec.module_dotted else []
        fqn_parts = containing.fqn.split(".")
        return tuple(fqn_parts[len(module_parts):-1])

    def resolve_symbol_to_fqns(self, symbol: str, origin: dict | None) -> list[str]:
        chain = tuple(part for part in symbol.split(".") if part)
        if not chain:
            return []

        if origin is not None and origin.get("file") in self.file_records:
            rec = self.file_records[origin["file"]]
            line = origin.get("line", 1)
            class_context = self._class_context_at(rec, line)
            resolved = resolve.resolve_chain(chain, class_context, rec, self.defs_by_fqn, self.module_dotted_set)
            if resolved:
                return [resolved]
            # Direct FQN match within this file's own module, ignoring
            # class context (module-level symbol referenced from inside
            # a method, e.g. a module-level helper).
            resolved = resolve.resolve_chain(chain, (), rec, self.defs_by_fqn, self.module_dotted_set)
            if resolved:
                return [resolved]

        # No origin, or origin didn't resolve it -- fall back to a
        # bare-name match against the whole index. More than one
        # definition sharing the bare name is reported as all of them
        # (e.g. overloads / same-named methods on different classes)
        # rather than arbitrarily guessing.
        candidates = self.defs_by_name.get(chain[-1], [])
        if len(chain) > 1:
            # A dotted symbol like "Invoice.total" -- prefer defs whose
            # FQN actually ends with the full dotted suffix.
            suffix = ".".join(chain)
            narrowed = [d for d in candidates if d.fqn.endswith("." + suffix) or d.fqn == suffix]
            if narrowed:
                candidates = narrowed
        return [d.fqn for d in candidates]

    def find_definition(self, symbol: str, origin: dict | None) -> list[Definition]:
        fqns = self.resolve_symbol_to_fqns(symbol, origin)
        return [self.defs_by_fqn[f] for f in fqns if f in self.defs_by_fqn]

    def find_references(
        self, symbol: str, origin: dict | None, include_test_files: bool = True
    ) -> tuple[list[Reference], bool]:
        """Returns (references, symbol_is_known). symbol_is_known is False
        only when the symbol resolves to no definition anywhere in the
        index at all (still reported as EmptyResult, never an error --
        see service.py)."""
        fqns = self.resolve_symbol_to_fqns(symbol, origin)
        if not fqns:
            return [], False

        seen: set[tuple[str, int, int]] = set()
        refs: list[Reference] = []

        def _add(r: Reference) -> None:
            key = (r.file, r.line, r.column)
            if key in seen:
                return
            seen.add(key)
            refs.append(r)

        for f in fqns:
            for r in self.by_fqn_refs.get(f, []):
                _add(r)

        bare_names = {f.split(".")[-1] for f in fqns} | {symbol.split(".")[-1]}
        for name in bare_names:
            for r in self.by_name_refs.get(name, []):
                # Exhaustiveness safety net: include unresolved same-name
                # occurrences (wildcard imports, dynamic access) but never
                # one cleanly resolved to a *different* symbol.
                if r.resolved_fqn is not None and r.resolved_fqn not in fqns:
                    continue
                _add(r)

        def_sites = {(d.file, d.line) for f in fqns for d in [self.defs_by_fqn.get(f)] if d}
        refs = [r for r in refs if (r.file, r.line) not in def_sites]
        if not include_test_files:
            refs = [r for r in refs if not r.is_test_file]
        refs.sort(key=lambda r: (r.file, r.line, r.column))
        return refs, True

    def find_callers(
        self, symbol: str, origin: dict | None, cursor: str | None = None, max_results: int = 100
    ) -> tuple[list[CallSite], str | None, bool]:
        fqns = self.resolve_symbol_to_fqns(symbol, origin)
        if not fqns:
            return [], None, False
        calls: list[CallSite] = []
        seen: set[tuple[str, int, int]] = set()
        for f in fqns:
            for c in self.calls_by_fqn.get(f, []):
                key = (c.file, c.line, c.column)
                if key in seen:
                    continue
                seen.add(key)
                calls.append(c)
        calls.sort(key=lambda c: (c.file, c.line, c.column))
        offset = int(cursor) if cursor else 0
        page = calls[offset : offset + max_results]
        next_cursor = str(offset + max_results) if offset + max_results < len(calls) else None
        return page, next_cursor, True

    # -- semantic search --------------------------------------------------
    def _ensure_bm25(self) -> BM25Index:
        if self._bm25 is None:
            docs: list[SearchDoc] = []
            for rec in self.file_records.values():
                for d in rec.definitions:
                    text = f"{d.name} {d.kind} {d.file} {d.snippet}"
                    docs.append(SearchDoc(doc_id=f"sym:{d.fqn}", file=d.file, symbol=d.name, text=text, tokens=[]))
                file_text = f"{rec.path} " + " ".join(d.name for d in rec.definitions)
                docs.append(SearchDoc(doc_id=f"file:{rec.path}", file=rec.path, symbol=None, text=file_text, tokens=[]))
            from .search import tokenize

            docs = [SearchDoc(d.doc_id, d.file, d.symbol, d.text, tokenize(d.text)) for d in docs]
            self._bm25 = BM25Index(docs)
        return self._bm25

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        bm25 = self._ensure_bm25()
        results = bm25.rank(query, max_results)
        out = []
        for doc, score in results:
            entry = {"file": doc.file, "score": round(score, 4)}
            if doc.symbol:
                entry["symbol"] = doc.symbol
            entry["snippet"] = doc.text[:160]
            out.append(entry)
        return out

    # -- file/module summary ----------------------------------------------
    def file_summary(self, path: str) -> tuple[str, int, bool] | None:
        rec = self.file_records.get(path)
        if rec is not None:
            summary = summarize(path, rec.definitions, rec.line_count, rec.is_generated)
            return summary, len(rec.definitions), rec.is_generated

        # Not one of the parsed source languages (e.g. a doc, config, or
        # unsupported-language file) -- still summarized structurally
        # rather than requiring the caller to read it whole.
        full = self.root / path
        if not full.is_file():
            return None
        text = full.read_text(encoding="utf-8", errors="replace")
        line_count = len(text.splitlines())
        generated = is_generated(path, text[:2000])
        summary = summarize(path, [], line_count, generated)
        return summary, 0, generated

    # -- ownership ----------------------------------------------------------
    def ownership(self, path: str) -> dict | None:
        """Returns {'owners': [...], 'change_frequency': {...}} or None
        when there is no ownership signal at all (EmptyResult), Raises
        git_utils.GitUnavailable when git itself is required and fails
        (mapped to upstream-unavailable by service.py)."""
        rules = parse_codeowners(self.root)
        owners = owners_for_path(rules, path)
        if not owners:
            return None

        cf = None
        if git_utils.is_git_repo(self.root):
            cf = git_utils.change_frequency(self.root, path)
        if cf is None:
            full_path = self.root / path
            last_modified = None
            if full_path.exists():
                import datetime

                last_modified = datetime.datetime.fromtimestamp(
                    full_path.stat().st_mtime, tz=datetime.timezone.utc
                ).isoformat()
            if last_modified is None:
                return None
            return {
                "owners": owners,
                "change_frequency": {"commits_last_90d": 0, "last_modified": last_modified, "top_contributors": []},
            }
        return {
            "owners": owners,
            "change_frequency": {
                "commits_last_90d": cf.commits_last_90d,
                "last_modified": cf.last_modified,
                "top_contributors": cf.top_contributors,
            },
        }

    # -- monorepo scope resolution --------------------------------------------
    def package_for(self, path: str) -> str | None:
        return resolve_package(self.root, path)

    # -- convention profile --------------------------------------------------
    def convention_profile(self) -> ConventionProfile:
        return read_convention_profile(self.root)

    def path_exists(self, path: str) -> bool:
        return (self.root / path).exists()
