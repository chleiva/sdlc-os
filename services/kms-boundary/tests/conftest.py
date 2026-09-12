"""Shared moto-KMS fixtures.

Real `boto3` calls against moto's mocked KMS backend -- not a
hand-rolled fake client. `moto.mock_aws` intercepts botocore's HTTP
calls at the transport layer, so `KmsBoundary` (and everything it calls
in `boto3`/`botocore`) runs exactly the same code path it would against
real AWS KMS.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from kms_boundary.envelope import KmsBoundary
from kms_boundary.key_resolver import StaticTenantKeyResolver


@pytest.fixture
def moto_kms():
    with mock_aws():
        yield boto3.client("kms", region_name="us-east-1")


@pytest.fixture
def two_tenant_keys(moto_kms):
    """Two tenants, each with its own real (mocked) KMS key -- exactly
    the "real, distinct key_id per tenant" shape SETUP.md tells a human
    to provision for real."""
    key_a = moto_kms.create_key(Description="tenant-a's key")["KeyMetadata"]["KeyId"]
    key_b = moto_kms.create_key(Description="tenant-b's key")["KeyMetadata"]["KeyId"]
    return {"tenant-a": key_a, "tenant-b": key_b}


@pytest.fixture
def boundary(moto_kms, two_tenant_keys):
    resolver = StaticTenantKeyResolver(two_tenant_keys)
    return KmsBoundary(moto_kms, resolver)
