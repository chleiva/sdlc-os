"""A same-package caller of compute_total (reference site #1 -- inside
the "obviously affected" package)."""
from pkg_a.billing.util import compute_total


def render_receipt(items):
    total = compute_total(items)
    return f"Total due: {total:.2f}"
