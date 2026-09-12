# orchestrator (D2) -- Docker Compose deployment packaging (D14).
#
# Honest note (see repo-root docker-compose.yml and README "Docker
# Compose deployment" section): orchestrator has **no standalone
# process/CLI entrypoint** in this codebase today -- there is no
# `__main__.py`, no console_script, nothing under src/orchestrator that
# guards `if __name__ == "__main__"`. Its own README documents only
# "Setup" (install) and "Running the tests" -- no "Run it" section,
# unlike job-dispatcher/fleet-dashboard. `orchestrator.core.Orchestrator`
# is a library class, driven in-process by whatever calls it (nothing
# in this repo does yet -- it is not wired to job-dispatcher's dispatch
# path today, one of the cross-deliverable gaps CLAUDE.md already
# tracks).
#
# So this image's job is real but narrow: build the package (including
# its new Rev 9 multi-vendor backends and Docker-sandbox tier) and run
# its own real, documented verification command
# (`python -m pytest tests/ -v`, README "Running the tests") as a
# build-time/start-time self-check, then exit. It is not a long-running
# network service and is not expected to stay "up" -- see
# docker-compose.yml's comment on this service.
#
# Several orchestrator tests spawn F3's real stub MCP servers
# (services/mcp-stubs/*/server.py) as real stdio subprocesses, so that
# sibling directory is copied in too, exactly as the README requires
# ("services/mcp-stubs/ must exist as a sibling directory").
#
# Build context: the repo root (see docker-compose.yml).

FROM python:3.14-slim AS runtime

WORKDIR /app/services
COPY services/run-registry/ /app/services/run-registry/
COPY services/mcp-stubs/ /app/services/mcp-stubs/
COPY services/orchestrator/ /app/services/orchestrator/

WORKDIR /app/services/orchestrator
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e '.[dev]' -e ../run-registry

# ORCHESTRATOR_INFERENCE_VENDOR and the per-vendor variables below match
# `create_agent_backend`'s kwargs verbatim (README "Selecting an
# inference vendor"). Flagged honestly: no process in this image reads
# them yet (there is no CLI to read them into -- see note above); they
# are set here as the forward-looking configuration surface a future
# orchestrator entrypoint (or whatever process ends up constructing
# `create_agent_backend(...)` at startup) would consume without any
# docker-compose.yml change, matching this deployment mode's "config,
# not code" requirement for vendor selection.
ENV ORCHESTRATOR_INFERENCE_VENDOR=anthropic
ENV RUN_REGISTRY_DB_PATH=/data/registry/registry.db
VOLUME ["/data/registry"]

CMD ["python", "-m", "pytest", "tests/", "-v"]
