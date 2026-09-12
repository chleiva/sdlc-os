"""Registry Service exposed as an MCP-shaped server (spec Section 7.6).

We use the official Python MCP SDK (`mcp`, pinned to 2.2.0 -- see
pyproject.toml) as the transport/tool-registration layer: `MCPServer`
from `mcp.server.mcpserver` (the 2.x replacement for the 1.x `FastMCP`)
gives each operation a named tool, an inferred typed-input schema from
the function signature, and a documented result payload for free, which
is exactly Section 7.6's contract shape.

Design choice, and why: the SDK's own transport-level `is_error` flag is
a protocol-level concept (did the tool call itself blow up), not the
same thing as this service's own named error taxonomy
(not-found/permission-denied/rate-limited/upstream-unavailable, plus the
Registry-specific illegal-transition/stale-version/invalid-input). So
every tool function here stays a thin pass-through to a
`RegistryService` method and returns that method's `Result.to_wire()`
dict as the tool's (successful, from MCP's point of view) structured
content -- the *domain* outcome (ok / empty / error) lives inside that
payload exactly as `service.py` produced it, never re-encoded or
re-interpreted at this layer. This keeps the contract-tested logic in
`service.py` (plain, synchronous, easy to unit test without a transport)
and this module a deliberately thin binding on top of it -- see the F2
report for the fuller rationale.

`RegistryService.get_run`/`list_runs`/etc. already do their own
tenant-scoped, fail-closed work; nothing here bypasses that or touches
`_internal.db` directly.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver import MCPServer

from run_registry.models import Attempt, Run
from run_registry.result import Result
from run_registry.service import RegistryService


def _run_encoder(run: Run) -> dict[str, Any]:
    return asdict(run)


def _runs_encoder(runs: list[Run]) -> list[dict[str, Any]]:
    return [asdict(r) for r in runs]


def _attempt_encoder(attempt: Attempt) -> dict[str, Any]:
    return asdict(attempt)


def _attempts_encoder(attempts: list[Attempt]) -> list[dict[str, Any]]:
    return [asdict(a) for a in attempts]


def build_server(db_path: str) -> MCPServer:
    """Construct the MCP server bound to one RegistryService instance.

    One `RegistryService` (and therefore one underlying DB connection)
    per server instance -- this function is the only place outside of
    tests that is allowed to construct a `RegistryService`.
    """
    service = RegistryService(db_path)
    server = MCPServer(
        name="run-registry",
        title="Run Registry Service",
        description=(
            "Single read/write path for Run and Attempt state "
            "(master spec Section 14.14). No caller ever gets a direct "
            "database connection."
        ),
        version="0.1.0",
    )

    @server.tool(name="create_run", description="Create a new Run at the intake stage.")
    def create_run(
        tenant_id: str,
        jira_key: str,
        repo: str,
        branch: str,
        capacity_class: str,
        trace_id: str,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        result: Result[Run] = service.create_run(
            tenant_id=tenant_id,
            jira_key=jira_key,
            repo=repo,
            branch=branch,
            capacity_class=capacity_class,
            trace_id=trace_id,
            worktree=worktree,
        )
        return result.to_wire(_run_encoder)

    @server.tool(name="get_run", description="Read one Run by ID, scoped to tenant_id.")
    def get_run(tenant_id: str, run_id: str) -> dict[str, Any]:
        result = service.get_run(tenant_id=tenant_id, run_id=run_id)
        return result.to_wire(_run_encoder)

    @server.tool(name="list_runs", description="List/query Runs scoped to tenant_id.")
    def list_runs(
        tenant_id: str,
        stage: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        result = service.list_runs(tenant_id=tenant_id, stage=stage, limit=limit, offset=offset)
        return result.to_wire(_runs_encoder)

    @server.tool(name="list_attempts", description="Full Attempt history for one Run (append-only).")
    def list_attempts(tenant_id: str, run_id: str) -> dict[str, Any]:
        result = service.list_attempts(tenant_id=tenant_id, run_id=run_id)
        return result.to_wire(_attempts_encoder)

    @server.tool(name="append_attempt", description="Append a new immutable Attempt to a Run.")
    def append_attempt(
        tenant_id: str,
        run_id: str,
        expected_version: int,
        reason: str,
        starting_stage: str,
        trace_id: str,
    ) -> dict[str, Any]:
        result = service.append_attempt(
            tenant_id=tenant_id,
            run_id=run_id,
            expected_version=expected_version,
            reason=reason,
            starting_stage=starting_stage,
            trace_id=trace_id,
        )
        return result.to_wire(_attempt_encoder)

    @server.tool(name="transition_stage", description="Write a validated stage transition.")
    def transition_stage(
        tenant_id: str, run_id: str, expected_version: int, next_stage: str
    ) -> dict[str, Any]:
        result = service.transition_stage(
            tenant_id=tenant_id,
            run_id=run_id,
            expected_version=expected_version,
            next_stage=next_stage,
        )
        return result.to_wire(_run_encoder)

    @server.tool(name="write_checkpoint", description="Write the checkpoint pointer (+ optional capacity class).")
    def write_checkpoint(
        tenant_id: str,
        run_id: str,
        expected_version: int,
        checkpoint_pointer: str,
        capacity_class: str | None = None,
    ) -> dict[str, Any]:
        result = service.write_checkpoint(
            tenant_id=tenant_id,
            run_id=run_id,
            expected_version=expected_version,
            checkpoint_pointer=checkpoint_pointer,
            capacity_class=capacity_class,
        )
        return result.to_wire(_run_encoder)

    @server.tool(name="write_execution_location", description="Write the current execution location.")
    def write_execution_location(
        tenant_id: str,
        run_id: str,
        expected_version: int,
        cloud_provider: str | None = None,
        region_az: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        result = service.write_execution_location(
            tenant_id=tenant_id,
            run_id=run_id,
            expected_version=expected_version,
            cloud_provider=cloud_provider,
            region_az=region_az,
            node_id=node_id,
        )
        return result.to_wire(_run_encoder)

    return server


def main() -> None:
    import os

    db_path = os.environ.get("RUN_REGISTRY_DB_PATH", "run_registry.db")
    server = build_server(db_path)
    server.run()


if __name__ == "__main__":
    main()
