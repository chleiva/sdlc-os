"""Viewer identity -> authorized tenant_id(s).

Master spec Sec 16.5 (New, Rev 8): "a viewer sees only the tenants their
identity is authorized for, enforced by the Registry Service ... not by
a client-side filter the dashboard could be tricked into skipping. A
cross-tenant 'fleet-of-fleets' view is available only to an operator
role explicitly granted visibility across tenants ... not a default."

Real identity resolution (Sec 16.5's OIDC mention, Sec 17.1's audited
grants) is out of scope for this deliverable -- D8 only consumes F2's
Registry Service, it does not build an identity provider. This module is
a deliberately small, clearly-labeled STAND-IN for that: a fixed token
-> {tenant_ids, role} map, so the rest of the dashboard (and its tests)
can exercise the real requirement -- "the SERVER decides which
tenant_id(s) a request is allowed to query, never the client" -- without
this deliverable also having to build OIDC. Swapping this module out for
a real identity provider is the integration point a human should wire up
before production use; nothing downstream of `authorized_tenant_ids()`
needs to change to do that.

The one rule every HTTP handler must follow: a tenant_id named in the
querystring is never trusted on its own. It is only ever used after
being intersected with `authorized_tenant_ids(token)` -- see
`http_app.py::_resolve_tenant_scope`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    token: str
    tenant_ids: frozenset[str]
    role: str  # "viewer" | "operator"

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"


# Demo/test identity map. In-memory, fixed at import time -- not read
# from, or written to, anywhere else. Replace with a real identity
# provider lookup for production use (see module docstring).
_IDENTITIES: dict[str, Identity] = {
    "demo-viewer-a": Identity(
        token="demo-viewer-a", tenant_ids=frozenset({"tenant-a"}), role="viewer"
    ),
    "demo-viewer-b": Identity(
        token="demo-viewer-b", tenant_ids=frozenset({"tenant-b"}), role="viewer"
    ),
    "demo-operator": Identity(
        token="demo-operator",
        tenant_ids=frozenset({"tenant-a", "tenant-b"}),
        role="operator",
    ),
}


class UnknownIdentityError(Exception):
    pass


def resolve_identity(token: str | None) -> Identity:
    if not token or token not in _IDENTITIES:
        raise UnknownIdentityError("no such viewer identity (missing/invalid token)")
    return _IDENTITIES[token]


def authorized_tenant_ids(token: str | None) -> frozenset[str]:
    """The tenant_id(s) this token is authorized to view.

    Fails closed: an unknown or missing token is authorized for nothing,
    never "everything" and never an error that would leak which tenants
    exist.
    """
    try:
        return resolve_identity(token).tenant_ids
    except UnknownIdentityError:
        return frozenset()


def register_identity_for_tests(identity: Identity) -> None:
    """Test-only hook so `tests/` can mint fresh per-test tenant_ids
    (the fixtures use randomized tenant_ids to avoid cross-test
    collisions) without editing this module's fixed demo map.
    """
    _IDENTITIES[identity.token] = identity
