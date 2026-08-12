"""Autonomous agent requests are exempt from the user-content filter.

Autonomous prompts are system-generated and inherently carry commit SHAs,
dates, and code that trip the governance content_filter's PII detectors
(15-digit SHAs → credit cards → critical → block), which 403-blocked glm-5
autonomous workflows (#2499). The autonomous proxy token carries
``session_type="agent"`` (agent_runner.py), so the handler gates the content
filter on that claim.
"""

import pytest

from app.modules.workspace.llm_proxy_handler import _is_autonomous_request


@pytest.mark.regression
def test_agent_session_type_is_autonomous():
    assert _is_autonomous_request({"session_type": "agent"}) is True


@pytest.mark.regression
def test_terminal_session_type_is_not_autonomous():
    assert _is_autonomous_request({"session_type": "terminal"}) is False


@pytest.mark.regression
def test_missing_session_type_is_not_autonomous():
    assert _is_autonomous_request({}) is False
    assert _is_autonomous_request(None) is False
