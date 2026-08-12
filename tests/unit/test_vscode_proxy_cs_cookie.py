"""Unit tests for ``ensure_cs_cookie`` (code-server HTTP-proxy cookie auth).

The endpoint-level behavior is covered by tests/issues/2183 + 610 (they verify
the proxy endpoint no longer ImportErrors and proceeds past auth). These unit
tests cover the ``cs_password``-present path those issue tests skip: cookie
extraction, caching, and fail-soft behavior.
"""

from unittest.mock import patch

import requests

from app.modules.workspace import vscode_proxy


def _resp(set_cookie="", exc=None):
    if exc:
        raise exc
    r = requests.Response()
    r.headers["Set-Cookie"] = set_cookie
    return r


def test_no_cs_password_returns_empty():
    info = {"cs_password": ""}
    assert vscode_proxy.ensure_cs_cookie(info, "http://remote:8080", "vs1") == ""


def test_cached_cookie_is_reused_without_login():
    info = {"cs_password": "pw", "cs_cookie": "cookie=abc"}
    with patch.object(vscode_proxy, "_proxy_session") as sess:
        assert vscode_proxy.ensure_cs_cookie(info, "http://remote:8080", "vs1") == "cookie=abc"
        sess.post.assert_not_called()


def test_login_extracts_and_caches_cookie():
    info = {"cs_password": "pw"}
    with patch.object(vscode_proxy, "_proxy_session") as sess:
        sess.post.return_value = _resp(set_cookie="cookie=session-key; Path=/; HttpOnly")
        result = vscode_proxy.ensure_cs_cookie(info, "http://remote:8080/", "vs1")
    assert result == "cookie=session-key"
    assert info["cs_cookie"] == "cookie=session-key"  # cached for reuse
    sess.post.assert_called_once()
    # Login targets <url>/login with the password, no redirect follow.
    args, kwargs = sess.post.call_args
    assert args[0] == "http://remote:8080/login"
    assert kwargs["data"] == {"password": "pw"}
    assert kwargs["allow_redirects"] is False


def test_login_failure_is_swallowed():
    info = {"cs_password": "pw"}
    with patch.object(vscode_proxy, "_proxy_session") as sess:
        sess.post.side_effect = requests.ConnectionError("refused")
        result = vscode_proxy.ensure_cs_cookie(info, "http://remote:8080", "vs1")
    assert result == ""
    assert "cs_cookie" not in info  # nothing cached on failure


def test_login_without_set_cookie_returns_empty():
    info = {"cs_password": "pw"}
    with patch.object(vscode_proxy, "_proxy_session") as sess:
        sess.post.return_value = _resp(set_cookie="")
        assert vscode_proxy.ensure_cs_cookie(info, "http://remote:8080", "vs1") == ""
    assert "cs_cookie" not in info
