# job-dispatcher (D1) -- Docker Compose deployment packaging (D14).
#
# Real entrypoint, unmodified: `python -m job_dispatcher --config ... \
# --db ... --port 8809` (services/job-dispatcher/README.md "Run it"),
# fronting the real POST /webhook + GET /healthz HTTP server
# (src/job_dispatcher/http_app.py). Install order below replicates the
# README's documented steps exactly, including the `--force-reinstall
# --no-deps` re-assertion of the two editable sibling installs (pip's
# own dependency resolution during the final `pip install -e ".[dev]"`
# step silently reinstalls them non-editably otherwise -- see README).
#
# Build context: the repo root (see docker-compose.yml) -- this
# Dockerfile needs services/run-registry and services/issue-tracker as
# real, editable-installed sibling dependencies, exactly as the README
# documents for a host checkout.

FROM python:3.14-slim AS runtime

WORKDIR /app/services

# Sibling dependencies first (order matches README). kms-boundary is a
# real, additional transitive dependency discovered while building this
# image: issue-tracker's own pyproject.toml declares
# "kms-boundary @ file:../kms-boundary" (its TenantJiraConfig.
# from_wrapped_oauth_token's real Sec. 17.3 KMS-unwrap path) -- not
# mentioned in either issue-tracker's or job-dispatcher's own README,
# but required for `pip install -e ../issue-tracker` to resolve at all.
COPY services/kms-boundary/ /app/services/kms-boundary/
COPY services/run-registry/ /app/services/run-registry/
COPY services/issue-tracker/ /app/services/issue-tracker/
COPY services/job-dispatcher/ /app/services/job-dispatcher/

WORKDIR /app/services/job-dispatcher
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e ../run-registry \
 && pip install --no-cache-dir -e ../issue-tracker \
 && pip install --no-cache-dir -e ".[dev]" \
 && pip install --no-cache-dir -e ../run-registry --force-reinstall --no-deps \
 && pip install --no-cache-dir -e ../issue-tracker --force-reinstall --no-deps

# --- D14 packaging-only helper (see deploy/job-dispatcher/) ---
# NOT part of job-dispatcher's own source: renders config/tenants.json
# from this deployment's .env at container start (job-dispatcher's own
# __main__.py takes a config *file*, not environment variables --
# see deploy/job-dispatcher/render_tenants_config.py's docstring), then
# execs the real, unmodified `python -m job_dispatcher` entrypoint.
COPY deploy/job-dispatcher/render_tenants_config.py /opt/d14/render_tenants_config.py
COPY deploy/job-dispatcher/entrypoint.sh /opt/d14/entrypoint.sh
RUN chmod +x /opt/d14/entrypoint.sh

ENV RUN_REGISTRY_DB_PATH=/data/registry/registry.db
ENV JOB_DISPATCHER_CONFIG_PATH=/config/tenants.json
VOLUME ["/data/registry", "/config"]

EXPOSE 8809
ENTRYPOINT ["/opt/d14/entrypoint.sh"]
