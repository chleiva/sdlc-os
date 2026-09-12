#!/bin/sh
# D14 packaging helper -- NOT part of job-dispatcher's own source tree
# (kept under deploy/, outside services/job-dispatcher/, so that
# directory stays untouched per D14's scope). Renders config/tenants.json
# from .env values, then execs job-dispatcher's real, unmodified
# `python -m job_dispatcher` entrypoint.
#
# --capacity-provider defaults to "local": this container is the Sec.
# 14.16 Docker Compose deployment mode's job-dispatcher, which has no
# real GPU/Karpenter cluster to request capacity from (inference is
# delegated to an external API-key vendor instead, Sec. 13.8) -- so
# LocalCapacityProvider (New, Rev 9, __main__.py's --capacity-provider
# flag) is the correct default here, not "mock". Overridable via
# JOB_DISPATCHER_CAPACITY_PROVIDER for anyone who wants this same image
# in a different composition.
set -e

python /opt/d14/render_tenants_config.py

exec python -m job_dispatcher \
  --config "${JOB_DISPATCHER_CONFIG_PATH:-/config/tenants.json}" \
  --db "${RUN_REGISTRY_DB_PATH:-/data/registry/registry.db}" \
  --host "${JOB_DISPATCHER_HOST:-0.0.0.0}" \
  --port "${JOB_DISPATCHER_PORT:-8809}" \
  --capacity-provider "${JOB_DISPATCHER_CAPACITY_PROVIDER:-local}"
