"""Regression tests for recoverable autonomous pause reasons."""

import pytest

from app.routes.autonomous import _is_recoverable_system_pause_reason


@pytest.mark.parametrize(
    "pause_error",
    [
        "Upstream provider quota exhausted: restore allocation",
        "Merge blocked by repository policy: PR #1989 requires approval",
    ],
)
def test_recoverable_system_pause_reason_is_cleared_on_resume(pause_error):
    assert _is_recoverable_system_pause_reason(pause_error)


@pytest.mark.parametrize(
    "pause_error",
    [
        "",
        "Operator paused to inspect an external deployment",
        "Transient network error (retry 1/3)",
    ],
)
def test_manual_or_unrelated_diagnostic_is_preserved_on_resume(pause_error):
    assert not _is_recoverable_system_pause_reason(pause_error)
