"""
Open ACE - Flask Application Factory

This module provides the Flask application factory for the Open ACE platform.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from urllib.parse import urlparse

from flask import Flask, g, has_request_context, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Cap for client-supplied request ids; the correlation id is echoed into the
# response header and written to logs, so a multi-kB value is an abuse vector.
REQUEST_ID_MAX_LENGTH = 128
# C0 control chars (incl. CR/LF) + DEL. Stripped from the inbound X-Request-ID
# to defeat header-injection (CRLF smuggling) and log-injection (log forging).
_REQUEST_ID_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LOCAL_CORS_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Cache for CORS allowed origins (populated at startup)
_CORS_ALLOWED_ORIGINS_CACHE: set[str] | None = None
_CORS_ORIGINS_ENV_SNAPSHOT: str | None = None


def _sanitize_request_id(value: str | None) -> str:
    """Make a client-supplied request id safe to echo back and log.

    The X-Request-ID is trusted enough to propagate verbatim into the response
    header and the error log, so any control characters (notably CRLF) are a
    header/log-injection surface. Strip control chars, trim whitespace, and cap
    the length; return an empty string when nothing usable remains.
    """
    if not value:
        return ""
    cleaned = _REQUEST_ID_CONTROL_CHARS.sub("", value).strip()
    return cleaned[:REQUEST_ID_MAX_LENGTH]


def _normalize_origin(origin: str) -> str | None:
    """Normalize origin to scheme://host:port format.

    Returns:
        Normalized origin string, or None if invalid.
    """
    try:
        parsed = urlparse(origin)
    except Exception:
        return None

    # Validate scheme
    if parsed.scheme not in ("http", "https"):
        return None

    # Normalize host
    host = parsed.hostname
    if not host:
        return None
    host = host.rstrip(".").lower()

    # Infer port if missing
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if parsed.scheme == "https" else 80

    return f"{parsed.scheme}://{host}:{port}"


def _build_cors_origins_cache() -> set[str]:
    """Build and validate CORS origins cache at startup.

    Parses OPENACE_CORS_ALLOWED_ORIGINS, validates scheme, normalizes each origin,
    and logs warnings for invalid entries.
    """
    raw_value = os.environ.get("OPENACE_CORS_ALLOWED_ORIGINS", "")
    raw_origins = {origin.strip() for origin in raw_value.split(",") if origin.strip()}

    normalized_origins: set[str] = set()
    for origin in raw_origins:
        normalized = _normalize_origin(origin)
        if normalized is None:
            logger.warning(f"CORS origin '{origin}' is invalid (must be http/https URL), skipping")
            continue
        if normalized != origin:
            logger.warning(
                f"CORS origin '{origin}' normalized to '{normalized}', "
                f"please update your config to use the normalized form"
            )
        normalized_origins.add(normalized)

    return normalized_origins


def _get_allowed_cors_origins() -> set[str]:
    """Return explicitly allowed cross-origin API callers (cached).

    The cache is invalidated if the environment variable changes.
    """
    global _CORS_ALLOWED_ORIGINS_CACHE, _CORS_ORIGINS_ENV_SNAPSHOT

    current_env = os.environ.get("OPENACE_CORS_ALLOWED_ORIGINS", "")

    # Check if environment variable has changed since last cache
    if current_env != _CORS_ORIGINS_ENV_SNAPSHOT:
        _CORS_ORIGINS_ENV_SNAPSHOT = current_env
        _CORS_ALLOWED_ORIGINS_CACHE = None

    if _CORS_ALLOWED_ORIGINS_CACHE is None:
        _CORS_ALLOWED_ORIGINS_CACHE = _build_cors_origins_cache()
    return _CORS_ALLOWED_ORIGINS_CACHE


def _reset_cors_origins_cache():
    """Reset CORS origins cache (for testing)."""
    global _CORS_ALLOWED_ORIGINS_CACHE, _CORS_ORIGINS_ENV_SNAPSHOT
    _CORS_ALLOWED_ORIGINS_CACHE = None
    _CORS_ORIGINS_ENV_SNAPSHOT = None


def _is_allowed_local_webui_origin(origin: str) -> bool:
    """Allow WebUI origins in the dev port range when loopback or same-host.

    Loopback hostnames (``localhost``/``127.0.0.1``/``::1``) are always
    allowed on the webui port range 3100-3200. Non-loopback hostnames are
    allowed only when they match the hostname the browser used to reach the
    backend (taken from the current request's ``Host`` header). This covers
    LAN IP / container-hostname access where the per-user qwen-code-webui
    iframe is served on a webui port using the same hostname the browser used
    to reach the backend. Without this, Firefox blocks the iframe's
    credentialed API calls with "NetworkError when attempting to fetch
    resource." (Issue #1859)
    """
    try:
        parsed = urlparse(origin)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    if parsed.port is None:
        return False

    if not (3100 <= parsed.port <= 3200):
        return False

    if parsed.hostname in _LOCAL_CORS_HOSTS:
        return True

    # Allow the server's own hostname (e.g. LAN IP / container hostname) so
    # that browsers reaching the backend via a non-loopback address can still
    # load the per-user qwen-code-webui iframe on a webui port. The Host
    # header reflects the address the browser itself used, so reflecting this
    # origin does not open a credential-reuse vector for arbitrary third
    # parties (they would have to control a webui port on the same host the
    # victim is browsing).
    #
    # Parse the Host header via ``urlparse("//" + host)`` so IPv6 literals
    # (e.g. ``[2001:db8::1]:5000``) are normalized to the bare address
    # (``2001:db8::1``), matching ``parsed.hostname``. A naive
    # ``host.split(":", 1)[0]`` would yield ``"[2001"`` for IPv6 and silently
    # fail closed. (Issue #1859 review follow-up.)
    try:
        if has_request_context():
            host_header = (urlparse(f"//{request.host or ''}").hostname or "").lower()
            if host_header and parsed.hostname == host_header:
                return True
    except Exception:
        pass

    return False


def _is_allowed_cors_origin(origin: str) -> bool:
    """Return whether an Origin should receive credentialed API CORS headers."""
    if not origin:
        return False
    # Normalize the incoming origin for comparison
    normalized = _normalize_origin(origin)
    if not normalized:
        return False
    if normalized in _get_allowed_cors_origins():
        return True
    return _is_allowed_local_webui_origin(origin)


def create_app(config=None):
    """
    Flask application factory.

    Args:
        config: Optional configuration dictionary or object.

    Returns:
        Flask application instance.
    """
    app = Flask(__name__, static_folder="../static", template_folder="../templates")

    # Trust nginx proxy headers for correct scheme detection
    # x_proto=1: trust X-Forwarded-Proto header (https/http)
    # x_for=1: trust X-Forwarded-For header
    # This is needed for HTTPS iframe URL generation in multi-user mode
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # Terminal WebSocket must be handled at the WSGI layer because
    # Flask/Werkzeug cannot reliably route upgraded connections.
    # See issue #147 and #557 for context.
    from app.terminal_ws_middleware import TerminalWebSocketMiddleware

    app.wsgi_app = TerminalWebSocketMiddleware(app.wsgi_app)

    # Load configuration
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    if config:
        if isinstance(config, dict):
            app.config.update(config)
        else:
            app.config.from_object(config)

    # NOTE: do NOT set a global MAX_CONTENT_LENGTH here. A Flask app-wide cap is
    # enforced by Werkzeug *before* the view runs and would 413 legitimate
    # authenticated upload endpoints that carry >256KB bodies (avatar uploads,
    # /api/upload/messages, /api/upload/batch, remote proxy bodies) -- a
    # functional regression. The SAML ACS parse-DoS cap is instead scoped to the
    # single unauthenticated /acs route (see app.routes.sso.saml_acs), which
    # checks request.content_length against a 256KB ceiling and returns 413.

    from app.utils.security_env import get_secret_key_for_app

    # SECRET_KEY configuration with security checks
    app.config["SECRET_KEY"] = get_secret_key_for_app(app.config.get("SECRET_KEY"))

    # Register error handlers
    register_error_handlers(app)

    # Register blueprints
    register_blueprints(app)

    # Ensure all tables exist (runs DDL once at startup, not per-request)
    from app.repositories.schema_init import ensure_all_tables

    ensure_all_tables()

    # Pre-check API key encryption availability
    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        APIKeyProxyService()  # __init__ calls _get_encryption_key() internally
    except RuntimeError as e:
        if os.environ.get("FLASK_ENV") == "production":
            raise RuntimeError(f"API key encryption misconfigured: {e}")
        logger.warning(f"API key proxy unavailable: {e}. Storing API keys will fail.")
    except Exception:
        pass  # cryptography not installed — handled at encrypt/decrypt time

    def _get_security_status() -> str:
        """Get security status for health check endpoint.

        Returns:
            "ok" if all security checks pass, "warnings" if there are non-blocking
            issues, "check_logs" if there are significant concerns.
        """
        from app.utils.security_env import is_weak_secret_value

        warnings = []

        # Check database password
        database_url = os.environ.get("DATABASE_URL", "")
        if database_url:
            try:
                # Extract password from URL
                auth_part = database_url.split("://", 1)[1].split("@", 1)[0]
                if ":" in auth_part:
                    password = auth_part.split(":", 1)[1]
                    # URL decode
                    from urllib.parse import unquote
                    password = unquote(password)
                    if password == "ace-secret":
                        warnings.append("default_db_password")
            except Exception:
                pass

        # Check SECRET_KEY
        secret_key = os.environ.get("SECRET_KEY", "")
        if is_weak_secret_value(secret_key):
            warnings.append("weak_secret_key")

        # Check OPENACE_ENCRYPTION_KEY
        enc_key = os.environ.get("OPENACE_ENCRYPTION_KEY", "")
        if is_weak_secret_value(enc_key):
            warnings.append("weak_encryption_key")

        if not warnings:
            return "ok"
        elif len(warnings) <= 2:
            return "warnings"
        else:
            return "check_logs"

    # Health check endpoint
    @app.route("/health")
    def health_check():
        """Health check endpoint for Docker and load balancers.

        Returns basic health status with security status indicator.
        Security details are intentionally limited to prevent information leakage.
        """
        from app.utils.version import get_git_commit

        return jsonify(
            {
                "status": "healthy",
                "service": "open-ace",
                "version": get_git_commit(),
                "security_status": _get_security_status(),
            }
        )

    # Detailed security health check endpoint (should be access-controlled via reverse proxy)
    @app.route("/health/security")
    def health_security():
        """Detailed security status endpoint.

        This endpoint returns detailed security check results.
        Access should be restricted via reverse proxy to internal networks only.

        Returns detailed security status without exposing actual secret values.
        """
        from app.utils.security_env import is_weak_secret_value

        checks = {}

        # Check database password
        database_url = os.environ.get("DATABASE_URL", "")
        if database_url:
            try:
                auth_part = database_url.split("://", 1)[1].split("@", 1)[0]
                if ":" in auth_part:
                    password = auth_part.split(":", 1)[1]
                    from urllib.parse import unquote
                    password = unquote(password)
                    checks["db_password"] = "default_value" if password == "ace-secret" else "ok"
                else:
                    checks["db_password"] = "ok"
            except Exception:
                checks["db_password"] = "ok"
        else:
            checks["db_password"] = "ok"

        # Check SECRET_KEY
        secret_key = os.environ.get("SECRET_KEY", "")
        if not secret_key:
            checks["secret_key"] = "missing"
        elif is_weak_secret_value(secret_key):
            checks["secret_key"] = "weak"
        else:
            checks["secret_key"] = "ok"

        # Check OPENACE_ENCRYPTION_KEY
        enc_key = os.environ.get("OPENACE_ENCRYPTION_KEY", "")
        if not enc_key:
            checks["encryption_key"] = "missing"
        elif is_weak_secret_value(enc_key):
            checks["encryption_key"] = "weak"
        else:
            checks["encryption_key"] = "ok"

        # Overall status
        all_ok = all(v == "ok" for v in checks.values())

        return jsonify(
            {
                "status": "ok" if all_ok else "warnings",
                "checks": checks,
            }
        )

    # Start background services
    start_background_services()

    logger.info("Open ACE application initialized")
    return app


def register_error_handlers(app):
    """Register error handlers for the application."""

    @app.before_request
    def assign_request_id():
        """Propagate or generate a per-request correlation id (X-Request-ID).

        A client-supplied id is sanitized first (control chars stripped, length
        capped) because it is echoed on the response and written to logs; if the
        sanitized value is empty we fall back to a generated id.
        """
        sanitized = _sanitize_request_id(request.headers.get("X-Request-ID"))
        g.request_id = sanitized or uuid.uuid4().hex

    @app.after_request
    def echo_request_id(response):
        """Echo the correlation id on the response for client-side tracing."""
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        return response

    @app.after_request
    def add_cors_headers(response):
        """Add CORS headers for iframe integration with qwen-code-webui."""
        if request.path.startswith("/api/"):
            origin = request.headers.get("Origin", "")
            if _is_allowed_cors_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
                response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @app.before_request
    def handle_options_preflight():
        """Handle CORS preflight requests before routing.

        This must be a before_request hook because Flask's automatic OPTIONS
        handler for specific routes takes precedence over the generic
        /api/<path:path> route matcher.
        """
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            origin = request.headers.get("Origin", "")
            if _is_allowed_cors_origin(origin):
                response = jsonify({"status": "ok"})
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["X-Request-ID"] = getattr(g, "request_id", "")
                return response
            # Even for blocked origins, return success to avoid information leakage
            response = jsonify({"status": "ok"})
            response.headers["X-Request-ID"] = getattr(g, "request_id", "")
            return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        """Handle all HTTP exceptions and return JSON for API routes."""
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return e.get_response()

    @app.errorhandler(Exception)
    def handle_generic_exception(e):
        """Handle unexpected exceptions."""
        logger.exception("Unexpected error occurred [request_id=%s]", getattr(g, "request_id", "-"))
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        raise e


def register_blueprints(app):
    """Register all application blueprints."""
    from app.routes.admin import admin_bp
    from app.routes.ai_agent_settings import ai_agent_settings_bp
    from app.routes.alerts import alerts_bp
    from app.routes.analysis import analysis_bp
    from app.routes.analytics import analytics_bp
    from app.routes.api_keys import api_keys_bp
    from app.routes.auth import auth_bp
    from app.routes.autonomous import autonomous_bp
    from app.routes.compliance import compliance_bp
    from app.routes.fetch import fetch_bp
    from app.routes.fs import fs_bp
    from app.routes.governance import governance_bp
    from app.routes.insights import insights_bp
    from app.routes.mapping_rules import mapping_rules_bp
    from app.routes.messages import messages_bp
    from app.routes.pages import pages_bp
    from app.routes.project_categories import project_categories_bp
    from app.routes.projects import projects_bp
    from app.routes.quota import quota_bp
    from app.routes.remote import remote_bp
    from app.routes.report import report_bp
    from app.routes.roi import roi_bp
    from app.routes.smtp_config import smtp_config_bp
    from app.routes.sso import sso_bp
    from app.routes.system import system_bp
    from app.routes.tenant import tenant_bp
    from app.routes.tool_accounts import tool_accounts_bp
    from app.routes.upload import upload_bp
    from app.routes.usage import usage_bp
    from app.routes.workspace import workspace_bp

    app.register_blueprint(usage_bp, url_prefix="/api")
    app.register_blueprint(messages_bp, url_prefix="/api")
    app.register_blueprint(analysis_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.register_blueprint(upload_bp, url_prefix="/api")
    app.register_blueprint(fetch_bp, url_prefix="/api")
    app.register_blueprint(fs_bp, url_prefix="/api")
    app.register_blueprint(report_bp, url_prefix="/api")
    app.register_blueprint(governance_bp, url_prefix="/api")
    app.register_blueprint(analytics_bp, url_prefix="/api")
    app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
    app.register_blueprint(tenant_bp)
    app.register_blueprint(sso_bp)
    app.register_blueprint(compliance_bp)
    app.register_blueprint(alerts_bp, url_prefix="/api")
    app.register_blueprint(roi_bp, url_prefix="/api")
    app.register_blueprint(quota_bp, url_prefix="/api")
    app.register_blueprint(system_bp, url_prefix="/api")
    app.register_blueprint(tool_accounts_bp, url_prefix="/api")
    app.register_blueprint(mapping_rules_bp)
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.register_blueprint(project_categories_bp, url_prefix="/api")
    app.register_blueprint(insights_bp, url_prefix="/api")
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    from app.routes.run_timeline import run_timeline_bp

    app.register_blueprint(run_timeline_bp, url_prefix="/api/remote")
    from app.routes.policy import policy_bp

    app.register_blueprint(policy_bp, url_prefix="/api")
    app.register_blueprint(api_keys_bp, url_prefix="/api")
    app.register_blueprint(autonomous_bp, url_prefix="/api/autonomous")
    app.register_blueprint(ai_agent_settings_bp, url_prefix="/api")
    app.register_blueprint(smtp_config_bp, url_prefix="/api")
    # model-gateway (removable): admin config routes for the optional LiteLLM gateway
    from app.routes.model_gateway import model_gateway_bp

    app.register_blueprint(model_gateway_bp, url_prefix="/api")
    # feature flags: returns current state of all configurable features
    from app.routes.feature_flags import feature_flags_bp

    app.register_blueprint(feature_flags_bp)
    app.register_blueprint(pages_bp)

    logger.info("All blueprints registered")


def start_background_services():
    """Start background services like data fetch scheduler."""
    try:
        from app.services.data_fetch_scheduler import init_scheduler

        init_scheduler()
    except Exception as e:
        logger.warning(f"Failed to start data fetch scheduler: {e}")

    try:
        from app.services.quota_enforcement_scheduler import init_quota_enforcement

        init_quota_enforcement()
    except Exception as e:
        logger.warning(f"Failed to start quota enforcement scheduler: {e}")

    try:
        from app.utils.config import is_autonomous_enabled

        if is_autonomous_enabled():
            from app.services.autonomous_scheduler import init_autonomous_scheduler

            init_autonomous_scheduler()
        else:
            logger.info("Autonomous scheduler disabled by configuration")
            logger.info(
                "To enable it again: set autonomous.enabled=true in config.json and restart the server"
            )
    except Exception as e:
        logger.warning(f"Failed to start autonomous scheduler: {e}")

    # Start alert compensation worker
    try:
        from app.services.alert_compensation_worker import init_alert_compensation

        init_alert_compensation()
    except Exception as e:
        logger.warning(f"Failed to start alert compensation worker: {e}")

    # Start scheduler health monitor
    try:
        from app.services.scheduler_health_monitor import init_scheduler_health_monitor

        init_scheduler_health_monitor()
    except Exception as e:
        logger.warning(f"Failed to start scheduler health monitor: {e}")

    # Issue #1815 Finding 2: Start SSO auth state cleanup task
    try:
        from app.modules.sso.manager import init_sso_cleanup

        init_sso_cleanup()
    except Exception as e:
        logger.warning(f"Failed to start SSO auth state cleanup: {e}")

    logger.info("Background services started")
