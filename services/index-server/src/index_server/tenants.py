"""Tenant -> repository registry (spec Sec. 8.6 / 14.13): this server is
deployed per tenant and is read-only, scoped to the tenant's own
repositories only. A request whose tenant_id is unknown, or whose
tenant_id is known but not authorized for the requested `repository`, is
rejected as permission-denied -- fail-closed, never a silent default
(matching the schema's own tenant_id field description).

Config shape (config/tenants.json):
    {
      "tenant-id": {
        "repositories": {"repo-name": "relative/or/absolute/path"}
      },
      ...
    }
Paths are resolved relative to this config file's own directory when
not absolute.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "tenants.json"


class TenantRegistry:
    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or Path(
            os.environ.get("INDEX_SERVER_TENANTS_CONFIG", str(DEFAULT_CONFIG_PATH))
        )
        self._config: dict = {}
        self._base_dir = self.config_path.parent
        if self.config_path.is_file():
            self._config = json.loads(self.config_path.read_text())

    def is_known_tenant(self, tenant_id: str) -> bool:
        return tenant_id in self._config

    def repo_path(self, tenant_id: str, repository: str) -> Path | None:
        """None => tenant doesn't exist, or exists but has no such
        repository registered (both are permission-denied at the
        service layer -- fail-closed per Sec. 14.13, never revealing
        whether the repository exists for some *other* tenant)."""
        tenant_cfg = self._config.get(tenant_id)
        if not tenant_cfg:
            return None
        repos = tenant_cfg.get("repositories", {})
        raw = repos.get(repository)
        if raw is None:
            return None
        p = Path(raw)
        return p if p.is_absolute() else (self._base_dir / p).resolve()

    def repositories_for(self, tenant_id: str) -> list[str]:
        return list(self._config.get(tenant_id, {}).get("repositories", {}))
