"""A test-file caller of compute_total (reference site #2 -- exercises
include_test_files toggling)."""
from pkg_a.billing.util import compute_total


class _Item:
    def __init__(self, price):
        self.price = price


def test_compute_total_applies_tax():
    result = compute_total([_Item(100)])
    assert result == 108.0
