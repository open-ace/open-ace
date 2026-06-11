"""Conftest for Issue #811 tests.

Import tmp_db fixture from integration tests.
Note: pytest_plugins in non-top-level conftest is deprecated in newer pytest versions.
The fixture is available via the top-level conftest.py which includes pytest_plugins.
"""
