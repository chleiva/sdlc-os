"""`TenantJiraConfig.from_wrapped_oauth_token` -- the new, additive
alternate constructor that consumes `kms_boundary` (a real
editable-installed local dependency) instead of only an
already-plaintext OAuth bearer token.

The existing plaintext-token constructor (`TenantJiraConfig(...,
oauth_bearer_token=...)`) is untouched -- every other test file in this
suite still uses it exactly as before; this file only adds coverage
for the new path.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from issue_tracker.config import TenantJiraConfig
from kms_boundary import CrossTenantDecryptionError, KmsBoundary, StaticTenantKeyResolver

TENANT = "tenant-acme"
OTHER_TENANT = "tenant-globex"


@pytest.fixture
def kms_boundary_with_two_tenants():
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-1")
        key_a = client.create_key(Description=TENANT)["KeyMetadata"]["KeyId"]
        key_b = client.create_key(Description=OTHER_TENANT)["KeyMetadata"]["KeyId"]
        resolver = StaticTenantKeyResolver({TENANT: key_a, OTHER_TENANT: key_b})
        yield KmsBoundary(client, resolver)


def test_from_wrapped_oauth_token_round_trips_the_real_token(kms_boundary_with_two_tenants):
    token = "real.oauth.bearer.token.value"
    wrapped = kms_boundary_with_two_tenants.encrypt(TENANT, token.encode("utf-8"))

    config = TenantJiraConfig.from_wrapped_oauth_token(
        wrapped_oauth_token=wrapped,
        kms_boundary=kms_boundary_with_two_tenants,
        tenant_id=TENANT,
        base_url="https://api.atlassian.com/ex/jira/some-cloud-id",
    )

    assert config.oauth_bearer_token == token
    assert config.auth_mode == "oauth_bearer"
    assert config.tenant_id == TENANT


def test_from_wrapped_oauth_token_refuses_another_tenants_wrapped_secret(kms_boundary_with_two_tenants):
    """The exact cross-tenant boundary this whole package exists for:
    tenant-acme's wrapped OAuth token must not be unwrappable under
    tenant-globex's KMS key context."""
    wrapped_for_acme = kms_boundary_with_two_tenants.encrypt(TENANT, b"acme-secret-token")

    with pytest.raises(CrossTenantDecryptionError):
        TenantJiraConfig.from_wrapped_oauth_token(
            wrapped_oauth_token=wrapped_for_acme,
            kms_boundary=kms_boundary_with_two_tenants,
            tenant_id=OTHER_TENANT,
            base_url="https://api.atlassian.com/ex/jira/some-cloud-id",
        )


def test_plaintext_constructor_path_still_works_unchanged():
    """The pre-existing constructor path needs no KMS context at all --
    confirms this addition didn't make it a breaking change."""
    config = TenantJiraConfig(
        tenant_id=TENANT,
        base_url="https://api.atlassian.com/ex/jira/some-cloud-id",
        oauth_bearer_token="a-plain-token",
    )
    assert config.oauth_bearer_token == "a-plain-token"


def test_from_wrapped_oauth_token_requires_tenant_id(kms_boundary_with_two_tenants):
    wrapped = kms_boundary_with_two_tenants.encrypt(TENANT, b"whatever")
    with pytest.raises(ValueError):
        TenantJiraConfig.from_wrapped_oauth_token(
            wrapped_oauth_token=wrapped,
            kms_boundary=kms_boundary_with_two_tenants,
            base_url="https://api.atlassian.com/ex/jira/some-cloud-id",
        )
