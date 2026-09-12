"""Entry point: `python -m fleet_dashboard`.

Environment variables:
  RUN_REGISTRY_DB_PATH   Path to the (shared) Registry SQLite file this
                          process's RegistryService instance points at.
                          Defaults to "run_registry.db" in the CWD, same
                          default F2's own `mcp_server.py` uses.
  FLEET_DASHBOARD_HOST    Default 127.0.0.1
  FLEET_DASHBOARD_PORT    Default 8808

See README.md for full run instructions, including demo seeding.
"""

from __future__ import annotations

import os

from run_registry import RegistryService

from fleet_dashboard import config
from fleet_dashboard.dashboard_service import DashboardService
from fleet_dashboard.http_app import serve


def main() -> None:
    db_path = os.environ.get("RUN_REGISTRY_DB_PATH", "run_registry.db")
    host = os.environ.get("FLEET_DASHBOARD_HOST", config.DEFAULT_HOST)
    port = int(os.environ.get("FLEET_DASHBOARD_PORT", config.DEFAULT_PORT))

    registry = RegistryService(db_path)
    service = DashboardService(registry)
    httpd = serve(service, host=host, port=port)
    print(f"Fleet Control Dashboard listening on http://{host}:{port}")
    print(f"Reading Run Registry via RegistryService at db_path={db_path!r}")
    print(f"Polling interval used by the client UI: {config.POLL_INTERVAL_SECONDS}s")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        registry.close()


if __name__ == "__main__":
    main()
