# run-registry (F2) -- Docker Compose deployment packaging (D14).
#
# Honest note (see repo-root docker-compose.yml and README "Docker
# Compose deployment" section for the full explanation): run-registry
# has no network-reachable server process in this codebase today. Per
# its own README ("Consumed by"), every other service consumes it as an
# *embedded library* (`pip install -e ../run-registry`, then
# `from run_registry import RegistryService` in-process) against a
# shared SQLite file -- never a client/server call. A `mcp_server.py`
# module exists in src/run_registry/, but it is not documented in the
# README as a way to run this service, and even if invoked, the
# `mcp.server.mcpserver` SDK it uses defaults to the stdio transport
# (see MCPServer.run()), which is not reachable across a Docker network
# boundary anyway.
#
# So this image's job is real but narrow: build the package, and apply
# its migrations (a real, documented CLI --
# `python -m run_registry.migrate` -- see migrate.py's own module
# docstring) against the shared SQLite file on the `run-registry-data`
# named volume, once, before any other container opens that file. It
# then exits 0. It is not a long-running service and is not expected to
# be "up" in `docker compose ps` -- see docker-compose.yml's
# `service_completed_successfully` dependency condition on it.
#
# Build context: the repo root (see docker-compose.yml) -- this
# Dockerfile only ever COPYs its own directory, but uses the repo-root
# context so every service Dockerfile follows the same convention.

FROM python:3.14-slim AS runtime

# Real, minimal build deps: run-registry itself is pure-Python +
# stdlib sqlite3, no compiled extensions -- gcc etc. are not needed.
WORKDIR /app/services/run-registry
COPY services/run-registry/ /app/services/run-registry/

# Matches README.md's documented install step exactly
# (`pip install -e ".[dev]"`); the `dev` extra only adds pytest, which
# this image does not use at runtime but which costs nothing to carry
# for parity with the documented command.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e ".[dev]"

# Shared SQLite file lives on a named volume mounted here by
# docker-compose.yml for every service that touches the Registry.
ENV RUN_REGISTRY_DB_PATH=/data/registry/registry.db
VOLUME ["/data/registry"]

ENTRYPOINT ["python", "-m", "run_registry.migrate"]
CMD ["/data/registry/registry.db"]
