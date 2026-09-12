# issue-tracker (D4) -- Docker Compose deployment packaging (D14).
#
# Honest note: issue-tracker has **no standalone process entrypoint** in
# this codebase today. `src/issue_tracker/mcp_server.py` defines a real
# `build_server(*, client_for_tenant: Callable[[str], JiraClient])` and
# an async `run_stdio(server)`, but there is no `__main__` guard and no
# CLI wiring `client_for_tenant` to a real per-tenant Jira configuration
# -- doing that here would mean inventing tenant-config-loading logic
# that doesn't exist anywhere in this package (out of scope: D14 does
# not modify or add to any service's own source, and issue-tracker's own
# README documents no such command). See SETUP.md for what a human with
# real Jira access still has to configure before any such entrypoint
# could exist.
#
# So this image's job is real but narrow: build the package and run its
# own real, documented verification command (`python -m pytest -q`,
# README "Running the tests") as a build-time self-check, then exit --
# see docker-compose.yml's comment on this service. The real consumption
# path today is direct Python import: job-dispatcher and gates both
# install this package editable and call its `JiraClient`/config/gating
# modules in-process (see their own READMEs).
#
# Build context: the repo root (see docker-compose.yml).
#
# issue-tracker's own pyproject.toml declares a real, additional
# transitive local-path dependency, "kms-boundary @ file:../kms-boundary"
# (`TenantJiraConfig.from_wrapped_oauth_token`'s Sec. 17.3 KMS-unwrap
# path) -- not mentioned in its own README's install steps, but
# required for `pip install -e '.[dev]'` to resolve at all (discovered
# by actually attempting this build -- see this deliverable's report).

FROM python:3.11-slim AS runtime

WORKDIR /app/services
COPY services/kms-boundary/ /app/services/kms-boundary/
COPY services/issue-tracker/ /app/services/issue-tracker/

WORKDIR /app/services/issue-tracker
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e '.[dev]'

CMD ["python", "-m", "pytest", "-q"]
