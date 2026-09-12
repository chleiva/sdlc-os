"""Semantic/natural-language discovery layer (spec Sec. 6.2/6.3).

Choice of BM25 over an embedding model, documented: an embedding model
would need network-fetched weights (or a bundled multi-hundred-MB
model) that this server has no business assuming are available in every
tenant's deployment environment (Sec. 14.13 -- this runs per tenant,
next to tenant source code, and should not have an undeclared outbound
model-download dependency). BM25 over tokenized identifiers + docstrings
+ file paths is a real, corpus-statistics-driven ranking (not a stub),
requires no external service or model file, and -- most importantly for
this contract -- is trivially and honestly labeled non-authoritative:
it is lexical/statistical, not a semantic embedding, so no caller could
mistake its ranking for the deterministic graph's precision. If a real
embedding model is later wired in, this module's `rank()` interface does
not need to change.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _split_camel_snake(token: str) -> list[str]:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)
    s = s.replace("_", " ").replace("-", " ").replace(".", " ").replace("/", " ")
    return [t.lower() for t in s.split() if t]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        tokens.extend(_split_camel_snake(raw))
    return tokens


@dataclass(frozen=True)
class SearchDoc:
    doc_id: str  # opaque handle back to the caller (e.g. "file:path" or "sym:fqn")
    file: str
    symbol: str | None
    text: str  # raw text used to build the snippet
    tokens: list[str]


class BM25Index:
    """A minimal, dependency-free BM25 index. Rebuilt wholesale on every
    (re)index -- this is cheap (pure Python dict counting over a few
    thousand short documents at most) relative to tree-sitter parsing,
    so it is not part of the incremental-refresh optimization; only
    parsing is (engine/repo_index.py).
    """

    k1 = 1.5
    b = 0.75

    def __init__(self, docs: list[SearchDoc]):
        self.docs = docs
        self.doc_freqs: list[Counter] = [Counter(d.tokens) for d in docs]
        self.doc_lens = [len(d.tokens) for d in docs]
        self.avg_len = (sum(self.doc_lens) / len(self.doc_lens)) if docs else 0.0
        df: Counter = Counter()
        for freqs in self.doc_freqs:
            for term in freqs:
                df[term] += 1
        n = len(docs)
        self.idf = {
            term: math.log(1 + (n - count + 0.5) / (count + 0.5)) for term, count in df.items()
        }

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        freqs = self.doc_freqs[doc_index]
        dl = self.doc_lens[doc_index] or 1
        total = 0.0
        for term in query_tokens:
            if term not in freqs:
                continue
            idf = self.idf.get(term, 0.0)
            f = freqs[term]
            total += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1)))
        return total

    def rank(self, query: str, max_results: int) -> list[tuple[SearchDoc, float]]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.docs:
            return []
        scored = [(d, self.score(query_tokens, i)) for i, d in enumerate(self.docs)]
        scored = [(d, s) for d, s in scored if s > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        top = scored[:max_results]
        if not top:
            return []
        max_score = top[0][1] or 1.0
        # Normalize into the contract's [0, 1] score range without
        # pretending it is a probability -- purely a display convenience.
        return [(d, min(1.0, s / max_score)) for d, s in top]
