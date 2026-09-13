# gates (D9) -- Docker Compose deployment packaging (D14).
#
# Honest note: gates has **no standalone process entrypoint** in this
# codebase today. `GatesService` (gate_service.py) is a library class
# composing autonomy/approval/escalation/batching logic, called
# in-process by whatever needs a gate decision -- not yet wired to D1/D2
# as a running service (CLAUDE.md's "Known cross-deliverable gaps"). Its
# README documents only "Setup" and "Running the tests", no "Run it".
#
# So this image's job is real but narrow: build the package (with all
# five real sibling dependencies it directly imports and drives --
# run-registry, orchestrator, issue-tracker, index-server,
# source-control, exactly as the README's install order requires) and
# run its own real, documented verification command (`python -m pytest
# tests/ -v`) as a build-time self-check, then exit -- see
# docker-compose.yml's comment on this service.
#
# Build context: the repo root (see docker-compose.yml).

FROM python:3.14-slim AS runtime

# kms-boundary is a real, additional transitive dependency: issue-tracker's
# own pyproject.toml declares "kms-boundary @ file:../kms-boundary" (not
# mentioned in any README's install steps -- discovered by actually
# attempting the job-dispatcher build, see this deliverable's report).
WORKDIR /app/services
COPY services/kms-boundary/ /app/services/kms-boundary/
COPY services/run-registry/ /app/services/run-registry/
COPY services/mcp-stubs/ /app/services/mcp-stubs/
COPY services/orchestrator/ /app/services/orchestrator/
COPY services/issue-tracker/ /app/services/issue-tracker/
COPY services/index-server/ /app/services/index-server/
COPY services/source-control/ /app/services/source-control/
COPY services/gates/ /app/services/gates/

WORKDIR /app/services/gates
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e '.[dev]' \
      -e ../run-registry \
      -e ../orchestrator \
      -e ../issue-tracker \
      -e ../index-server \
      -e ../source-control

CMD ["python", "-m", "pytest", "tests/", "-v"]
