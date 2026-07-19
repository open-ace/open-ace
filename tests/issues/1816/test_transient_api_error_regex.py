"""Tests for _TRANSIENT_API_ERROR_RE false-positive fix (issue #1816).

The regex used to detect transient API errors (429 rate limit, 5xx) in agent
output had a bare ``429`` alternative that matched any occurrence of the number
"429" — including HTTP status codes in legitimate plan/review text discussing
rate-limiting or error handling. This caused the orchestrator to retry a
perfectly good agent response as if it were an API error, wasting time and
eventually timing out (#1816 regression).

The fix requires contextual clues (``status``, ``error``, ``too many requests``)
around a bare ``429``, while still catching real API error messages.
"""

from app.modules.workspace.autonomous.orchestrator import _TRANSIENT_API_ERROR_RE


class TestTransientApiErrorRegex:
    """The regex must not match HTTP status codes in legitimate agent output."""

    # ── Should NOT match (plan/review text with HTTP status codes) ──

    def test_plan_with_429_in_table(self):
        """The exact #1816 case: plan text has '429' in a design table."""
        text = "| T1 | 窗口内连续失败 5 次 | 5 次错误密码 | 第 5 次 429 | 触发锁定 |"
        assert not _TRANSIENT_API_ERROR_RE.search(
            text
        ), "HTTP 429 in a plan design table must not be treated as an API error"

    def test_plan_with_return_429(self):
        text = "锁定时直接返回 429 错误"
        assert not _TRANSIENT_API_ERROR_RE.search(text)

    def test_plan_with_http_500(self):
        text = "数据库操作失败 | 500 + 错误消息"
        assert not _TRANSIENT_API_ERROR_RE.search(text)

    def test_code_with_429_constant(self):
        text = "if status == 429: retry()"
        assert not _TRANSIENT_API_ERROR_RE.search(text)

    def test_bare_number_429(self):
        """A bare '429' with no API error context must not match."""
        assert not _TRANSIENT_API_ERROR_RE.search("429")
        assert not _TRANSIENT_API_ERROR_RE.search("error 429 is returned")
        assert not _TRANSIENT_API_ERROR_RE.search("returns 429 to client")

    # ── Should match (real API error messages) ──

    def test_api_error_429(self):
        assert _TRANSIENT_API_ERROR_RE.search("API Error: 429")

    def test_api_error_503(self):
        assert _TRANSIENT_API_ERROR_RE.search("api error: 503")

    def test_status_code_429(self):
        assert _TRANSIENT_API_ERROR_RE.search("status code: 429")
        assert _TRANSIENT_API_ERROR_RE.search("status: 429")

    def test_error_code_429(self):
        assert _TRANSIENT_API_ERROR_RE.search("error code: 429")
        assert _TRANSIENT_API_ERROR_RE.search("error: 429")

    def test_429_too_many_requests(self):
        assert _TRANSIENT_API_ERROR_RE.search("429 Too Many Requests")

    def test_quota_exceeded(self):
        assert _TRANSIENT_API_ERROR_RE.search("quota exceeded")

    def test_rate_limited(self):
        assert _TRANSIENT_API_ERROR_RE.search("rate limited")

    def test_rate_limit_exceeded(self):
        assert _TRANSIENT_API_ERROR_RE.search("rate limit exceeded")

    def test_overloaded(self):
        assert _TRANSIENT_API_ERROR_RE.search("The service may be temporarily overloaded")

    def test_bad_gateway(self):
        assert _TRANSIENT_API_ERROR_RE.search("502 Bad Gateway")

    def test_service_unavailable(self):
        assert _TRANSIENT_API_ERROR_RE.search("503 Service Unavailable")
