from __future__ import annotations

import pytest

from gates.identity import AuthenticatedIdentity, MockIdentityResolver, UnknownIdentityError


def test_mock_resolver_resolves_a_known_credential():
    alice = AuthenticatedIdentity(subject_id="user-alice", display_name="Alice", handles=frozenset({"@alice"}))
    resolver = MockIdentityResolver.from_identities(alice)
    assert resolver.resolve("user-alice") == alice


def test_mock_resolver_raises_for_unknown_credential():
    resolver = MockIdentityResolver({})
    with pytest.raises(UnknownIdentityError):
        resolver.resolve("nobody")
