"""D5: real GitHub App-backed source-control MCP server (SDLC Auto, Wave 1).

Public entry points live in `service.py` (SourceControlService) and
`mcp_server.py` (the MCP transport binding). This module deliberately
re-exports nothing from internal modules (app_auth, github_client,
git_ops) -- mirroring F2's run_registry package convention that the
package's public surface is not a shortcut to internals.
"""
