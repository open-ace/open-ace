"""Tests for issue #2884: Feishu page_size must not exceed 50.

The Feishu Contact v3 API rejects page_size > 50 for the department-children
and find-by-department-users endpoints. This test module verifies:

1. Both pagination call sites use page_size <= 50 (FEISHU_DIRECTORY_PAGE_SIZE).
2. Multi-page pagination via has_more / page_token works correctly.
3. HTTP 4xx responses carrying a Feishu JSON error body are surfaced as
   FeishuApiError with the platform error code and description.
4. Non-JSON 4xx responses still raise the generic HTTPError.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.services.feishu_org_sync import (
    FEISHU_DIRECTORY_PAGE_SIZE,
    FeishuApiError,
    FeishuOrgSyncService,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal requests.Response stand-in for mocking HTTP calls."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("No JSON")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


def _make_service():
    """Build a FeishuOrgSyncService with a mock HTTP session."""
    http = MagicMock()
    service = FeishuOrgSyncService(http_session=http)
    service._active_app_id = "test-app"
    service._active_app_secret = "test-secret"
    return service, http


# ---------------------------------------------------------------------------
# Tests: page_size constant
# ---------------------------------------------------------------------------


def test_directory_page_size_is_50():
    """FEISHU_DIRECTORY_PAGE_SIZE must be <= 50 per Feishu Contact v3 limits."""
    assert FEISHU_DIRECTORY_PAGE_SIZE == 50
    assert FEISHU_DIRECTORY_PAGE_SIZE <= 50


# ---------------------------------------------------------------------------
# Tests: _fetch_child_departments uses page_size <= 50
# ---------------------------------------------------------------------------


def test_fetch_child_departments_page_size():
    """The department-children request must not exceed page_size=50."""
    service, http = _make_service()
    http.request.return_value = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [],
                "has_more": False,
            },
        },
    )

    service._fetch_child_departments("test-token", "0")

    call_args = http.request.call_args
    params = call_args.kwargs.get("params") or call_args[1].get("params")
    assert params["page_size"] == FEISHU_DIRECTORY_PAGE_SIZE
    assert params["page_size"] <= 50


def test_fetch_child_departments_paginates_across_pages():
    """When has_more is True, page_token must advance until has_more is False."""
    service, http = _make_service()

    page1 = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [
                    {
                        "open_department_id": "dep-1",
                        "name": "Dept 1",
                        "parent_department_ids": ["0"],
                    }
                ],
                "has_more": True,
                "page_token": "token-page-2",
            },
        },
    )
    page2 = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [
                    {
                        "open_department_id": "dep-2",
                        "name": "Dept 2",
                        "parent_department_ids": ["0"],
                    }
                ],
                "has_more": False,
            },
        },
    )
    http.request.side_effect = [page1, page2]

    departments = service._fetch_child_departments("test-token", "0")

    assert len(departments) == 2
    assert departments[0].department_id == "dep-1"
    assert departments[1].department_id == "dep-2"
    assert http.request.call_count == 2

    # Verify second call used the page_token from the first response
    second_call_params = http.request.call_args_list[1].kwargs.get(
        "params"
    ) or http.request.call_args_list[1][1].get("params")
    assert second_call_params.get("page_token") == "token-page-2"


# ---------------------------------------------------------------------------
# Tests: _fetch_department_users uses page_size <= 50
# ---------------------------------------------------------------------------


def test_fetch_department_users_page_size():
    """The find-by-department users request must not exceed page_size=50."""
    service, http = _make_service()
    http.request.return_value = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [],
                "has_more": False,
            },
        },
    )

    service._fetch_department_users("test-token", "dep-1")

    call_args = http.request.call_args
    params = call_args.kwargs.get("params") or call_args[1].get("params")
    assert params["page_size"] == FEISHU_DIRECTORY_PAGE_SIZE
    assert params["page_size"] <= 50


def test_fetch_department_users_paginates_across_pages():
    """Multi-page user fetching must advance page_token correctly."""
    service, http = _make_service()

    page1 = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [
                    {
                        "open_id": "ou_alice",
                        "name": "Alice",
                        "department_ids": ["dep-1"],
                        "status": {},
                    }
                ],
                "has_more": True,
                "page_token": "users-token-2",
            },
        },
    )
    page2 = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [
                    {
                        "open_id": "ou_bob",
                        "name": "Bob",
                        "department_ids": ["dep-1"],
                        "status": {},
                    }
                ],
                "has_more": False,
            },
        },
    )
    http.request.side_effect = [page1, page2]

    users = service._fetch_department_users("test-token", "dep-1")

    assert len(users) == 2
    assert users[0].open_id == "ou_alice"
    assert users[1].open_id == "ou_bob"
    assert http.request.call_count == 2

    second_call_params = http.request.call_args_list[1].kwargs.get(
        "params"
    ) or http.request.call_args_list[1][1].get("params")
    assert second_call_params.get("page_token") == "users-token-2"


# ---------------------------------------------------------------------------
# Tests: _request_json_once error handling for HTTP 4xx
# ---------------------------------------------------------------------------


def test_request_json_parses_feishu_error_on_4xx():
    """HTTP 4xx with a Feishu JSON error body should raise FeishuApiError."""
    service, http = _make_service()

    feishu_error_body = {
        "code": 99992402,
        "msg": "field validation failed",
        "details": [
            {
                "field": "page_size",
                "value": "100",
                "description": "the max value is 50",
            }
        ],
    }
    http.request.return_value = _FakeResponse(400, json_data=feishu_error_body)

    with pytest.raises(FeishuApiError) as exc_info:
        service._request_json_once(
            method="GET",
            url="https://open.feishu.cn/open-apis/contact/v3/departments/0/children",
            token="test-token",
            params={"page_size": 50},
            json_payload=None,
            retried=False,
        )

    err = exc_info.value
    assert err.code == 99992402
    # The error message should include the field description
    assert "the max value is 50" in err.msg
    assert "field validation failed" in err.msg


def test_request_json_falls_back_to_http_error_for_non_json_4xx():
    """HTTP 4xx without a Feishu JSON body should still raise HTTPError."""
    service, http = _make_service()

    fake_resp = _FakeResponse(502, text="Bad Gateway")
    # Simulate a response where json() fails (non-JSON body)
    fake_resp.json = MagicMock(side_effect=ValueError("No JSON"))
    http.request.return_value = fake_resp

    with pytest.raises(requests.HTTPError):
        service._request_json_once(
            method="GET",
            url="https://open.feishu.cn/open-apis/contact/v3/departments/0/children",
            token="test-token",
            params={"page_size": 50},
            json_payload=None,
            retried=False,
        )


def test_request_json_success_still_works():
    """Successful responses (code=0) continue to return data as before."""
    service, http = _make_service()
    http.request.return_value = _FakeResponse(
        200,
        json_data={
            "code": 0,
            "data": {
                "items": [{"open_department_id": "dep-1", "name": "Test"}],
                "has_more": False,
            },
        },
    )

    result = service._request_json_once(
        method="GET",
        url="https://open.feishu.cn/open-apis/contact/v3/departments/0/children",
        token="test-token",
        params={"page_size": 50},
        json_payload=None,
        retried=False,
    )

    assert "items" in result
    assert len(result["items"]) == 1


def test_request_json_http_200_with_nonzero_code_raises_api_error():
    """HTTP 200 with code != 0 should still raise FeishuApiError (unchanged)."""
    service, http = _make_service()
    # Use a non-auth error code so the auth-retry path is not triggered.
    http.request.return_value = _FakeResponse(
        200,
        json_data={
            "code": 99999999,
            "msg": "some other API error",
        },
    )

    with pytest.raises(FeishuApiError) as exc_info:
        service._request_json_once(
            method="GET",
            url="https://open.feishu.cn/open-apis/contact/v3/departments/0/children",
            token="test-token",
            params={"page_size": 50},
            json_payload=None,
            retried=False,
        )

    assert exc_info.value.code == 99999999
    assert "some other API error" in exc_info.value.msg
