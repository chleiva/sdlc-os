"""TypeScript tree-sitter adapter -- the second language this index
covers (see engine/languages.py's module docstring for why TypeScript
was picked over Go). Deliberately narrower than the Python adapter
(functions/classes/methods/interfaces/type aliases/top-level consts,
relative-import resolution, call/member-access references) -- enough to
prove the same deterministic symbol-table + exhaustive-reference
machinery works across languages, not a full TS type-checker.
"""
from __future__ import annotations

from index_server.engine import qualname
from index_server.engine.languages import parse_source
from index_server.engine.model import Definition, ImportBinding, RawRef


def _text(node) -> str:
    return node.text.decode()


def _line_text(source_lines: list[str], row: int) -> str:
    return source_lines[row].strip() if 0 <= row < len(source_lines) else ""


def _string_literal_value(node) -> str | None:
    if node.type != "string":
        return None
    for c in node.children:
        if c.type == "string_fragment":
            return c.text.decode()
    return ""


def _flatten_member_expression(node) -> tuple[str, ...] | None:
    if node.type == "identifier":
        return (_text(node),)
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        base = _flatten_member_expression(obj) if obj is not None else None
        if base is None or prop is None:
            return None
        return base + (_text(prop),)
    return None


class _Walker:
    def __init__(self, module_dotted: str, source_lines: list[str]):
        self.module_dotted = module_dotted
        self.source_lines = source_lines
        self.definitions: list[Definition] = []
        self.raw_refs: list[RawRef] = []
        self.import_bindings: dict[str, ImportBinding] = {}
        self.wildcard_import_modules: list[str] = []
        self._visited: set[int] = set()

    def _mark_visited(self, node) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            self._visited.add(n.id)
            stack.extend(n.children)

    def _snippet_for(self, node) -> str:
        return _line_text(self.source_lines, node.start_point.row)

    def _handle_import(self, node) -> None:
        source_node = None
        for c in node.children:
            if c.type == "string":
                source_node = c
        spec = _string_literal_value(source_node) if source_node is not None else None
        target_module = qualname.resolve_relative_ts_import(self.module_dotted, spec) if spec else None

        clause = next((c for c in node.children if c.type == "import_clause"), None)
        if clause is not None and target_module is not None:
            for c in clause.children:
                if c.type == "named_imports":
                    for spec_node in c.children:
                        if spec_node.type != "import_specifier":
                            continue
                        idents = [gc for gc in spec_node.children if gc.type == "identifier"]
                        if len(idents) == 1:
                            name = _text(idents[0])
                            self.import_bindings[name] = ImportBinding(name, "symbol", f"{target_module}.{name}")
                        elif len(idents) == 2:
                            orig, alias = _text(idents[0]), _text(idents[1])
                            self.import_bindings[alias] = ImportBinding(alias, "symbol", f"{target_module}.{orig}")
                elif c.type == "namespace_import":
                    alias_node = next((gc for gc in c.children if gc.type == "identifier"), None)
                    if alias_node is not None:
                        alias = _text(alias_node)
                        self.import_bindings[alias] = ImportBinding(alias, "module", target_module)
                elif c.type == "identifier":
                    # default import: `import fmt2 from "./formatter"`.
                    alias = _text(c)
                    self.import_bindings[alias] = ImportBinding(alias, "module", target_module)
        self._mark_visited(node)

    def visit(self, node, class_stack: tuple[str, ...], scope_kind: str) -> None:
        if node.id in self._visited:
            return
        t = node.type

        if t == "import_statement":
            self._handle_import(node)
            return

        if t == "export_statement":
            for c in node.children:
                self.visit(c, class_stack, scope_kind)
            return

        if t == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for c in node.children:
                    self.visit(c, class_stack, scope_kind)
                return
            name = _text(name_node)
            kind = "method" if class_stack else "function"
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val, name=name, kind=kind, file="",
                    line=name_node.start_point.row + 1, column=name_node.start_point.column + 1,
                    end_line=node.end_point.row + 1, snippet=self._snippet_for(node),
                )
            )
            self._visited.add(name_node.id)
            params = node.child_by_field_name("parameters")
            if params is not None:
                self._mark_visited(params)  # parameter names/types: skip as refs (simplification)
            body = node.child_by_field_name("body")
            if body is not None:
                for stmt in body.children:
                    self.visit(stmt, class_stack, "function")
            return

        if t == "class_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node) if name_node is not None else "<anonymous>"
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val, name=name, kind="class", file="",
                    line=(name_node.start_point.row + 1) if name_node else node.start_point.row + 1,
                    column=(name_node.start_point.column + 1) if name_node else node.start_point.column + 1,
                    end_line=node.end_point.row + 1, snippet=self._snippet_for(node),
                )
            )
            if name_node is not None:
                self._visited.add(name_node.id)
            body = node.child_by_field_name("body")
            if body is not None:
                new_stack = class_stack + (name,)
                for stmt in body.children:
                    self.visit(stmt, new_stack, "class")
            return

        if t == "method_definition":
            name_node = node.child_by_field_name("name")
            name = _text(name_node) if name_node is not None else "<anonymous>"
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val, name=name, kind="method", file="",
                    line=(name_node.start_point.row + 1) if name_node else node.start_point.row + 1,
                    column=(name_node.start_point.column + 1) if name_node else node.start_point.column + 1,
                    end_line=node.end_point.row + 1, snippet=self._snippet_for(node),
                )
            )
            if name_node is not None:
                self._visited.add(name_node.id)
            params = node.child_by_field_name("parameters")
            if params is not None:
                self._mark_visited(params)
            body = node.child_by_field_name("body")
            if body is not None:
                for stmt in body.children:
                    self.visit(stmt, class_stack, "function")
            return

        if t == "interface_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node) if name_node is not None else "<anonymous>"
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val, name=name, kind="interface", file="",
                    line=(name_node.start_point.row + 1) if name_node else node.start_point.row + 1,
                    column=(name_node.start_point.column + 1) if name_node else node.start_point.column + 1,
                    end_line=node.end_point.row + 1, snippet=self._snippet_for(node),
                )
            )
            self._mark_visited(node)
            return

        if t == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node) if name_node is not None else "<anonymous>"
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val, name=name, kind="type", file="",
                    line=(name_node.start_point.row + 1) if name_node else node.start_point.row + 1,
                    column=(name_node.start_point.column + 1) if name_node else node.start_point.column + 1,
                    end_line=node.end_point.row + 1, snippet=self._snippet_for(node),
                )
            )
            self._mark_visited(node)
            return

        if t in ("lexical_declaration", "variable_declaration") and scope_kind in ("module", "class"):
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                name_node = decl.child_by_field_name("name")
                value_node = decl.child_by_field_name("value")
                if name_node is not None and name_node.type == "identifier":
                    name = _text(name_node)
                    kind = "constant" if name.isupper() else "variable"
                    fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
                    self.definitions.append(
                        Definition(
                            fqn=fqn_val, name=name, kind=kind, file="",
                            line=name_node.start_point.row + 1, column=name_node.start_point.column + 1,
                            end_line=decl.end_point.row + 1, snippet=self._snippet_for(decl),
                        )
                    )
                    self._visited.add(name_node.id)
                if value_node is not None:
                    self.visit(value_node, class_stack, scope_kind)
            return

        if t == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                chain = _flatten_member_expression(func_node)
                if chain is not None:
                    self.raw_refs.append(
                        RawRef(
                            kind="call", chain=chain,
                            line=func_node.start_point.row + 1, column=func_node.start_point.column + 1,
                            class_context=class_stack,
                        )
                    )
                    self._mark_visited(func_node)
                else:
                    self.visit(func_node, class_stack, scope_kind)
            args = node.child_by_field_name("arguments")
            if args is not None:
                for c in args.children:
                    self.visit(c, class_stack, scope_kind)
            return

        if t == "member_expression":
            chain = _flatten_member_expression(node)
            if chain is not None:
                self.raw_refs.append(
                    RawRef(
                        kind="attr_read", chain=chain,
                        line=node.start_point.row + 1, column=node.start_point.column + 1,
                        class_context=class_stack,
                    )
                )
                self._mark_visited(node)
                return
            for c in node.children:
                self.visit(c, class_stack, scope_kind)
            return

        if t == "identifier":
            self.raw_refs.append(
                RawRef(
                    kind="bare_read", chain=(_text(node),),
                    line=node.start_point.row + 1, column=node.start_point.column + 1,
                    class_context=class_stack,
                )
            )
            return

        for c in node.children:
            self.visit(c, class_stack, scope_kind)


def extract(relative_path: str, source: bytes):
    module_dotted = qualname.module_dotted_path(relative_path)
    language = "tsx" if relative_path.endswith(".tsx") else "typescript"
    tree = parse_source(language, source)
    source_lines = source.decode("utf-8", errors="replace").splitlines()
    walker = _Walker(module_dotted, source_lines)
    for stmt in tree.root_node.children:
        walker.visit(stmt, (), "module")
    definitions = [
        Definition(
            fqn=d.fqn, name=d.name, kind=d.kind, file=relative_path,
            line=d.line, column=d.column, end_line=d.end_line, snippet=d.snippet,
        )
        for d in walker.definitions
    ]
    return definitions, walker.raw_refs, walker.import_bindings, walker.wildcard_import_modules
