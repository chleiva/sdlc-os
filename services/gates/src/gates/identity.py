"""Human identity resolution (master spec Sec. 17.3): gate approval
authenticates via OIDC against the organization's existing identity
provider. There is no live OIDC IdP available in this environment, so
this module defines the real integration seam -- `IdentityResolver` --
plus a deterministic `MockIdentityResolver` that backs every test in
this deliverable, exactly the same "real orchestration logic, mocked
external boundary" discipline every other deliverable in this repo uses
(D4's Jira mock, D5's GitHub mock, D6's scripted model backend).

A real deployment plugs in an `IdentityResolver` that validates an OIDC
ID token/access token against the org's IdP (issuer, audience,
signature, expiry) and maps its claims onto `AuthenticatedIdentity` --
nothing in the rest of this package needs to change to support that;
every gate-clearance function in this package takes an
`AuthenticatedIdentity`, never a raw token or bearer string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """One authenticated human, as resolved from their credential at
    the moment they acted on a gate. `subject_id` is the stable
    identifier (OIDC `sub` claim, in a real deployment) two identities
    are compared by -- never display_name/email, which can collide or
    change.

    `handles` carries whatever repository-ownership handles (e.g.
    GitHub-style `@jane-doe`, or a team handle `@billing-team`) this
    identity is known to satisfy, so a CODEOWNERS check (codeowners.py)
    can be evaluated against a real authenticated actor rather than a
    free-typed string.
    """

    subject_id: str
    display_name: str
    email: str | None = None
    handles: frozenset[str] = field(default_factory=frozenset)


class UnknownIdentityError(Exception):
    """Raised when a credential does not resolve to any known identity."""


class IdentityResolver(Protocol):
    """The real integration seam for a live OIDC identity provider.
    `resolve` takes whatever bearer credential the caller presented
    (an OIDC access/ID token in a real deployment) and returns the
    authenticated identity it names, or raises `UnknownIdentityError`.
    """

    def resolve(self, credential: str) -> AuthenticatedIdentity: ...


class MockIdentityResolver:
    """Deterministic stand-in for a real OIDC provider, used by every
    test in this deliverable. `credential` here is just an opaque
    lookup key into a fixed directory the test supplies -- it proves
    nothing about a real token's signature, which is exactly the
    external boundary a live OIDC IdP would need to fill in later (see
    module docstring).
    """

    def __init__(self, directory: dict[str, AuthenticatedIdentity]):
        self._directory = dict(directory)

    def resolve(self, credential: str) -> AuthenticatedIdentity:
        identity = self._directory.get(credential)
        if identity is None:
            raise UnknownIdentityError(f"no identity registered for credential {credential!r}")
        return identity

    @classmethod
    def from_identities(cls, *identities: AuthenticatedIdentity) -> "MockIdentityResolver":
        """Convenience constructor: each identity is reachable via its
        own `subject_id` as the credential, so tests can write
        `resolver.resolve('user-alice')` directly."""
        return cls({identity.subject_id: identity for identity in identities})
