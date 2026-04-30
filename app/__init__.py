#!/usr/bin/env python3
"""
Open ACE - Flask Application Factory

This module provides the Flask application factory for the Open ACE platform.
"""

import logging
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def create_app(config=None):
    """
    Flask application factory.

    Args:
        config: Optional configuration dictionary or object.

    Returns:
        Flask application instance.
    """
    app = Flask(__name__, static_folder="../static", template_folder="../templates")

    # Load configuration
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    # SECRET_KEY configuration with security checks
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        flask_env = os.environ.get("FLASK_ENV", "development")
        if flask_env == "production":
            raise RuntimeError("SECRET_KEY environment variable must be set in production!")
        secret_key = "dev-secret-key"
        logger.warning("Using development SECRET_KEY - DO NOT use in production!")
    app.config["SECRET_KEY"] = secret_key

    if config:
        if isinstance(config, dict):
            app.config.update(config)
        else:
            app.config.from_object(config)

    # Register error handlers
    register_error_handlers(app)

    # Register blueprints
    register_blueprints(app)

    # Ensure all tables exist (runs DDL once at startup, not per-request)
    from app.repositories.schema_init import ensure_all_tables

    ensure_all_tables()

    # Health check endpoint
    @app.route("/health")
    def health_check():
        """Health check endpoint for Docker and load balancers."""
        from app.utils.version import get_git_commit

        return jsonify(
            {
                "status": "healthy",
                "service": "open-ace",
                "version": get_git_commit(),
            }
        )

    # Start background services
    start_background_services()

    logger.info("Open ACE application initialized")
    return app


def register_error_handlers(app):
    """Register error handlers for the application."""

    @app.after_request
    def add_cors_headers(response):
        """Add CORS headers for iframe integration with qwen-code-webui."""
        # Allow requests from any origin for API routes (needed for iframe integration)
        if request.path.startswith("/api/"):
            origin = request.headers.get("Origin", "")
            # In multi-user mode, webui instances run on different ports
            # Allow localhost, 127.0.0.1, and workspace URL origins (any IP on port range)
            if origin:
                # Parse origin to check if it's from a valid webui port
                from urllib.parse import urlparse

                try:
                    parsed = urlparse(origin)
                    # Allow if:
                    # 1. localhost or 127.0.0.1
                    # 2. Same host as server but different port (workspace webui instances)
                    if (
                        parsed.hostname in ("localhost", "127.0.0.1")
                        or parsed.port
                        and 3100 <= parsed.port <= 3200
                    ):
                        response.headers["Access-Control-Allow-Origin"] = origin
                        response.headers["Access-Control-Allow-Methods"] = (
                            "GET, POST, PUT, DELETE, OPTIONS"
                        )
                        response.headers["Access-Control-Allow-Headers"] = (
                            "Content-Type, Authorization"
                        )
                        response.headers["Access-Control-Allow-Credentials"] = "true"
                except Exception:
                    pass
        return response

    # Handle OPTIONS preflight requests for CORS
    @app.route("/api/<path:path>", methods=["OPTIONS"])
    def handle_options(path):
        """Handle CORS preflight requests."""
        origin = request.headers.get("Origin", "")
        if origin:
            from urllib.parse import urlparse

            try:
                parsed = urlparse(origin)
                # Allow localhost, 127.0.0.1, or any origin from workspace port range
                if parsed.hostname in ("localhost", "127.0.0.1") or (
                    parsed.port and 3100 <= parsed.port <= 3200
                ):
                    response = jsonify({"status": "ok"})
                    response.headers["Access-Control-Allow-Origin"] = origin
                    response.headers["Access-Control-Allow-Methods"] = (
                        "GET, POST, PUT, DELETE, OPTIONS"
                    )
                    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
                    response.headers["Access-Control-Allow-Credentials"] = "true"
                    return response
            except:
                pass
        return jsonify({"status": "ok"}), 200

    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        """Handle all HTTP exceptions and return JSON for API routes."""
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return e.get_response()

    @app.errorhandler(Exception)
    def handle_generic_exception(e):
        """Handle unexpected exceptions."""
        logger.exception("Unexpected error occurred")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        raise e


def register_blueprints(app):
    """Register all application blueprints."""
    from app.routes.admin import admin_bp
    from app.routes.alerts import alerts_bp
    from app.routes.analysis import analysis_bp
    from app.routes.analytics import analytics_bp
    from app.routes.auth import auth_bp
    from app.routes.compliance import compliance_bp
    from app.routes.fetch import fetch_bp
    from app.routes.fs import fs_bp
    from app.routes.governance import governance_bp
    from app.routes.insights import insights_bp
    from app.routes.messages import messages_bp
    from app.routes.pages import pages_bp
    from app.routes.projects import projects_bp
    from app.routes.quota import quota_bp
    from app.routes.remote import remote_bp
    from app.routes.report import report_bp
    from app.routes.roi import roi_bp
    from app.routes.sso import sso_bp
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
    app.register_blueprint(tool_accounts_bp, url_prefix="/api")
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.register_blueprint(insights_bp, url_prefix="/api")
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
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

    logger.info("Background services started")
