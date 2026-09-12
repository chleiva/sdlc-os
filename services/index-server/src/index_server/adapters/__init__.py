"""Per-language tree-sitter adapters. Each adapter parses one file and
returns (definitions, raw_refs, import_bindings, wildcard_import_modules)
-- see engine/model.py and engine/resolve.py for how these are combined
across files into the global index.
"""
