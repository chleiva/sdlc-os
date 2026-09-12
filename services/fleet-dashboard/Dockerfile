# fleet-dashboard (D8) -- Docker Compose deployment packaging (D14).
#
# Real entrypoint, unmodified: `python -m fleet_dashboard` (README
# "Running the server"), a real stdlib http.server app reading the
# shared Run Registry SQLite file via `RegistryService` -- configured
# entirely through the environment variables its own __main__.py already
# reads (RUN_REGISTRY_DB_PATH, FLEET_DASHBOARD_HOST, FLEET_DASHBOARD_PORT),
# so no D14 wrapper script is needed here (contrast job-dispatcher, which
# has no env-var configuration surface of its own).
#
# Install order replicates the README's documented three-step dance
# exactly: run-registry must be editable-installed *and stay* editable
# (its migrations directory is resolved relative to its own installed
# source location -- a plain wheel copy breaks it, "no such table: runs"
# is the documented symptom), which the final `--force-reinstall
# --no-deps` re-assertion step undoes pip's own side effect of
# reinstalling it non-editably.
#
# Build context: the repo root (see docker-compose.yml).

FROM python:3.11-slim AS runtime

WORKDIR /app/services
COPY services/run-registry/ /app/services/run-registry/
COPY services/fleet-dashboard/ /app/services/fleet-dashboard/

WORKDIR /app/services/fleet-dashboard
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e ../run-registry \
 && pip install --no-cache-dir -e ".[dev]" \
 && pip install --no-cache-dir -e ../run-registry --force-reinstall --no-deps

ENV RUN_REGISTRY_DB_PATH=/data/registry/registry.db
ENV FLEET_DASHBOARD_HOST=0.0.0.0
ENV FLEET_DASHBOARD_PORT=8808
VOLUME ["/data/registry"]

EXPOSE 8808
CMD ["python", "-m", "fleet_dashboard"]
