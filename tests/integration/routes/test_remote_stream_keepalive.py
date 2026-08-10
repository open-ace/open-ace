"""Tests for the remote SSE stream keepalive format.

Background (Issue #1511 / frontend PR ivycomputing/qwen-code-webui#196):
the SSE stream emits a keepalive while the session is idle. It used to be
an SSE comment line (``: keepalive``), which browsers silently drop and
never deliver to ``onmessage`` — so a frontend stall detector that resets
on every message never saw the heartbeat and falsely declared the
connection dead after its timeout. The keepalive must be a ``data:``
event so the browser fires ``onmessage``.
"""

import json
from unittest.mock import MagicMock, patch

from flask import g


def _auth_chain():
    """Patch the remote blueprint auth chain to a minimal authenticated user.

    The blueprint's ``before_request`` calls ``_set_user_from_token`` (imported
    into ``app.routes.remote``) which sets ``g.user``. We bypass token loading
    and inject a minimal admin user so ``_check_session_access`` passes.
    """

    def apply_user(_user):
        g.user = {"id": 1, "role": "system_admin"}
        g.user_id = 1
        g.user_role = "system_admin"

    return (
        patch("app.routes.remote._set_user_from_token", return_value=True),
        patch("app.routes.remote._set_user_from_webui_token", return_value=False),
        patch("app.modules.workspace.session_access._apply_user", side_effect=apply_user),
        patch("app.routes.remote._check_session_access", return_value=(None, None)),
    )


def _make_minimal_app():
    """Flask app with only the remote blueprint registered (no full create_app)."""
    from flask import Flask

    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _make_agent_mgr(loop_count):
    """Return a mock remote agent manager.

    Yields no buffered output (so the idle/keepalive branch is taken) and
    reports the session as ended after ``loop_count`` iterations, so the
    generator terminates.
    """
    mgr = MagicMock()
    mgr.get_last_delivered.return_value = 0
    mgr.get_buffered_output.return_value = []  # always idle → keepalive path
    mgr.is_session_ended.side_effect = [False] * loop_count + [True]
    return mgr


def _consume_stream(resp):
    """Read the full SSE response body and decode to text."""
    return b"".join(resp.iter_encoded()).decode("utf-8")


def _stream_body(loop_count=52):
    """Run the stream route with mocked deps and return the decoded SSE body."""
    from contextlib import ExitStack

    app = _make_minimal_app()
    agent_mgr = _make_agent_mgr(loop_count=loop_count)
    auth_patches = _auth_chain()

    with ExitStack() as stack:
        for p in auth_patches:
            stack.enter_context(p)
        stack.enter_context(
            patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr)
        )
        stack.enter_context(patch("app.routes.remote.time.sleep"))  # no real delays
        with app.test_client() as client:
            resp = client.get("/api/remote/sessions/test-sid/stream")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    return _consume_stream(resp)


class TestKeepaliveFormat:
    def test_keepalive_is_data_event_not_comment(self):
        """Idle keepalive must be a ``data:`` event, never an SSE comment line."""
        body = _stream_body()

        # The keepalive MUST be a data: event (fires onmessage) ...
        assert 'data: {"type": "keepalive"}' in body
        # ... and MUST NOT be a comment line (silently dropped by browsers).
        assert ": keepalive" not in body

    def test_keepalive_payload_is_valid_json(self):
        """The keepalive data payload parses as JSON with type == keepalive."""
        body = _stream_body()
        # Extract the first keepalive data line and validate it.
        keepalive_line = next(
            line for line in body.splitlines() if line.startswith("data:") and "keepalive" in line
        )
        payload = json.loads(keepalive_line[len("data:") :].strip())
        assert payload == {"type": "keepalive"}

    def test_stream_emits_connected_comment_header(self):
        """The leading ``: connected`` header (to flush response headers) is kept."""
        body = _stream_body()
        # The first chunk is the connected header — this is intentional and
        # must NOT be changed (only the periodic keepalive is a data event).
        assert body.startswith(": connected")
