# source-control (D5) -- Docker Compose deployment packaging (D14).
#
# Honest note: source-control's own `src/source_control/mcp_server.py`
# has a real `main()`/`if __name__ == "__main__"` guard -- but by
# design, `main()` immediately `raise SystemExit(...)` with a message
# explaining that a real deployment must construct an
# `InstallationRegistry` from its own per-tenant KMS-backed secret store
# (Section 17.3) at process start, which does not exist in code anywhere
# in this repo (CLAUDE.md's "Known cross-deliverable gaps": "No
# KMS/secret-decryption boundary exists in code anywhere"). Running that
# real, unmodified entrypoint is therefore genuinely a container that
# starts and exits immediately with that exact message today -- not a
# placeholder, the real documented behavior.
#
# Rather than have this container flap in a restart loop against a
# command that is designed to fail until D10's KMS boundary exists, this
# image's default command is source-control's other real, documented
# command: its own test suite (`python -m pytest -v`, README "Install &
# run tests"), as a build-time self-check. `python -m source_control.mcp_server`
# (the real, exit-immediately-by-design entrypoint above) is left
# available for manual inspection -- see docker-compose.yml's comment.
#
# Build context: the repo root (see docker-compose.yml).

FROM python:3.11-slim AS runtime

WORKDIR /app/services/source-control
COPY services/source-control/ /app/services/source-control/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e ".[dev]"

CMD ["python", "-m", "pytest", "-v"]
