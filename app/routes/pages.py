#!/usr/bin/env python3
"""
Open ACE - Pages Routes

Page routes for web UI. All pages are served by React SPA.
"""

import os

from flask import (
    Blueprint,
    make_response,
    request,
    send_from_directory,
)

from app.services.auth_service import AuthService

pages_bp = Blueprint("pages", __name__)
auth_service = AuthService()


def get_user_from_request():
    """Get user info from request."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if not token:
        return None, None

    session = auth_service.get_session(token)
    if not session:
        return None, None

    return session, token


def serve_react_app():
    """Serve the React SPA application."""
    dist_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "js", "dist"
    )
    index_path = os.path.join(dist_dir, "index.html")

    if os.path.exists(index_path):
        with open(index_path) as f:
            content = f.read()
        response = make_response(content)
        response.headers["Content-Type"] = "text/html"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response
    else:
        return "React app not built. Run 'cd frontend && npm run build'", 404


@pages_bp.route("/")
def index():
    """Serve the React SPA for the main page."""
    return serve_react_app()


@pages_bp.route("/login")
def login_page():
    """Serve the React SPA for the login page."""
    return serve_react_app()


@pages_bp.route("/logout")
def logout_page():
    """Logout and serve React SPA."""
    token = request.cookies.get("session_token")
    if token:
        auth_service.logout(token)

    response = serve_react_app()
    response.delete_cookie("session_token")
    return response


# Catch-all route for React SPA (must be registered last)
@pages_bp.route("/<path:path>")
def catch_all(path):
    """Serve React SPA for all other routes."""
    # Don't catch API routes or static files - return 404 to let Flask handle
    if path.startswith("api/") or path.startswith("static/"):
        from flask import abort

        abort(404)

    return serve_react_app()


@pages_bp.route("/static/claude-code-webui/<path:filename>")
def serve_static(filename):
    """Serve static files."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
    return send_from_directory(static_dir, filename)
