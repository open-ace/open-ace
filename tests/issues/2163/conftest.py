"""
Test configuration and fixtures for Issue #2163 tests.
"""

import pytest


@pytest.fixture
def mock_db():
    """Mock database fixture."""
    from unittest.mock import Mock
    return Mock()


@pytest.fixture
def mock_app():
    """Mock Flask app fixture."""
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app