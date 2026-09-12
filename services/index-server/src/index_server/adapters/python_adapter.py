"""Python tree-sitter adapter: one file's source -> Definitions + RawRefs +
ImportBindings. No cross-file knowledge is used or needed here -- that is
exactly what makes per-file (re)parsing safe to run incrementally
(engine/repo_index.py only reparses files that actually changed).
"""
from __future__ import annotations

from index_server.engine import qualname
from index_server.engine.languages import parse_source
from index_server.engine.model import Definition, ImportBinding, RawRef

_SKIP_AS_REF_PARENTS = {
    "function_definition",  # name field
    "class_definition",  # name field
}


def _line_text(source_lines: list[str], row: int) -> str:
    return source_lines[row].strip() if 0 <= row < len(source_lines) else ""


def _flatten_dotted(node) -> tuple[str, ...] | None:
    """Flatten a pure identifier/attribute chain node into a token tuple,
    e.g. 'pkg_a.billing.util.compute_total' -> (pkg_a, billing, util,
    compute_total). Returns None if any part of the chain isn't a plain
    name (e.g. it's a call or subscript), since that isn't a resolvable
    dotted reference.
    """
    if node.type == "identifier":
        return (node.text.decode(),)
    if node.type == "dotted_name":
        return tuple(c.text.decode() for c in node.children if c.type == "identifier")
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        base = _flatten_dotted(obj) if obj is not None else None
        if base is None or attr is None:
            return None
        return base + (attr.text.decode(),)
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

    # -- helpers ----------------------------------------------------
    def _mark_visited(self, node) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            self._visited.add(n.id)
            stack.extend(n.children)

    def _snippet_for(self, node) -> str:
        return _line_text(self.source_lines, node.start_point.row)

    # -- imports ------------------------------------------------------
    def _handle_import_statement(self, node) -> None:
        # import a.b.c [as alias]. A plain (unaliased) 'import a.b.c' binds
        # only the top-level name 'a' in real Python scoping -- but any
        # code that then writes the full 'a.b.c.symbol' chain is still
        # resolved by resolve.py's global module-prefix matching, so no
        # local binding is needed (or created) for that case; only the
        # 'as alias' form needs one, since 'alias' isn't a real dotted
        # prefix resolve.py could otherwise discover.
        for child in node.children:
            if child.type == "aliased_import":
                dotted = child.child_by_field_name("name") or next(
                    (c for c in child.children if c.type == "dotted_name"), None
                )
                alias = next((c for c in child.children if c.type == "identifier"), None)
                if dotted is not None and alias is not None:
                    target = ".".join(c.text.decode() for c in dotted.children if c.type == "identifier")
                    self.import_bindings[alias.text.decode()] = ImportBinding(alias.text.decode(), "module", target)
        self._mark_visited(node)

    def _handle_import_from_statement(self, node) -> None:
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            module_node = next((c for c in node.children if c.type == "dotted_name"), None)
        module_dotted = (
            ".".join(c.text.decode() for c in module_node.children if c.type == "identifier")
            if module_node is not None
            else ""
        )
        module_node_id = module_node.id if module_node is not None else None
        for child in node.children:
            if child.type == "wildcard_import":
                self.wildcard_import_modules.append(module_dotted)
            elif child.type == "dotted_name" and child.id != module_node_id:
                name = ".".join(c.text.decode() for c in child.children if c.type == "identifier")
                self.import_bindings[name] = ImportBinding(name, "symbol", f"{module_dotted}.{name}")
            elif child.type == "aliased_import":
                name_node = next((c for c in child.children if c.type == "dotted_name"), None)
                alias = next((c for c in child.children if c.type == "identifier"), None)
                if name_node is not None and alias is not None:
                    name = ".".join(c.text.decode() for c in name_node.children if c.type == "identifier")
                    self.import_bindings[alias.text.decode()] = ImportBinding(
                        alias.text.decode(), "symbol", f"{module_dotted}.{name}"
                    )
        self._mark_visited(node)

    # -- main walk ------------------------------------------------------
    def visit(self, node, class_stack: tuple[str, ...], scope_kind: str) -> None:
        if node.id in self._visited:
            return
        t = node.type

        if t == "import_statement":
            self._handle_import_statement(node)
            return
        if t == "import_from_statement":
            self._handle_import_from_statement(node)
            return

        if t == "function_definition":
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode()
            kind = "method" if class_stack else "function"
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val,
                    name=name,
                    kind=kind,
                    file="",  # filled in by caller
                    line=name_node.start_point.row + 1,
                    column=name_node.start_point.column + 1,
                    end_line=node.end_point.row + 1,
                    snippet=self._snippet_for(node),
                )
            )
            self._visited.add(name_node.id)
            params = node.child_by_field_name("parameters")
            if params is not None:
                self._visit_parameters(params, class_stack)
            body = node.child_by_field_name("body")
            if body is not None:
                for stmt in body.children:
                    self.visit(stmt, class_stack, "function")
            return

        if t == "class_definition":
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode()
            fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
            self.definitions.append(
                Definition(
                    fqn=fqn_val,
                    name=name,
                    kind="class",
                    file="",
                    line=name_node.start_point.row + 1,
                    column=name_node.start_point.column + 1,
                    end_line=node.end_point.row + 1,
                    snippet=self._snippet_for(node),
                )
            )
            self._visited.add(name_node.id)
            superclasses = node.child_by_field_name("superclasses")
            if superclasses is not None:
                self.visit(superclasses, class_stack, scope_kind)
            body = node.child_by_field_name("body")
            if body is not None:
                new_stack = class_stack + (name,)
                for stmt in body.children:
                    self.visit(stmt, new_stack, "class")
            return

        if t == "expression_statement" and scope_kind in ("module", "class"):
            inner = node.children[0] if node.children else None
            if inner is not None and inner.type == "assignment":
                left = inner.child_by_field_name("left")
                right = inner.child_by_field_name("right")
                if left is not None and left.type == "identifier":
                    name = left.text.decode()
                    kind = "constant" if name.isupper() else "variable"
                    fqn_val = qualname.fqn(self.module_dotted, *class_stack, name)
                    self.definitions.append(
                        Definition(
                            fqn=fqn_val,
                            name=name,
                            kind=kind,
                            file="",
                            line=left.start_point.row + 1,
                            column=left.start_point.column + 1,
                            end_line=inner.end_point.row + 1,
                            snippet=self._snippet_for(inner),
                        )
                    )
                    self._visited.add(left.id)
                if right is not None:
                    self.visit(right, class_stack, scope_kind)
                return

        if t == "keyword_argument":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node is not None:
                self._visited.add(name_node.id)
            if value_node is not None:
                self.visit(value_node, class_stack, scope_kind)
            return

        if t == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                chain = _flatten_dotted(func_node)
                if chain is not None:
                    self.raw_refs.append(
                        RawRef(
                            kind="call",
                            chain=chain,
                            line=func_node.start_point.row + 1,
                            column=func_node.start_point.column + 1,
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

        if t == "attribute":
            chain = _flatten_dotted(node)
            if chain is not None:
                self.raw_refs.append(
                    RawRef(
                        kind="attr_read",
                        chain=chain,
                        line=node.start_point.row + 1,
                        column=node.start_point.column + 1,
                        class_context=class_stack,
                    )
                )
                self._mark_visited(node)
                return
            # Not a pure dotted chain (e.g. call().attr) -- recurse normally.
            for c in node.children:
                self.visit(c, class_stack, scope_kind)
            return

        if t == "identifier":
            self.raw_refs.append(
                RawRef(
                    kind="bare_read",
                    chain=(node.text.decode(),),
                    line=node.start_point.row + 1,
                    column=node.start_point.column + 1,
                    class_context=class_stack,
                )
            )
            return

        for c in node.children:
            self.visit(c, class_stack, scope_kind)

    def _visit_parameters(self, params_node, class_stack: tuple[str, ...]) -> None:
        for p in params_node.children:
            if p.type == "identifier":
                self._visited.add(p.id)
            elif p.type in ("default_parameter", "typed_default_parameter", "typed_parameter"):
                name_node = p.child_by_field_name("name")
                if name_node is not None:
                    self._visited.add(name_node.id)
                value_node = p.child_by_field_name("value")
                if value_node is not None:
                    self.visit(value_node, class_stack, "function")


def extract(relative_path: str, source: bytes):
    """Parse one Python file. Returns (definitions, raw_refs,
    import_bindings, wildcard_import_modules); `file` on each Definition
    is left blank -- the caller (engine/repo_index.py) stamps it in
    along with the resolved module path.
    """
    module_dotted = qualname.module_dotted_path(relative_path)
    tree = parse_source("python", source)
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
