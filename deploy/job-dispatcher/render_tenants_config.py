#!/usr/bin/env python3
"""D14 packaging helper -- NOT part of job-dispatcher's own source tree.

job-dispatcher's real, unmodified `__main__.py` (services/job-dispatcher/
src/job_dispatcher/__main__.py) takes a `--config path/to/tenants.json`
file (see its `DispatcherConfig.from_dict`/`config/tenants.example.json`)
-- it has no environment-variable configuration surface of its own, by
design (its docstring: "a simple config file is fine ... deliberately
not a database").

Section 14.16's Docker Compose deployment mode is single-tenant by
construction (master spec: "tenant_id fixed to a single configured
value"), so this script renders exactly one tenant entry into that same
documented JSON shape from this deployment's `.env` values, so a
deployer edits `.env` (per §17.4's local-secret-handling framing), not a
committed JSON file, and never touches job-dispatcher's own source.

Run once at container start, before job-dispatcher's real entrypoint
(see docker-compose.yml's job-dispatcher command and
services/job-dispatcher/Dockerfile).
"""
from __future__ import annotations

import json
import os
import sys

REQUIRED = ["TENANT_ID", "JIRA_PROJECT_KEY", "JIRA_WEBHOOK_SECRET", "JIRA_BASE_URL"]


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print(
            f"render_tenants_config: missing required .env value(s): {', '.join(missing)} "
            "-- see .env.example at the repo root.",
            file=sys.stderr,
        )
        return 1

    auth_mode = os.environ.get("JIRA_AUTH_MODE", "oauth_bearer")
    jira: dict[str, object] = {
        "base_url": os.environ["JIRA_BASE_URL"],
        "auth_mode": auth_mode,
        "opt_in_label": os.environ.get("JIRA_OPT_IN_LABEL", "ai-factory"),
        "trigger_status": os.environ.get("JIRA_TRIGGER_STATUS", "Selected for Development"),
        "approval_status": os.environ.get("JIRA_APPROVAL_STATUS", "In Progress"),
        "change_review_status": os.environ.get("JIRA_CHANGE_REVIEW_STATUS", "In Review"),
        "size_field_mode": os.environ.get("JIRA_SIZE_FIELD_MODE", "label"),
        "epic_link_mode": os.environ.get("JIRA_EPIC_LINK_MODE", "parent"),
    }
    if auth_mode == "basic":
        jira["basic_auth_email"] = os.environ.get("JIRA_BASIC_AUTH_EMAIL", "")
        jira["basic_auth_api_token"] = os.environ.get("JIRA_BASIC_AUTH_API_TOKEN", "")
    else:
        jira["oauth_bearer_token"] = os.environ.get("JIRA_OAUTH_BEARER_TOKEN", "")

    config = {
        "tenants": [
            {
                "tenant_id": os.environ["TENANT_ID"],
                "project_key": os.environ["JIRA_PROJECT_KEY"],
                "webhook_secret": os.environ["JIRA_WEBHOOK_SECRET"],
                "jira": jira,
            }
        ]
    }

    out_path = os.environ.get("JOB_DISPATCHER_CONFIG_PATH", "/config/tenants.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"render_tenants_config: wrote {out_path} for tenant_id={os.environ['TENANT_ID']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
