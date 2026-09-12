"""Cross-package reference site #3 -- pkg_b (reporting), OUTSIDE
pkg_a (billing) where compute_total is actually defined. This is the
reference the exhaustive find-references acceptance criterion depends
on surfacing: a rename of compute_total that only looked inside pkg_a
would miss this call entirely.
"""
from pkg_a.billing.util import compute_total


def monthly_report(item_batches):
    return [compute_total(batch) for batch in item_batches]
