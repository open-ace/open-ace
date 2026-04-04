#!/usr/bin/env python3
"""Routes module for Open ACE application."""

from app.routes.admin import admin_bp
from app.routes.alerts import alerts_bp
from app.routes.analysis import analysis_bp
from app.routes.analytics import analytics_bp
from app.routes.auth import auth_bp
from app.routes.fetch import fetch_bp
from app.routes.fs import fs_bp
from app.routes.governance import governance_bp
from app.routes.messages import messages_bp
from app.routes.pages import pages_bp
from app.routes.projects import projects_bp
from app.routes.report import report_bp
from app.routes.tenant import tenant_bp
from app.routes.upload import upload_bp
from app.routes.usage import usage_bp
from app.routes.workspace import workspace_bp

__all__ = [
    "usage_bp",
    "messages_bp",
    "analysis_bp",
    "auth_bp",
    "admin_bp",
    "upload_bp",
    "pages_bp",
    "fetch_bp",
    "fs_bp",
    "report_bp",
    "governance_bp",
    "analytics_bp",
    "workspace_bp",
    "tenant_bp",
    "alerts_bp",
    "projects_bp",
]
