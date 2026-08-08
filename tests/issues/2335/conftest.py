"""Run the original #2335 verifier tests with the opt-in feature enabled."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _enable_acceptance_verification():
    with patch(
        "app.modules.workspace.autonomous.phases.acceptance_verification."
        "is_acceptance_verification_enabled",
        return_value=True,
    ):
        yield
