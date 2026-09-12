"""Cross-package reference site #4 -- a second, differently-shaped
cross-package call (module-alias import rather than a direct symbol
import), still in pkg_b, still outside pkg_a."""
import pkg_a.billing.util as billing_util


def legacy_total(items):
    return billing_util.compute_total(items)
