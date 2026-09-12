"""Loads D1's per-tenant configuration from a single JSON file: the
Jira-project-to-tenant mapping (Sec. 4.4), each tenant's webhook HMAC
secret (Sec. 17.3), and each tenant's Jira client config (so D1 can post
the capacity-delay comment via D4's real `JiraClient`).

"A simple config file is fine" per the brief -- this is deliberately not
a database. See `config/tenants.example.json` for the shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from issue_tracker.config import TenantJiraConfig
from issue_tracker.jira_client import JiraClient

from job_dispatcher.tenant_resolution import TenantDirectory


@dataclass(frozen=True)
class TenantEntry:
    tenant_id: str
    project_key: str
    webhook_secret: bytes
    jira_config: TenantJiraConfig


@dataclass(frozen=True)
class DispatcherConfig:
    tenants: tuple[TenantEntry, ...]

    @property
    def tenant_directory(self) -> TenantDirectory:
        return TenantDirectory.from_mapping({t.project_key: t.tenant_id for t in self.tenants})

    def secret_lookup(self, tenant_id: str) -> bytes | None:
        for t in self.tenants:
            if t.tenant_id == tenant_id:
                return t.webhook_secret
        return None

    def jira_config_for_tenant(self, tenant_id: str) -> TenantJiraConfig | None:
        for t in self.tenants:
            if t.tenant_id == tenant_id:
                return t.jira_config
        return None

    def jira_client_factory(self) -> Callable[[str], JiraClient]:
        """A `tenant_id -> JiraClient` callable, one client built (and
        cached) per tenant, for `JobDispatcher(jira_client_for_tenant=...)`.
        """
        cache: dict[str, JiraClient] = {}

        def _for_tenant(tenant_id: str) -> JiraClient:
            client = cache.get(tenant_id)
            if client is None:
                jira_config = self.jira_config_for_tenant(tenant_id)
                if jira_config is None:
                    raise KeyError(f"no Jira config configured for tenant {tenant_id!r}")
                client = JiraClient(config=jira_config)
                cache[tenant_id] = client
            return client

        return _for_tenant

    @staticmethod
    def from_dict(raw: dict) -> "DispatcherConfig":
        entries = []
        for entry in raw["tenants"]:
            secret = entry["webhook_secret"]
            secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
            jira_raw = entry["jira"]
            jira_config = TenantJiraConfig(
                tenant_id=entry["tenant_id"],
                base_url=jira_raw["base_url"],
                auth_mode=jira_raw.get("auth_mode", "oauth_bearer"),
                oauth_bearer_token=jira_raw.get("oauth_bearer_token"),
                basic_auth_email=jira_raw.get("basic_auth_email"),
                basic_auth_api_token=jira_raw.get("basic_auth_api_token"),
                project_key=entry["project_key"],
                opt_in_label=jira_raw.get("opt_in_label", "ai-factory"),
                opt_in_issue_type=jira_raw.get("opt_in_issue_type"),
                trigger_status=jira_raw.get("trigger_status", "Selected for Development"),
                approval_status=jira_raw.get("approval_status", "In Progress"),
                change_review_status=jira_raw.get("change_review_status", "In Review"),
                size_field_mode=jira_raw.get("size_field_mode", "label"),
                size_custom_field_id=jira_raw.get("size_custom_field_id"),
                epic_link_mode=jira_raw.get("epic_link_mode", "parent"),
                epic_link_custom_field_id=jira_raw.get("epic_link_custom_field_id"),
                request_timeout_seconds=jira_raw.get("request_timeout_seconds", 10.0),
            )
            entries.append(
                TenantEntry(
                    tenant_id=entry["tenant_id"],
                    project_key=entry["project_key"],
                    webhook_secret=secret_bytes,
                    jira_config=jira_config,
                )
            )
        return DispatcherConfig(tenants=tuple(entries))

    @staticmethod
    def load_json(path: str) -> "DispatcherConfig":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return DispatcherConfig.from_dict(raw)
