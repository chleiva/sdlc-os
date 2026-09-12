"""SourceControlService exposed as an MCP-shaped server, implementing F3's
source-control contract for real (services/mcp-stubs/source-control/schema/
source-control.schema.json) -- same tool names, same input/output shapes,
backed by a real GitHub App installation and real local git worktrees
instead of canned data.

Thin binding only: every tool function here is a pass-through to
`SourceControlService`, which already returns the exact `to_wire()` dict
shape the schema's `output_schema` expects. Nothing here re-interprets
that payload.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from source_control.service import InstallationRegistry, SourceControlService


def build_server(registry: InstallationRegistry) -> MCPServer:
    service = SourceControlService(registry)
    server = MCPServer(
        name="source-control",
        title="Source-Control Service (GitHub App)",
        description=(
            "Real GitHub App-backed source-control MCP server (master spec "
            "Section 15 / Section 17.1). Repository access is always via a "
            "GitHub App installation token, never a personal access token."
        ),
        version="1.0.0",
    )

    @server.tool(name="create-branch-worktree", description="Create a branch and its dedicated worktree for one agent/session.")
    def create_branch_worktree(
        tenant_id: str, repository: str, base_ref: str, branch_name: str, session_id: str,
    ) -> dict[str, Any]:
        return service.create_branch_worktree(
            tenant_id=tenant_id, repository=repository, base_ref=base_ref,
            branch_name=branch_name, session_id=session_id,
        )

    @server.tool(name="open-pr", description="Open a PR against the org's existing PR flow with a generated description.")
    def open_pr(
        tenant_id: str, repository: str, head_branch: str, base_branch: str,
        title: str, description: str, draft: bool = False,
    ) -> dict[str, Any]:
        return service.open_pr(
            tenant_id=tenant_id, repository=repository, head_branch=head_branch,
            base_branch=base_branch, title=title, description=description, draft=draft,
        )

    @server.tool(name="get-pr-check-status", description="Poll PR and CI check status without re-implementing test execution.")
    def get_pr_check_status(tenant_id: str, repository: str, pr_number: int) -> dict[str, Any]:
        return service.get_pr_check_status(tenant_id=tenant_id, repository=repository, pr_number=pr_number)

    @server.tool(name="get-file-contents", description="Read-only file contents, scoped to the branch the calling run owns.")
    def get_file_contents(tenant_id: str, repository: str, branch: str, path: str) -> dict[str, Any]:
        return service.get_file_contents(tenant_id=tenant_id, repository=repository, branch=branch, path=path)

    @server.tool(name="list-files", description="Read-only directory listing, scoped to the branch the calling run owns.")
    def list_files(
        tenant_id: str, repository: str, branch: str, path_prefix: str = "", recursive: bool = True,
    ) -> dict[str, Any]:
        return service.list_files(
            tenant_id=tenant_id, repository=repository, branch=branch,
            path_prefix=path_prefix, recursive=recursive,
        )

    return server


def main() -> None:  # pragma: no cover - process entry point
    # A real deployment constructs an InstallationRegistry from the
    # tenant's own KMS-wrapped secret store (spec Section 17.3) at
    # process start -- never from a process environment variable (that
    # shape is exactly what a PAT-based integration would look like) --
    # see SETUP.md for what a human operator still has to configure.
    raise SystemExit(
        "source_control.mcp_server is a library entry point; a real deployment must "
        "construct an InstallationRegistry from its own per-tenant KMS-backed "
        "secret store and call build_server(registry).run() -- see SETUP.md."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
