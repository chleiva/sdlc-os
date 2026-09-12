"""Minimal Atlassian Document Format (ADF) helpers.

Jira Cloud REST API v3 (unlike v2) requires `description` and comment
`body` fields to be ADF -- a JSON document tree -- not plain strings.
This is one of the concrete differences a "real v3" client must get
right that a naive v2-shaped client would miss. We only need enough ADF
to carry plain-text/markdown-ish plan and verification-report content;
we do not implement the full ADF mark/node vocabulary (tables, panels,
etc.) since D2/D7 (who decide *what* to write) are not in scope here --
D4 only posts what it is given, as plain paragraphs.
"""

from __future__ import annotations

from typing import Any


def text_to_adf(body: str) -> dict[str, Any]:
    """Render plain text as an ADF document: one paragraph per line,
    blank lines preserved as empty paragraphs so simple formatting
    (e.g. a plan's line breaks) survives the round trip.
    """
    paragraphs: list[dict[str, Any]] = []
    for line in body.split("\n"):
        if line == "":
            paragraphs.append({"type": "paragraph", "content": []})
        else:
            paragraphs.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                }
            )
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": paragraphs}


def adf_to_text(doc: dict[str, Any] | str | None) -> str:
    """Best-effort inverse of `text_to_adf`, for reading back issue
    descriptions/comments in tests and in `get-issue` result mapping.
    Accepts a plain string too (defensive: some mocked/legacy payloads
    may carry v2-shaped plain-string descriptions).
    """
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc
    lines: list[str] = []
    for node in doc.get("content", []):
        if node.get("type") != "paragraph":
            continue
        lines.append("".join(part.get("text", "") for part in node.get("content", [])))
    return "\n".join(lines)


def is_empty_adf_or_text(body: dict[str, Any] | str | None) -> bool:
    """True if a rendered ADF doc (or plain string) has no visible text --
    used by `post-comment`'s EmptyResult branch (schema: "comment body was
    empty after template rendering").
    """
    return adf_to_text(body).strip() == ""
