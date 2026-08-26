"""LLM proxy returns a retryable 5xx for transient DNS-resolution failures
instead of a permanent 403, so autonomous workflows retry a brief DNS blip
rather than hard-failing. Issue #3116.
"""

from unittest.mock import patch

import pytest
from flask import Flask

from app.modules.workspace.llm_proxy_handler import _determine_target_url
from app.utils.llm_proxy_url_validator import LlmProxyValidationResult

pytestmark = [pytest.mark.issue(3116), pytest.mark.regression]

_APP = Flask(__name__)


def _call_determine(validation_result):
    with patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        return_value=validation_result,
    ):
        with _APP.test_request_context("/api/remote/llm-proxy/v1/messages"):
            return _determine_target_url(
                provider="anthropic",
                base_url="https://coding.example.com",
                path="v1/messages",
                tenant_id=1,
            )


def test_transient_validation_failure_returns_503():
    out = _call_determine(
        LlmProxyValidationResult(False, "Host could not be resolved: timeout", transient=True)
    )
    assert isinstance(out, tuple)
    _response, status = out
    assert status == 503


def test_policy_violation_still_returns_403():
    out = _call_determine(
        LlmProxyValidationResult(False, "Blocked non-public address: 10.1.2.3", transient=False)
    )
    assert isinstance(out, tuple)
    _response, status = out
    assert status == 403


def test_transient_503_message_matches_orchestrator_retry_classifier():
    """Linchpin: the 503 body the handler emits must be recognized as transient
    by the orchestrator's retry regex, otherwise the workflow won't retry."""
    from app.modules.workspace.autonomous.orchestrator import _TRANSIENT_API_ERROR_RE

    out = _call_determine(
        LlmProxyValidationResult(False, "Host could not be resolved: timeout", transient=True)
    )
    response, status = out
    assert status == 503
    message = response.get_json()["error"]["message"]
    assert _TRANSIENT_API_ERROR_RE.search(
        message
    ), f"503 message not recognized as transient by orchestrator: {message!r}"
