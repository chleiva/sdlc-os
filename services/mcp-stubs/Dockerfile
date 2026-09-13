# mcp-stubs (F3) -- Docker Compose deployment packaging (D14).
#
# F3 ships four real MCP-shaped stub servers, all stdio transport, none
# with a network entrypoint (README "Running a stub server standalone":
# `python3 <name>/server.py`, stdio only). Three of the four --index,
# issue-tracker, source-control-- are already superseded by real
# implementations elsewhere in this repo (services/index-server (D3),
# services/issue-tracker (D4), services/source-control (D5)); only `ci`
# has no real deliverable behind it anywhere in this repo (D15/CI
# integration is out of scope for every wave so far), so `ci/server.py`
# is this image's default command. The other three stub servers are
# still built into this image and can be run manually for reference/
# testing (`docker compose run mcp-stubs python3 index/server.py`, etc.)
# but are not started by default.
#
# Like every MCP stdio server in this codebase, this is not reachable
# over the compose network -- see docker-compose.yml's comment and this
# repo's top-level README for the honest limitation. This package has no
# pyproject.toml `[project]` table (only `[tool.pytest.ini_options]`),
# so it is not installed as a package either, matching its own README's
# `pip install -r requirements.txt` (no `-e .`) + run-scripts-by-path
# usage exactly.
#
# Build context: the repo root (see docker-compose.yml).

FROM python:3.14-slim AS runtime

WORKDIR /app/services/mcp-stubs
COPY services/mcp-stubs/ /app/services/mcp-stubs/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

CMD ["python3", "ci/server.py"]
