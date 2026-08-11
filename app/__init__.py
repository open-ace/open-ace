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

import sqlalchemy as sa
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


def _precheck_encryption_registry():
    """
    Pre-check encryption key registry and all encryption paths.

    This function is called during application startup to verify that:
    1. EncryptionKeyRegistry can be initialized
    2. Key derivation and encryption/decryption work
    3. All encryption paths are functional

    In production, failures raise RuntimeError (fail-fast).
    In development, failures log warnings but allow startup.

    Issue: #1820
    """
    from app.utils.security_env import is_strict_mode

    # Try to initialize EncryptionKeyRegistry
    try:
        from app.utils.encryption_key_registry import get_registry

        registry = get_registry()

        # Verify encryption/decryption roundtrip
        test_plaintext = "startup_test_secret"
        ciphertext = registry.encrypt(test_plaintext)
        result = registry.decrypt(ciphertext)

        if result is None:
            raise RuntimeError("Encryption/decryption roundtrip failed")

        decrypted, key_id = result
        if decrypted != test_plaintext:
            raise RuntimeError(
                f"Encryption/decryption mismatch: expected '{test_plaintext}', got '{decrypted}'"
            )

        logger.info(
            f"EncryptionKeyRegistry initialized: "
            f"keys={registry.get_key_count()}, "
            f"primary_key_id={registry.get_primary_key_id()}, "
            f"config_version={registry.get_config_version()}"
        )

    except RuntimeError as e:
        if is_strict_mode():
            raise RuntimeError(f"Encryption key registry initialization failed: {e}")
        logger.warning(f"Encryption key registry initialization failed: {e}")
    except Exception as e:
        if is_strict_mode():
            raise RuntimeError(f"Unexpected error initializing encryption registry: {e}")
        logger.warning(f"Unexpected error initializing encryption registry: {e}")

    # Pre-check SMTP password encryption path
    # This also covers SSO client_secret encryption (uses same password manager)
    try:
        from app.utils.smtp_crypto import get_password_manager

        manager = get_password_manager()
        test_password = "smtp_test_password"
        encrypted = manager.encrypt(test_password)
        decrypted = manager.decrypt(encrypted)
        if decrypted != test_password:
            raise RuntimeError("SMTP password encryption/decryption mismatch")
    except RuntimeError as e:
        if is_strict_mode():
            raise RuntimeError(f"SMTP encryption check failed: {e}")
        logger.warning(f"SMTP encryption check failed: {e}")
    except Exception:
        pass  # cryptography not installed — handled at encrypt/decrypt time


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

    # Issue #2331: Require explicit security mode in production paths
    # Must run BEFORE any application initialization to fail fast
    from app.utils.security_mode import require_explicit_mode

    try:
        require_explicit_mode()
    except RuntimeError as e:
        # Fail fast: cannot determine security mode
        # The error message from require_explicit_mode() provides clear guidance
        logger.error(f"Security mode validation failed: {e}")
        logger.error("Cannot start application without explicit security mode")
        # Always re-raise - we cannot safely determine mode to make decisions
        raise

    # Terminal WebSocket must be handled at the WSGI layer because
    # Flask/Werkzeug cannot reliably route upgraded connections.
    # See issue #147 and #557 for context.
    from app.terminal_ws_middleware import TerminalWebSocketMiddleware

    app.wsgi_app = TerminalWebSocketMiddleware(app.wsgi_app)

    # Query parameter sanitizer for sensitive tokens (Issue #1896)
    # Sanitizes token/session_token/auth/api_key from logs
    from app.middleware.query_param_sanitizer import QueryParamSanitizer

    app.wsgi_app = QueryParamSanitizer(app.wsgi_app)

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

    # Initialize Prometheus metrics (Issue #2186)
    # Only for web workers - scheduler has its own metrics server
    scheduler_mode = os.environ.get("SCHEDULER_MODE", "web")
    prometheus_initialized = False
    if scheduler_mode != "scheduler":
        try:
            from prometheus_flask_exporter import PrometheusMetrics

            # Initialize metrics with /metrics path
            # PrometheusMetrics auto-registers to Flask app on init
            PrometheusMetrics(
                app,
                path="/metrics",
                group_by_endpoint=True,
                buckets=[0.01, 0.05, 0.1, 0.5, 1, 5],
            )
            prometheus_initialized = True
            logger.info("Prometheus metrics initialized on /metrics")
        except ImportError:
            logger.warning(
                "prometheus_flask_exporter not available - metrics endpoint will return 503. "
                "Install with: pip install prometheus_flask_exporter"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize Prometheus metrics: {e}")

    # Fallback /metrics endpoint if PrometheusMetrics not initialized
    if not prometheus_initialized:

        @app.route("/metrics")
        def metrics_endpoint():
            """Fallback metrics endpoint when prometheus_flask_exporter is not available."""
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "prometheus_flask_exporter not installed. Install with: pip install prometheus_flask_exporter",
                    }
                ),
                503,
            )

    # Register blueprints
    register_blueprints(app)

    # Schema initialization: distinguish production vs development paths (Issue #2190)
    from app.repositories.database import is_postgresql
    from app.repositories.schema_guard import (
        SchemaCompatibilityError,
        check_schema_compatibility,
        get_environment_mode,
    )

    env_mode = get_environment_mode()

    if is_postgresql() and env_mode == "production":
        # PostgreSQL production path: check schema version, do NOT execute DDL
        from app.repositories.database import Database

        db = Database()
        conn = db.get_connection()
        try:
            check_schema_compatibility(conn)
        except SchemaCompatibilityError as e:
            logger.error(f"Schema compatibility check failed: {e}")
            raise RuntimeError(
                f"Database schema is not compatible. {e}\n"
                f"Current revision: {e.current_revision}\n"
                f"Minimum required: {e.min_revision}\n"
                "Run 'alembic upgrade head' to migrate database."
            )
        finally:
            conn.close()
        logger.info("Production schema version check passed")
    else:
        # SQLite development path: allow bootstrap
        from app.repositories.schema_init import ensure_all_tables

        ensure_all_tables()
        logger.info(f"Development schema bootstrap completed (mode={env_mode})")

    # Pre-check encryption key registry (Issue #1820, #2186)
    try:
        _precheck_encryption_registry()
    except Exception as e:
        # Record initialization error for /readyz check
        from app.utils.health_checks import set_init_error

        set_init_error(str(e), category="encryption")
        # Re-raise in production to prevent startup
        from app.utils.security_mode import is_production

        if is_production():
            raise
        logger.warning(f"Encryption pre-check failed (non-production): {e}")

    # Pre-check API key encryption availability
    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        APIKeyProxyService()  # __init__ calls _get_encryption_key() internally
    except RuntimeError as e:
        from app.utils.security_mode import is_production

        if is_production():
            raise RuntimeError(f"API key encryption misconfigured: {e}")
        logger.warning(f"API key proxy unavailable: {e}. Storing API keys will fail.")
    except Exception:
        pass  # cryptography not installed — handled at encrypt/decrypt time

    # Mark initialization as completed (Issue #2186)
    from app.utils.health_checks import mark_init_completed

    mark_init_completed()

    # Liveness probe endpoint (Issue #2186)
    @app.route("/livez")
    def liveness_check():
        """Liveness probe for Kubernetes.

        Only checks if the process is alive and can respond.
        Does NOT check dependencies to avoid restart storms.
        """
        from app.utils.health_checks import get_current_timestamp

        return jsonify({"status": "alive", "timestamp": get_current_timestamp()}), 200

    # Health check endpoint (deprecated, delegates to /readyz)
    @app.route("/health")
    def health_check():
        """Health check endpoint for Docker and load balancers.

        DEPRECATED: Use /livez for liveness and /readyz for readiness.
        This endpoint now delegates to /readyz for compatibility.
        """
        from werkzeug.wrappers import Response as WerkzeugResponse

        from app.utils.version import get_git_commit

        # Get readiness check result - this returns (Response, status_code)
        ready_response = readiness_check()

        # Handle tuple (Response, status_code)
        if isinstance(ready_response, tuple) and len(ready_response) == 2:
            response_obj, status_code = ready_response
            if isinstance(response_obj, WerkzeugResponse):
                # Get the JSON data from the response
                data = response_obj.get_json()

                # Add deprecation notice
                if isinstance(data, dict):
                    data["deprecated"] = True
                    data["message"] = "Use /livez for liveness and /readyz for readiness"
                    data["version"] = get_git_commit()
                return jsonify(data), status_code

        return ready_response

    # Security status endpoint (Issue #1893)
    @app.route("/security-status")
    def security_status():
        """Security baseline status endpoint for monitoring and health checks.

        Issue #2331: Enhanced with security mode source and pilot metadata.

        Returns security configuration status for the current deployment.
        In production mode, returns HTTP 503 if security baseline fails.
        """
        from app.utils.security_baseline import check_all
        from app.utils.security_mode import (
            SecurityModeSource,
            get_security_mode_with_source,
            load_pilot_metadata,
        )

        results = check_all()
        status_code = 200

        # Issue #2331: Add security mode source information
        try:
            mode, source = get_security_mode_with_source()
            pilot_metadata = load_pilot_metadata()

            results["security_mode"] = {
                "mode": mode.value,
                "source": source.value,
                "explicit": source == SecurityModeSource.EXPLICIT,
            }

            if pilot_metadata:
                results["pilot_metadata"] = pilot_metadata

                # Warning if pilot metadata exists in production mode
                if mode.value == "production":
                    results["warnings"] = results.get("warnings", [])
                    results["warnings"].append(
                        "Production mode running with pilot metadata file. "
                        "This indicates pilot-to-production migration without secret configuration."
                    )
        except RuntimeError as e:
            results["security_mode"] = {
                "mode": "error",
                "source": "error",
                "error": str(e),
            }
            status_code = 503

        # Add migration status for FLASK_ENV users
        results["migration_status"] = {
            "flask_env_deprecated": True,
            "removal_version": "v2.1.0",
            "migration_script": "scripts/migrate_security_mode.sh",
            "issue": "https://github.com/open-ace/open-ace/issues/2331",
        }

        # For production mode, return 503 if unhealthy
        if results.get("status") == "unhealthy":
            status_code = 503

        return jsonify(results), status_code

    # Readiness check endpoint (Issue #2186, #2190, #2331)
    @app.route("/readyz")
    def readiness_check():
        """Readiness check endpoint for Kubernetes and load balancers.

        Checks database connection, schema version compatibility, config directory,
        workspace directory, encryption keys, initialization status, and security mode.
        Returns HTTP 503 if any critical check fails.

        Issue #2331: Also checks security mode source validation.
        """
        from app.repositories.database import Database, is_postgresql
        from app.repositories.schema_guard import (
            MIN_SUPPORTED_REVISION,
            SchemaCompatibilityError,
            check_schema_compatibility,
            get_database_revision,
        )
        from app.utils.health_checks import (
            check_config_directory,
            check_database_connection,
            check_encryption_registry,
            check_initialization_status,
            check_ssh_sync_failure,
            check_workspace_directory,
            run_check_with_timeout,
        )
        from app.utils.security_mode import (
            SecurityModeSource,
            get_security_mode_with_source,
            load_pilot_metadata,
        )

        checks: dict[str, dict[str, str | bool | None | list]] = {
            "database": {"status": "unknown"},
            "schema_version": {"status": "unknown", "compatible": False},
            "config_dir": {"status": "unknown"},
            "workspace_dir": {"status": "unknown"},
            "encryption_keys": {"status": "unknown"},
            "init_status": {"status": "unknown"},
            "security_mode": {"status": "unknown"},
            "ssh_sync": {"status": "unknown"},
        }

        status_code = 200

        # Issue #2331: Check security mode source first
        # Security mode must be EXPLICIT in production-capable paths
        try:
            mode, source = get_security_mode_with_source()
            pilot_metadata = load_pilot_metadata()

            checks["security_mode"]["mode"] = mode.value
            checks["security_mode"]["source"] = source.value
            checks["security_mode"]["pilot_metadata"] = pilot_metadata is not None
            checks["security_mode"]["status"] = "ok"

            # Fail if mode is not explicit in production-capable paths
            # (validation already done by require_explicit_mode(), but double-check here)
            if source != SecurityModeSource.EXPLICIT:
                # Check if we're in test context
                from app.utils.security_mode import is_test_context

                if not is_test_context():
                    checks["security_mode"]["status"] = "not_explicit"
                    checks["security_mode"][
                        "reason"
                    ] = f"Security mode must be explicitly set (current source: {source.value})"
                    status_code = 503

            # Check for pilot metadata in production mode
            if mode.value == "production" and pilot_metadata:
                # Keep status as "ok" but add warning for monitoring
                # This is a migration path, not a failure condition
                checks["security_mode"]["warnings"] = checks["security_mode"].get("warnings", [])
                checks["security_mode"]["warnings"].append(
                    {
                        "type": "pilot_metadata_in_production",
                        "message": (
                            "Production mode running with pilot metadata file present. "
                            "This indicates pilot-to-production migration without secret configuration."
                        ),
                    }
                )
                logger.error(
                    "Production mode running with pilot metadata file! "
                    "Remove metadata and set secrets explicitly."
                )
                # Log error but don't fail readiness (migration path)
        except RuntimeError as e:
            checks["security_mode"]["status"] = "error"
            checks["security_mode"]["error"] = str(e)
            status_code = 503

        # Check database connection with timeout (Issue #2186)
        db_result = check_database_connection(timeout=2.0)
        checks["database"] = db_result
        if db_result.get("status") != "ok":
            status_code = 503

        # Check schema version (PostgreSQL production only)
        # Issue #2331: Use unified security mode
        from app.utils.security_mode import get_security_mode

        if is_postgresql() and get_security_mode().value == "production":
            try:
                db = Database()
                conn = db.get_connection()
                try:
                    current_revision = get_database_revision(conn)

                    checks["schema_version"]["current"] = current_revision
                    checks["schema_version"]["required"] = MIN_SUPPORTED_REVISION

                    if current_revision is None:
                        # Fresh database
                        checks["schema_version"]["status"] = "fresh"
                        checks["schema_version"]["compatible"] = True
                    else:
                        # Delegate to schema_guard's compatibility logic, which
                        # correctly treats timestamp revisions (e.g. 20260805_001)
                        # as >= the "baseline_*" starting point. A plain string
                        # comparison here ("2026..." < "baseline...") always
                        # reported incompatible and kept /readyz at 503 forever.
                        try:
                            check_schema_compatibility(conn)
                            checks["schema_version"]["status"] = "ok"
                            checks["schema_version"]["compatible"] = True
                        except SchemaCompatibilityError as exc:
                            checks["schema_version"]["status"] = "incompatible"
                            checks["schema_version"]["compatible"] = False
                            checks["schema_version"]["error"] = str(exc)
                            status_code = 503
                finally:
                    conn.close()  # Ensure connection is always closed

            except Exception as e:
                from app.utils.health_checks import _sanitize_error_message

                checks["schema_version"]["status"] = "error"
                checks["schema_version"]["error"] = _sanitize_error_message(e)
                status_code = 503
        else:
            # SQLite development mode - skip schema version check
            checks["schema_version"]["status"] = "skipped"
            checks["schema_version"]["compatible"] = True

        # Check config directory with timeout (Issue #2186)
        config_result = run_check_with_timeout(check_config_directory, timeout_seconds=1.0)
        checks["config_dir"] = config_result
        if config_result.get("status") not in ("ok", "skipped"):
            status_code = 503

        # Check workspace directory with timeout (Issue #2186)
        workspace_result = run_check_with_timeout(check_workspace_directory, timeout_seconds=1.0)
        checks["workspace_dir"] = workspace_result
        if workspace_result.get("status") not in ("ok", "skipped"):
            status_code = 503

        # Check encryption keys with timeout (Issue #2186)
        encryption_result = run_check_with_timeout(check_encryption_registry, timeout_seconds=2.0)
        checks["encryption_keys"] = encryption_result
        if encryption_result.get("status") == "error":
            status_code = 503

        # Check initialization status (Issue #2186)
        init_result = check_initialization_status()
        checks["init_status"] = init_result
        if init_result.get("status") != "ok":
            status_code = 503

        # Check for SSH sync failure (Issue #2328)
        # If secure SSH sync fails, the container should report not ready
        # to ensure operational visibility of the failure
        ssh_sync_result = check_ssh_sync_failure()
        checks["ssh_sync"] = ssh_sync_result
        if ssh_sync_result.get("status") != "ok":
            status_code = 503

        # Build response
        if status_code == 503:
            response = {
                "status": "not_ready",
                "checks": checks,
                "action": "Check logs for details. Run 'alembic upgrade head' if schema migration needed.",
            }
        else:
            response = {
                "status": "ready",
                "checks": checks,
            }

        return jsonify(response), status_code

    # Start background services (Issue #2187)
    # Web workers should NOT start schedulers - only scheduler worker should
    # Environment variable SCHEDULER_MODE controls behavior:
    # - "scheduler": Start all schedulers (scheduler worker)
    # - "web" or unset: Do NOT start schedulers (web worker)
    # Development mode (server.py) starts both web and scheduler
    scheduler_mode = os.environ.get("SCHEDULER_MODE", "web")
    if scheduler_mode == "scheduler":
        start_background_services()
        logger.info("Background services started (SCHEDULER_MODE=scheduler)")
    else:
        logger.info("Background services NOT started (SCHEDULER_MODE=%s)", scheduler_mode)

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
    from app.routes.feishu_config import feishu_config_bp
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
    app.register_blueprint(feishu_config_bp, url_prefix="/api")
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
