"""D4 -- Issue-Tracker Integration (Jira Cloud REST API v3).

Implements F3's issue-tracker MCP contract for real
(services/mcp-stubs/issue-tracker/schema/issue-tracker.schema.json),
against Jira Cloud REST API v3, per master spec Sec. 4.4.

What is real here: HTTP request/response shapes for Jira Cloud REST API
v3 (create epic/story, get issue, transition, comment, issue links),
the opt-in gating logic, the fixed gate-transition allowlist, and the
per-tenant HMAC webhook-signing scheme (Sec. 17.3). What is mocked:
the Jira server and the Atlassian Rovo MCP server on the other end of
those HTTP calls -- see mocks/ and SETUP.md for the exact boundary.
"""
