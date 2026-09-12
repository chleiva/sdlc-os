"""Unit tests for the two tree-sitter adapters directly (no cross-file
linking) -- proves the deterministic-search-first layer (spec Sec. 6.2)
is built on a real parser, not regex/fuzzy matching."""
from index_server.adapters import python_adapter, typescript_adapter


def test_python_adapter_classifies_function_method_class_constant():
    src = b"""
MAX = 10

class Widget:
    def render(self):
        return MAX
"""
    defs, refs, imports, wildcards = python_adapter.extract("pkg/widget.py", src)
    by_name = {d.name: d for d in defs}
    assert by_name["MAX"].kind == "constant"
    assert by_name["Widget"].kind == "class"
    assert by_name["render"].kind == "method"
    assert by_name["render"].fqn == "pkg.widget.Widget.render"


def test_python_adapter_resolves_aliased_module_import_binding():
    src = b"import a.b.c as alias\n"
    _, _, imports, _ = python_adapter.extract("m.py", src)
    assert imports["alias"].kind == "module"
    assert imports["alias"].target == "a.b.c"


def test_python_adapter_wildcard_import_is_tracked_not_silently_dropped():
    src = b"from a.b import *\n"
    _, _, _, wildcards = python_adapter.extract("m.py", src)
    assert wildcards == ["a.b"]


def test_python_adapter_does_not_treat_keyword_argument_names_as_references():
    src = b"""
def f(x=1):
    return g(x=x)
"""
    defs, refs, _, _ = python_adapter.extract("m.py", src)
    bare_names = [r.chain[0] for r in refs if r.kind == "bare_read"]
    # 'x' is used once as a genuine value read (the call's argument
    # value), never as the keyword name itself.
    assert bare_names.count("x") == 1


def test_typescript_adapter_classifies_function_class_method_interface_type():
    src = b"""
export function f(): void {}
export interface I { a: number; }
export type T = number;
export class C {
  m(): void {}
}
"""
    defs, refs, imports, wildcards = typescript_adapter.extract("pkg/mod.ts", src)
    by_name = {d.name: d for d in defs}
    assert by_name["f"].kind == "function"
    assert by_name["I"].kind == "interface"
    assert by_name["T"].kind == "type"
    assert by_name["C"].kind == "class"
    assert by_name["m"].kind == "method"
    assert by_name["m"].fqn == "pkg.mod.C.m"


def test_typescript_adapter_resolves_relative_import_and_namespace_alias():
    src = b'import * as fmt from "./formatter";\n'
    _, _, imports, _ = typescript_adapter.extract("pkg/view.ts", src)
    assert imports["fmt"].kind == "module"
    assert imports["fmt"].target == "pkg.formatter"
