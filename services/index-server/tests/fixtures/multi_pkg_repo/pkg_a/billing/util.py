"""Billing utility functions -- the symbol under test for the exhaustive
cross-package find-references acceptance criterion (compute_total)."""

TAX_RATE = 0.08


def compute_total(items):
    """Sum item prices and apply the fixed tax rate."""
    subtotal = sum(item.price for item in items)
    return subtotal * (1 + TAX_RATE)


class Invoice:
    def __init__(self, items):
        self.items = items

    def total(self):
        return compute_total(self.items)
