"""Deliberately contains a real ruff violation (F401: unused import) so
Layer 3's static-analysis test proves a real lint violation is caught by
a real linter, not asserted against canned data."""
import os  # noqa placeholder removed on purpose -- this import is unused


def add(a: int, b: int) -> int:
    return a + b
