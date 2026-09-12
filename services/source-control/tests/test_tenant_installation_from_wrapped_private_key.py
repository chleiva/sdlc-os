"""`TenantInstallation.from_wrapped_private_key` -- the new, additive
alternate constructor that consumes `kms_boundary` (a real
editable-installed local dependency, same pattern as
`job-dispatcher @ file:../run-registry`) instead of only an
already-unwrapped plaintext PEM.

The existing plaintext-PEM constructor (`TenantInstallation(...,
private_key_pem=...)`) is untouched -- every other test file in this
suite still uses it exactly as before; this file only adds coverage
for the new path.
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from kms_boundary import CrossTenantDecryptionError, KmsBoundary, StaticTenantKeyResolver
from source_control.service import TenantInstallation

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


def test_from_wrapped_private_key_round_trips_the_real_pem(kms_boundary_with_two_tenants, rsa_keypair, tmp_path):
    _, _, private_pem = rsa_keypair
    wrapped = kms_boundary_with_two_tenants.encrypt(TENANT, private_pem)

    installation = TenantInstallation.from_wrapped_private_key(
        tenant_id=TENANT,
        installation_id="inst-1",
        app_id="918273",
        app_slug="sdlc-auto",
        wrapped_private_key=wrapped,
        kms_boundary=kms_boundary_with_two_tenants,
        allowed_repositories=frozenset({"acme/app"}),
        mirror_root=Path(tmp_path) / "mirrors",
    )

    assert installation.private_key_pem == private_pem
    assert installation.tenant_id == TENANT


def test_from_wrapped_private_key_refuses_another_tenants_wrapped_secret(
    kms_boundary_with_two_tenants, rsa_keypair, tmp_path
):
    """The exact cross-tenant boundary this whole package exists for:
    tenant-acme's wrapped GitHub App private key must not be unwrappable
    under tenant-globex's KMS key context."""
    _, _, private_pem = rsa_keypair
    wrapped_for_acme = kms_boundary_with_two_tenants.encrypt(TENANT, private_pem)

    with pytest.raises(CrossTenantDecryptionError):
        TenantInstallation.from_wrapped_private_key(
            tenant_id=OTHER_TENANT,
            installation_id="inst-2",
            app_id="918273",
            app_slug="sdlc-auto",
            wrapped_private_key=wrapped_for_acme,
            kms_boundary=kms_boundary_with_two_tenants,
            allowed_repositories=frozenset({"globex/app"}),
            mirror_root=Path(tmp_path) / "mirrors",
        )


def test_plaintext_constructor_path_still_works_unchanged(rsa_keypair, tmp_path):
    """The pre-existing constructor path needs no KMS context at all --
    confirms this addition didn't make it a breaking change."""
    _, _, private_pem = rsa_keypair
    installation = TenantInstallation(
        tenant_id=TENANT,
        installation_id="inst-1",
        app_id="918273",
        app_slug="sdlc-auto",
        private_key_pem=private_pem,
        allowed_repositories=frozenset({"acme/app"}),
        mirror_root=Path(tmp_path) / "mirrors",
    )
    assert installation.private_key_pem == private_pem
