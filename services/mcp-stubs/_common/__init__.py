"""Shared runtime code for the F3 MCP stub servers.

Not a server itself -- every server under services/mcp-stubs/<name>/server.py
imports from here so the envelope shape, the tenant_id-driven scenario
dispatch, and the schema-validation-before-return logic exist in exactly one
place instead of being re-implemented per server and quietly drifting.
"""
