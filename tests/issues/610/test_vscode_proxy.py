#!/usr/bin/env python3
"""Unit tests for VSCode proxy request handling."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.modules.workspace import vscode_proxy


def test_prepare_request_headers_forces_identity_encoding():
    headers = {
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "User-Agent": "browser",
    }

    prepared = vscode_proxy._prepare_request_headers(headers, "http://remote.example:8080/path")

    assert prepared["Host"] == "remote.example:8080"
    assert prepared["Accept-Encoding"] == "identity"
    assert prepared["User-Agent"] == "browser"
    assert "Connection" not in prepared
    assert "gzip" not in prepared.values()


def test_streaming_proxy_sends_identity_encoding_upstream():
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Type": "text/html"}
    response.iter_content.return_value = [b"<html></html>"]

    with patch.object(vscode_proxy._proxy_session, "request", return_value=response) as request:
        status, headers, content = vscode_proxy.proxy_request_streaming(
            "GET",
            "http://remote.example:8080/",
            {"accept-encoding": "br", "Host": "openace.local"},
        )

    assert status == 200
    assert headers["Content-Type"] == "text/html"
    assert list(content) == [b"<html></html>"]
    forwarded_headers = request.call_args.kwargs["headers"]
    assert forwarded_headers["Accept-Encoding"] == "identity"
    assert forwarded_headers["Host"] == "remote.example:8080"
    assert "accept-encoding" not in forwarded_headers
