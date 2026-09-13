# verification-pipeline (D7) -- Docker Compose deployment packaging (D14).
#
# Honest note: verification-pipeline has **no standalone process
# entrypoint** in this codebase today -- `VerificationPipeline`
# (pipeline.py) is a library class, invoked in-process by whatever calls
# it (not yet wired to orchestrator's stage transitions -- CLAUDE.md's
# "Known cross-deliverable gaps"). Its README documents only "Running
# the tests", no "Run it" section.
#
# Two of its real MCP clients (`ci_client.py`, `index_client.py`) spawn
# OTHER deliverables' real server.py scripts as subprocesses of *this
# package's own interpreter*, resolved by a fixed relative path from
# this file's own location (`Path(__file__).resolve().parents[3]`,
# i.e. a sibling of services/verification-pipeline under services/) --
# see both modules' module docstrings. That means this image needs
# services/index-server's and services/mcp-stubs' real source trees
# copied in at that same relative layout (not just their dependencies)
# for layer 7 (cross-codebase completion check) and layer 1 (the ci
# stub round-trip) to actually spawn something real, exactly as they do
# on a host checkout of this monorepo.
#
# This image's default command is verification-pipeline's own real,
# documented command (`python3 -m pytest -q`, README "Running the
# tests") as a build-time self-check, then exit -- see
# docker-compose.yml's comment on this service.
#
# Build context: the repo root (see docker-compose.yml).

FROM python:3.14-slim AS runtime

WORKDIR /app/services
COPY services/index-server/ /app/services/index-server/
COPY services/mcp-stubs/ /app/services/mcp-stubs/
COPY services/verification-pipeline/ /app/services/verification-pipeline/

WORKDIR /app/services/verification-pipeline
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

CMD ["python3", "-m", "pytest", "-q"]
