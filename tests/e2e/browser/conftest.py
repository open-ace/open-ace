"""Metadata shared by the migrated browser regression suite."""

import pytest


def pytest_collection_modifyitems(items):
    """Preserve regression provenance after removing the purpose-based directory."""
    for item in items:
        item.add_marker(pytest.mark.regression)
