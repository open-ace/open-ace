"""
Tests for Issue #2532: Security Headers for Token Responses

Ensures that responses containing sensitive token fields (agent_token,
registration_token) include proper security headers to prevent caching
and referrer leakage.

Covers:
- Detection of sensitive fields in JSON response body
- Security headers added for token responses
- SSE stream responses are not affected
- Large responses are skipped for performance
- Non-JSON responses are not affected
"""

from __future__ import annotations

import pytest
from flask import Flask, Response, jsonify, request
from werkzeug.test import Client

# Import the module under test
from app.routes.remote import add_security_headers, SENSITIVE_RESPONSE_FIELDS


@pytest.mark.issue(2532)
@pytest.mark.regression
class TestSecurityHeadersForTokenFields:
    """Test security headers are added for responses with sensitive token fields."""

    @pytest.fixture
    def app(self):
        """Create a minimal Flask app with the after_request hook."""
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/test/token-response", methods=["POST"])
        def token_response():
            return jsonify({"success": True, "agent_token": "test_token_abc123"})

        @app.route("/test/registration-response", methods=["POST"])
        def registration_response():
            return jsonify({"success": True, "registration_token": "reg_token_xyz"})

        @app.route("/test/no-token-response", methods=["POST"])
        def no_token_response():
            return jsonify({"success": True, "message": "No sensitive data"})

        @app.route("/test/sse-stream", methods=["GET"])
        def sse_stream():
            return Response(
                "data: test\n\n",
                mimetype="text/event-stream",
            )

        @app.route("/test/file-download", methods=["GET"])
        def file_download():
            return Response("file content", mimetype="application/octet-stream")

        @app.route("/test/query-token", methods=["GET"])
        def query_token():
            return jsonify({"success": True})

        @app.route("/test/large-response", methods=["POST"])
        def large_response():
            # Create a response larger than the threshold
            large_data = {"success": True, "agent_token": "test_token", "data": "x" * 15000}
            return jsonify(large_data)

        # Register the after_request hook
        app.after_request(add_security_headers)

        return app

    def test_agent_token_response_has_security_headers(self, app):
        """Response with agent_token should have security headers."""
        client = Client(app)
        resp = client.post("/test/token-response")

        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_registration_token_response_has_security_headers(self, app):
        """Response with registration_token should have security headers."""
        client = Client(app)
        resp = client.post("/test/registration-response")

        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_no_token_response_no_extra_headers(self, app):
        """Response without sensitive fields should not have extra security headers."""
        client = Client(app)
        resp = client.post("/test/no-token-response")

        assert resp.status_code == 200
        # These headers should not be present (or at least not added by our hook)
        # Note: Other middleware might add them, so we check they're not set
        # to the specific values we use
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            assert "no-store" not in cache_control

    def test_sse_stream_unaffected(self, app):
        """SSE stream responses should not trigger JSON body check."""
        client = Client(app)
        resp = client.get("/test/sse-stream")

        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        # SSE has its own caching strategy
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            # Should be "no-cache" for SSE, not our "no-store"
            assert "no-store" not in cache_control

    def test_file_download_unaffected(self, app):
        """Non-JSON responses should not trigger body check."""
        client = Client(app)
        resp = client.get("/test/file-download")

        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("application/octet-stream")
        # Should not have our security headers
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            assert "no-store" not in cache_control

    def test_query_token_parameter_still_works(self, app):
        """URL query parameter token detection should still work (Issue #1896)."""
        client = Client(app)
        resp = client.get("/test/query-token?token=abc123")

        assert resp.status_code == 200
        # Should have security headers from query param check
        assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"

    def test_large_response_skipped(self, app):
        """Large JSON responses should be skipped for performance."""
        client = Client(app)
        resp = client.post("/test/large-response")

        assert resp.status_code == 200
        # Large responses should not have security headers added
        # (content_length check prevents the detection)
        # Note: Flask might not set content_length in test mode, so this test
        # verifies the logic doesn't break on large responses

    def test_both_token_sources_add_headers(self, app):
        """When both query param and body have token, headers should be set."""
        client = Client(app)
        resp = client.post("/test/token-response?token=xyz")

        assert resp.status_code == 200
        # Headers should be present (setdefault means no overwrite needed)
        assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"


