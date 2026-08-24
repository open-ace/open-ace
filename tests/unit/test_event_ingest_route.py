"""#3046: web-side SSE ingest route (scheduler → web handover).

Pins the security model (shared secret as primary control, fail-closed 503
when unconfigured; loopback/trusted-source check as defense-in-depth) and the
functional contract (re-broadcast through the web emitter, activity replay
window, size/count caps).
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous import events_ingest
from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter

pytestmark = [pytest.mark.issue(3046)]

SECRET = "test-ingest-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ingest.db'}")
    from app import create_app

    app = create_app({"TESTING": True})

    # Deterministic secret resolution: dedicated key empty, no env var, no
    # root-level config secret — each test opts into what it needs.
    monkeypatch.setattr(
        events_ingest, "get_config_value", lambda section, key, default=None: default
    )
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr("scripts.shared.config._load_user_config", lambda: {})
    events_ingest._parse_trusted_sources.cache_clear()
    # The blueprint's before_request gate reads the REAL config on this
    # machine; force it so the route tests are hermetic everywhere.
    monkeypatch.setattr("app.utils.config.is_autonomous_enabled", lambda: True)

    # Fresh emitter singleton so subscriber state never leaks across tests.
    AutonomousEventEmitter._instance = None

    with app.test_client() as c:
        yield c

    AutonomousEventEmitter._instance = None


def _post(client, events, *, secret=SECRET, addr="127.0.0.1"):
    return client.post(
        "/api/autonomous/internal/events/ingest",
        json={"events": events},
        headers={"X-OpenACE-Events-Key": secret} if secret is not None else {},
        environ_base={"REMOTE_ADDR": addr},
    )


def _event(**overrides):
    payload = {"workflow_id": "wf-1", "event_type": "status_change", "data": {"status": "planning"}}
    payload.update(overrides)
    return payload


def test_no_secret_configured_fails_closed(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "")
    monkeypatch.setattr("scripts.shared.config._load_user_config", lambda: {})
    response = _post(client, [_event()], secret="")
    assert response.status_code == 503


def test_wrong_secret_rejected(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    assert _post(client, [_event()], secret="wrong").status_code == 403


def test_missing_secret_header_rejected(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    assert _post(client, [_event()], secret=None).status_code == 403


def test_non_loopback_rejected_without_trusted_sources(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    assert _post(client, [_event()], addr="192.168.1.50").status_code == 403


def test_valid_loopback_secret_broadcasts(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    emitter = AutonomousEventEmitter.instance()
    q = emitter.subscribe("wf-1")
    response = _post(client, [_event()])
    assert response.status_code == 200
    assert response.get_json()["accepted"] == 1
    received = q.get(timeout=2)
    assert received["event_type"] == "status_change"
    assert received["data"]["status"] == "planning"


def test_agent_activity_ingested_into_replay_window(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    emitter = AutonomousEventEmitter.instance()
    assert (
        _post(client, [_event(event_type="agent_activity", data={"text": "tool"})]).status_code
        == 200
    )
    # Subscribe AFTER the ingest: the replay window must serve the activity.
    q = emitter.subscribe("wf-1")
    replayed = q.get(timeout=2)
    assert replayed["event_type"] == "agent_activity"


def test_trusted_sources_with_malformed_entry(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)

    def _cfg(section, key, default=None):
        if key == "events_ingest_trusted_sources":
            return ["10.1.0.0/16", "not-an-ip"]
        return default

    monkeypatch.setattr(events_ingest, "get_config_value", _cfg)
    events_ingest._parse_trusted_sources.cache_clear()
    assert _post(client, [_event()], addr="10.1.2.3").status_code == 200
    assert _post(client, [_event()], addr="10.2.0.1").status_code == 403


def test_body_over_limit_rejected(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    pad = "x" * (1024 * 1024 + 512)
    response = _post(client, [_event(data={"status": pad})])
    assert response.status_code == 413


def test_event_count_over_limit_rejected(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    response = _post(client, [_event(workflow_id=f"wf-{i}") for i in range(101)])
    assert response.status_code == 413


def test_malformed_events_rejected(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    assert _post(client, []).status_code == 400
    assert _post(client, "not-a-list").status_code == 400


def test_empty_body_rejected(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    response = client.post(
        "/api/autonomous/internal/events/ingest",
        headers={"X-OpenACE-Events-Key": SECRET},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 400


def test_ipv6_loopback_forms_accepted(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    for addr in ("::1", "::ffff:127.0.0.1"):
        assert _post(client, [_event()], addr=addr).status_code == 200, addr


def test_secret_priority_dedicated_key_wins(monkeypatch):
    monkeypatch.setattr(
        events_ingest,
        "get_config_value",
        lambda section, key, default=None: "k" * 40 if key == "events_ingest_key" else default,
    )
    monkeypatch.setenv("SECRET_KEY", "env-secret-should-lose")
    assert events_ingest.resolve_ingest_secret() == "k" * 40


def test_secret_priority_weak_dedicated_key_falls_through(monkeypatch):
    monkeypatch.setattr(
        events_ingest,
        "get_config_value",
        lambda section, key, default=None: "short" if key == "events_ingest_key" else default,
    )
    monkeypatch.setenv("SECRET_KEY", "env-secret-should-win")
    assert events_ingest.resolve_ingest_secret() == "env-secret-should-win"


def test_secret_priority_root_config_fallback(monkeypatch):
    monkeypatch.setattr(
        events_ingest, "get_config_value", lambda section, key, default=None: default
    )
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr("scripts.shared.config._load_user_config", lambda: {"secret_key": "root"})
    assert events_ingest.resolve_ingest_secret() == "root"


def test_ingest_url_explicit_override(monkeypatch):
    monkeypatch.setattr(
        events_ingest,
        "get_config_value",
        lambda section, key, default=None: (
            "http://web:8000/ingest" if key == "events_ingest_url" else default
        ),
    )
    assert events_ingest.resolve_ingest_url() == "http://web:8000/ingest"


def test_ingest_url_default_uses_configured_web_port(monkeypatch):
    monkeypatch.setattr(
        events_ingest,
        "get_config_value",
        lambda section, key, default=None: 5000 if key == "web_port" else default,
    )
    monkeypatch.setattr("scripts.shared.config._get_web_port", lambda: 5000, raising=False)
    # The default URL must carry the ingest PATH — a bare origin POSTs to "/"
    # and hits the GET-only SPA catch-all (405), as the first production
    # deploy of #3047 demonstrated.
    assert (
        events_ingest.resolve_ingest_url()
        == "http://127.0.0.1:5000/api/autonomous/internal/events/ingest"
    )


def test_route_is_marked_public_endpoint(client):
    # The SEC001 API security scanner allows unauthenticated routes only when
    # explicitly marked; the ingest route is process-to-process, not user-facing.
    view = client.application.view_functions["autonomous.ingest_internal_events"]
    assert getattr(view, "_is_public_endpoint", False) is True
