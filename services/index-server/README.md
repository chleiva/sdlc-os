# index-server — D3: Repository Index Server

Wave 1 deliverable. The real implementation behind F3's index-server MCP
contract: a deterministic symbol/reference/call-graph index (via
tree-sitter, for Python and TypeScript), a non-authoritative semantic
search layer (BM25 — no embedding model, so no undeclared network/model-
weight dependency inside a per-tenant deployment), convention-profile and
CODEOWNERS/change-frequency reading, and incremental (not full-rebuild)
refresh on commit.

Find-references is exhaustive by design (spec §6.6): a rename must
surface every reference across the whole index, including packages
outside the "obviously affected" one — this is what the verification
pipeline's cross-codebase completion check (D7) relies on.

## Install & run tests

```bash
cd services/index-server
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

53 tests, entirely against a local multi-package fixture repo under
`tests/fixtures/` — no external services required.

## Run the server

```bash
python src/index_server/server.py   # real MCP server over stdio
```

## Consumed by

D7 (verification pipeline, for the completion check) and D9 (gates, for
CODEOWNERS-based reviewer resolution) — both import this package's real
code directly rather than reimplementing index/CODEOWNERS parsing.
