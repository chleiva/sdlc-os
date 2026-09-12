"""AC5, structural half: "No level, including L3, ever produces a merge
or deploy action without a separate explicit approval outside this
deliverable's own authority."

A fitness test, not a behavioral one: it inspects this deliverable's own
source tree (AST-based, not a fragile string grep) for any call
expression whose method/attribute name suggests a merge or deploy
action, and independently double-checks the one dependency this
deliverable calls into for source control (D5's `GitHubAppClient`/
`SourceControlService`) genuinely has no such method to call in the
first place -- so the floor holds even if a future change to this
deliverable tried to add one.
"""
from __future__ import annotations

import ast
from pathlib import Path

GATES_SRC = Path(__file__).resolve().parent.parent / "src" / "gates"
SOURCE_CONTROL_SRC = Path(__file__).resolve().parent.parent.parent / "source-control" / "src" / "source_control"

# Name fragments that would indicate a merge/deploy-capable call. Kept
# broad and case-insensitive on purpose -- a false positive just means
# a human reads one extra line; a false negative is the actual risk
# this test exists to catch.
FORBIDDEN_NAME_FRAGMENTS = ("merge", "deploy")

# `pr.get("merged")` / a `state == "merged"` string comparison are
# legitimate *read-only* inspection of a PR's status (source_control/
# service.py's get_pr_check_status-adjacent code) -- allow the literal
# string "merged" as data, but not a call to anything named like a verb
# that would perform one.
ALLOWED_ATTRIBUTE_EXACT = set()  # nothing is allowlisted for *this* deliverable's own tree


def _iter_py_files(root: Path):
    return sorted(root.rglob("*.py"))


def _call_names(tree: ast.AST):
    """Yields every function/method *name* actually being called (the
    trailing attribute or bare name of a `Call` node), across the
    whole file -- catches `x.merge_pr(...)`, `merge(...)`,
    `client.deploy_something(...)`, etc."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            yield func.attr
        elif isinstance(func, ast.Name):
            yield func.id


def test_no_call_in_this_deliverables_source_names_a_merge_or_deploy_action():
    offenders = []
    for path in _iter_py_files(GATES_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _call_names(tree):
            lowered = name.lower()
            if any(fragment in lowered for fragment in FORBIDDEN_NAME_FRAGMENTS) and name not in ALLOWED_ATTRIBUTE_EXACT:
                offenders.append(f"{path.relative_to(GATES_SRC.parent.parent)}: call to {name!r}")
    assert offenders == [], (
        "found a call in services/gates/src whose name suggests a merge/deploy action -- "
        f"L3's absolute floor (Sec. 12) forbids this structurally:\n" + "\n".join(offenders)
    )


def test_no_merge_or_deploy_keyword_argument_or_string_literal_action_in_this_deliverable():
    """Belt-and-braces: also flag any string literal that looks like a
    merge/deploy action name (e.g. a dict key someone might route a
    dispatch through), not just direct calls."""
    offenders = []
    for path in _iter_py_files(GATES_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lowered = node.value.lower()
                if "merge_pr" in lowered or "auto_merge" in lowered or "deploy_action" in lowered:
                    offenders.append(f"{path}: literal {node.value!r}")
    assert offenders == []


def test_the_only_source_control_dependency_this_deliverable_calls_has_no_merge_capable_method():
    """Double-checks the floor from the other side: even if this
    deliverable's own code were changed to call it, D5's real
    SourceControlService/GitHubAppClient exposes no method whose name
    suggests it could merge a PR or trigger a deploy -- there is
    nothing to call. (Read-only inspection of a sibling deliverable's
    source; this test never modifies services/source-control/.)"""
    offenders = []
    for path in _iter_py_files(SOURCE_CONTROL_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                lowered = node.name.lower()
                if "merge" in lowered or "deploy" in lowered:
                    offenders.append(f"{path}: def {node.name}")
    assert offenders == [], (
        "services/source-control defines a merge/deploy-named method -- re-verify D9's floor still holds:\n"
        + "\n".join(offenders)
    )
