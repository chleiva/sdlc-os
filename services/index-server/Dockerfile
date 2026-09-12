# index-server (D3) -- Docker Compose deployment packaging (D14).
#
# Real entrypoint, unmodified: `python src/index_server/server.py`
# (README "Run the server") -- a real MCP server, over **stdio**
# transport (the mcp SDK's default; nothing in this codebase asks for
# streamable-http/sse). That means this container is not reachable over
# the compose network the way job-dispatcher/fleet-dashboard are: MCP
# stdio servers in this codebase are meant to be spawned as a local
# subprocess by whatever process needs them (see
# orchestrator/mcp_clients.py and verification-pipeline/index_client.py,
# both of which spawn `index-server/src/index_server/server.py` as a
# child process of their own venv, not a network call). This container
# runs the real command anyway (it blocks reading stdin, so it stays
# "up" rather than exiting) for image-build completeness and manual
# inspection (`docker compose exec index-server ...`) -- see
# docker-compose.yml's comment and this repo's top-level README for the
# honest limitation.
#
# README explicitly documents python3.13 for this service (its own
# tree-sitter grammar pins are validated against that interpreter).

FROM python:3.13-slim AS runtime

WORKDIR /app/services/index-server
COPY services/index-server/ /app/services/index-server/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

CMD ["python", "src/index_server/server.py"]
