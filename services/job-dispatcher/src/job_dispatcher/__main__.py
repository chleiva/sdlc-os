"""`python -m job_dispatcher` -- run the real webhook receiver.

Reads tenant configuration (Sec. 4.4 project->tenant mapping, Sec. 17.3
per-tenant webhook secrets, and each tenant's Jira client config) from a
JSON file (default: config/tenants.json next to this repo, override
with --config), and the Run Registry's SQLite path (--db).

`CapacityProvider` here is `MockCapacityProvider` (see capacity.py's
docstring) -- this environment has no real Karpenter/Kubernetes cluster
to request capacity from. D6 plugs in the real implementation; nothing
else in this module needs to change to swap it in (only this wiring
point does).
"""

from __future__ import annotations

import argparse
import sys

from run_registry import RegistryService

from job_dispatcher.capacity import MockCapacityProvider
from job_dispatcher.config import DispatcherConfig
from job_dispatcher.dispatcher import JobDispatcher
from job_dispatcher.http_app import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_dispatcher")
    parser.add_argument("--config", default="config/tenants.json", help="path to tenant config JSON")
    parser.add_argument("--db", default="job_dispatcher_registry.db", help="Run Registry SQLite path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8809)
    args = parser.parse_args(argv)

    config = DispatcherConfig.load_json(args.config)
    registry = RegistryService(args.db)
    dispatcher = JobDispatcher(
        secret_lookup=config.secret_lookup,
        tenant_directory=config.tenant_directory,
        registry=registry,
        capacity_provider=MockCapacityProvider(),
        jira_client_for_tenant=config.jira_client_factory(),
    )

    httpd = serve(dispatcher, host=args.host, port=args.port)
    print(f"job-dispatcher listening on http://{args.host}:{args.port} (POST /webhook, GET /healthz)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        registry.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