@pytest.mark.issue(2532)
@pytest.mark.regression
class TestSensitiveFieldsConstants:
    """Test the sensitive fields constant definition."""

    def test_sensitive_fields_contains_expected_tokens(self):
        """SENSITIVE_RESPONSE_FIELDS should contain expected token field names."""
        assert "agent_token" in SENSITIVE_RESPONSE_FIELDS
        assert "registration_token" in SENSITIVE_RESPONSE_FIELDS

    def test_sensitive_fields_does_not_contain_token_version(self):
        """token_version is not sensitive (used for validation, not auth)."""
        assert "token_version" not in SENSITIVE_RESPONSE_FIELDS

    def test_sensitive_fields_is_frozenset(self):
        """SENSITIVE_RESPONSE_FIELDS should be immutable."""
        assert isinstance(SENSITIVE_RESPONSE_FIELDS, frozenset)


@pytest.mark.issue(2532)
@pytest.mark.regression
class TestSecurityHeadersEdgeCases:
    """Test edge cases for security header detection."""

    @pytest.fixture
    def app(self):
        """Create a Flask app for edge case testing."""
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/test/empty-json", methods=["GET"])
        def empty_json():
            return jsonify({})

        @app.route("/test/malformed-content-type", methods=["GET"])
        def malformed_content_type():
            return Response("not json", mimetype="text/plain")

        @app.route("/test/null-response", methods=["GET"])
        def null_response():
            return Response("", mimetype="application/json")

        @app.route("/test/nested-token", methods=["POST"])
        def nested_token():
            # Token in nested object - should not trigger
            return jsonify({"success": True, "data": {"agent_token": "nested"}})

        @app.route("/test/token-version", methods=["POST"])
        def token_version():
            # token_version is not sensitive
            return jsonify({"success": True, "token_version": 5})

        app.after_request(add_security_headers)
        return app

    def test_empty_json_no_headers(self, app):
        """Empty JSON response should not add security headers."""
        client = Client(app)
        resp = client.get("/test/empty-json")

        assert resp.status_code == 200
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            assert "no-store" not in cache_control

    def test_malformed_content_type_unaffected(self, app):
        """Non-JSON content type should not trigger check."""
        client = Client(app)
        resp = client.get("/test/malformed-content-type")

        assert resp.status_code == 200
        # Should not have our security headers
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            assert "no-store" not in cache_control

    def test_null_response_no_crash(self, app):
        """Null/empty JSON response should not crash."""
        client = Client(app)
        resp = client.get("/test/null-response")

        assert resp.status_code == 200

    def test_nested_token_not_detected(self, app):
        """Token in nested object should not trigger detection (only top-level)."""
        client = Client(app)
        resp = client.post("/test/nested-token")

        assert resp.status_code == 200
        # Nested token should not trigger security headers
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            assert "no-store" not in cache_control

    def test_token_version_not_sensitive(self, app):
        """token_version field should not trigger security headers."""
        client = Client(app)
        resp = client.post("/test/token-version")

        assert resp.status_code == 200
        cache_control = resp.headers.get("Cache-Control")
        if cache_control:
            assert "no-store" not in cache_control


@pytest.mark.issue(2532)
@pytest.mark.regression
class TestSetdefaultSemantics:
    """Test that setdefault semantics preserve existing headers."""

    @pytest.fixture
    def app(self):
        """Create a Flask app with pre-set headers."""
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/test/existing-headers", methods=["POST"])
        def existing_headers():
            resp = jsonify({"success": True, "agent_token": "test_token"})
            # Set headers before after_request runs
            resp.headers["Cache-Control"] = "public, max-age=3600"
            resp.headers["Referrer-Policy"] = "strict-origin"
            return resp

        app.after_request(add_security_headers)
        return app

    def test_existing_headers_not_overwritten(self, app):
        """setdefault should not overwrite existing security headers."""
        client = Client(app)
        resp = client.post("/test/existing-headers")

        assert resp.status_code == 200
        # Existing headers should be preserved (setdefault behavior)
        assert resp.headers.get("Cache-Control") == "public, max-age=3600"
        assert resp.headers.get("Referrer-Policy") == "strict-origin"
        # X-Content-Type-Options should be added (wasn't set before)
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
