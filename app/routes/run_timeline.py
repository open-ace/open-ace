"""
Open ACE - Run Timeline API Routes.

Exposes the persisted remote-session run/event timeline as a read API under
the shared ``/api/remote`` namespace. Lives in its own blueprint so the whole
feature can be removed with one registration line.

Auth: reuses ``load_remote_user`` (the same session-token / webui-token loading
the remote blueprint uses), then the shared session-access owner/admin check.
The local ``_check_session_access`` wrapper delegates to it; the
leading-underscore name is what the API security scanner recognises as an
ownership check (SEC002). When ``run_timeline.enabled`` is false the blueprint
returns ``{success: False, disabled: True}`` (200) so the frontend can hide
itself.
"""

from __future__ import annotations


import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

from app.modules.workspace.session_access import load_remote_user

logger = logging.getLogger(__name__)

run_timeline_bp = Blueprint("run_timeline", __name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000


@run_timeline_bp.before_request
def _guard():
    """Feature flag + authentication gate for every timeline request."""
    from app.utils.config import is_run_timeline_enabled

    if not is_run_timeline_enabled():
        return jsonify({"success": False, "disabled": True}), 200

    return load_remote_user()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _check_session_access(session_id: str):
    """Owner/admin authz wrapper (delegates to the shared helper).

    Defined locally so the API security scanner recognises the ownership check
    by its leading-underscore name (see scripts/lint/api_security_scanner.py).
    """
    from app.modules.workspace.session_access import check_session_access

    return check_session_access(session_id)


@run_timeline_bp.route("/sessions/<session_id>/events", methods=["GET"])
def get_session_events(session_id: str):
    """Return the persisted run timeline for a remote session.

    Query params: ``limit`` (default 50, max 1000), ``offset``, ``after`` (event
    id cursor for live streaming), ``event_type`` filter, ``order`` (asc|desc),
    ``since`` / ``until`` (ISO timestamps). Events are ordered by the
    autoincrement ``id`` for stable chronological ordering.
    """
    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    from app.repositories.run_timeline_repo import RunTimelineRepository

    repo = RunTimelineRepository()

    limit = min(request.args.get("limit", default=_DEFAULT_LIMIT, type=int), _MAX_LIMIT)
    limit = max(limit, 1)
    offset = max(request.args.get("offset", default=0, type=int), 0)
    after_id = request.args.get("after", type=int)
    event_type = request.args.get("event_type") or None
    order = request.args.get("order", default="asc")
    since = _parse_ts(request.args.get("since"))
    until = _parse_ts(request.args.get("until"))

    events = repo.query_events(
        session_id,
        limit=limit,
        offset=offset,
        after_id=after_id,
        event_type=event_type,
        order=order,
        since=since,
        until=until,
    )
    total = repo.count_events(session_id, event_type=event_type, since=since, until=until)
    run = repo.get_run_by_session(session_id)

    return jsonify(
        {
            "success": True,
            "run": run.to_dict() if run else None,
            "events": [e.to_dict() for e in events],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@run_timeline_bp.route("/sessions/<session_id>/approvals", methods=["GET"])
def get_session_approvals(session_id: str):
    """Return the durable approval records for a remote session."""
    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    from app.repositories.run_timeline_repo import RunTimelineRepository

    repo = RunTimelineRepository()
    approvals = repo.list_approvals(session_id)
    return jsonify(
        {
            "success": True,
            "approvals": [a.to_dict() for a in approvals],
            "total": len(approvals),
        }
    )
