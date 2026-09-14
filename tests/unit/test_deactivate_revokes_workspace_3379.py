"""Issue #3379 (PR-A + review round 2): deactivation must stop the workspace
and close tokens.

The #3374 acceptance checklist requires "停用用户/撤销 token 后无法恢复继续
执行". Before PR-A, DELETE /admin/users/<id> revoked sessions but left the
user's WebUI instance running, its LLM proxy token live, and — because webui
tokens are stateless (signature + TTL) — every already-issued URL token kept
authenticating until its TTL lapsed (URL_TOKEN_ALLOWED_PATHS admits admin
routes).

Review round 2 additions pinned here:
- R-1: the teardown runs OUTSIDE the manager lock (concurrent token
  validations and /user-url hits must not freeze behind a ~90s destroy), and
  the admin routes schedule it asynchronously (audit records
  workspace_stop_scheduled, not a fictional completed stop).
- R-2/R-9: SSO denial is a real branch (403 / sso_error redirect) that
  audits the DENIED user_id; the mock-based tests use explicit user rows.
- R-3: is_active is parsed with an explicit parser — "false"/"0"/"False"
  deactivate, garbage 400s before any state change (PUT and restore share
  the parser).
- R-4: get_session_by_token refuses sessions of inactive/soft-deleted users
  (real SQLite); DELETE revokes AFTER the soft delete.
- R-5: users.tokens_valid_after — tokens minted before the stamp stay dead
  across reactivation and restore (v1 dies outright while stamped).
- R-6: single-user mode never stops the SHARED instance; the audit says so.
- R-7: an admin cannot deactivate themselves.
- R-8: sessions_revoked records the repository's per-table counts.
- R-10: an already-inactive account re-sent is_active=false still gets the
  revoke+stop remediation.
- R-11: routes peek at the manager singleton (never construct one) and
  stop_user_webui reports whether anything was stopped.
- R-12: validate_token_with_user performs ONE user lookup per request and
  the hot-path callers no longer issue a second get_user_by_id.
"""

import hashlib
import secrets as _secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import webui_manager as wm
from app.services.webui_manager import (
    WebUIInstance,
    WebUIManager,
    WorkspaceConfig,
    _tokens_valid_after_epoch,
)

pytestmark = [pytest.mark.issue(3379), pytest.mark.regression]


MOCK_ADMIN = {"id": 1, "username": "admin", "role": "admin", "tenant_id": 1}

SESSION_COUNTS = {"sessions": 2, "sso_sessions": 1, "web_user_auth_sessions": 0}


class _ManagerStub:
    """Records stop calls; stands in for the manager singleton.

    stop_user_webui mirrors the real semantics (R-11): True when a per-user
    instance was found, False otherwise; the single-user shared instance is
    never touched (R-6).
    """

    def __init__(self, multi_user=True):
        self.stopped = []
        self.config = WorkspaceConfig(enabled=True, multi_user_mode=multi_user)

    def stop_user_webui(self, user_id):
        self.stopped.append(user_id)
        return True


def _sync_spawn(monkeypatch):
    """Patch admin's greenlet seam to a synchronous recorder (R-1).

    Returns the list of spawned callables; each is executed manually so the
    ordering/identity assertions can observe what would have run on a
    greenlet without a hub (#2457 lesson).
    """
    import app.routes.admin as admin_mod

    spawned = []
    monkeypatch.setattr(admin_mod, "_spawn_background", lambda fn: spawned.append(fn))
    return spawned


def _peek_returns(monkeypatch, stub):
    monkeypatch.setattr(wm, "peek_webui_manager", lambda: stub)


# ── token validation: the named user must be active and not deleted ─────


def _manager_with_secret(secret="s" * 64):
    return WebUIManager(WorkspaceConfig(enabled=True, token_secret=secret))


def test_v2_token_rejected_when_user_deactivated(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": False})
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "deactivated" in err


def test_v2_token_rejected_when_user_soft_deleted(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "deleted_at": "2026-09-13"},
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "deleted" in err


def test_v2_token_rejected_when_user_missing(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: None)
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "not found" in err


def test_v2_token_rejected_when_lookup_fails(monkeypatch):
    """Fail closed: a token that cannot be tied to a live user does not pass."""
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)

    def _boom(uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(wm, "_webui_token_user", _boom)
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "unavailable" in err


def test_v1_token_rejected_when_user_deactivated(monkeypatch):
    manager = _manager_with_secret()
    # Mint a v1 token directly (legacy shape, no TTL).
    import hashlib

    random_part = "deadbeef"
    signature = hashlib.sha256(f"7:3100:{random_part}:{'s' * 64}".encode()).hexdigest()[:16]
    token = f"7:3100:{random_part}:{signature}"
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": False})
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "deactivated" in err


def test_active_user_token_still_validates(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": True})
    assert manager.validate_token(token) == (True, 7, None)


# ── R-5: tokens_valid_after — old tokens stay dead across reactivation ──


def test_v2_token_predating_stamp_rejected_even_after_reactivation(monkeypatch):
    """The acceptance gap R-5 closes: deactivate → reactivate used to
    resurrect every URL token leaked before the deactivation."""
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    # Reactivated (is_active back to True) but the stamp from the
    # deactivation is still on the row.
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {
            "id": uid,
            "is_active": True,
            "tokens_valid_after": datetime.now(timezone.utc) + timedelta(seconds=30),
        },
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "predates" in err


def test_v2_token_minted_after_stamp_passes(monkeypatch):
    manager = _manager_with_secret()
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {
            "id": uid,
            "is_active": True,
            "tokens_valid_after": datetime.now(timezone.utc) - timedelta(hours=1),
        },
    )
    token = manager.generate_token(7, 3100)  # minted after the stamp
    ok, user_id, err = manager.validate_token(token)
    assert ok is True and user_id == 7 and err is None


def test_v2_stamp_second_boundary_is_inclusive(monkeypatch):
    """Round 3 (R-5 gap 2): token timestamps are whole seconds while the
    stamp carries microseconds — a token minted in the SAME second as the
    stamp must pass. The old strict `<` rejected it, which an automated
    deactivate→reactivate→/user-url sequence (#3379 acceptance f-item) or an
    instance token minted in that second would hit; one second earlier
    stays dead."""
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    # Read the mint straight from the token (v2:{uid}:{port}:{timestamp}:…):
    # taking a separate int(time.time()) races the second boundary between
    # minting and reading, making the "same second" premise flaky (round 4).
    minted = int(token.split(":")[3])

    # Stamp WITHIN the mint's second (microsecond remainder): floor(stamp)
    # == minted → the token is NOT older than the stamp.
    same_second = datetime.fromtimestamp(minted + 0.95, tz=timezone.utc)
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "tokens_valid_after": same_second},
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is True and user_id == 7 and err is None

    # One second after the mint: still rejected (the stamp keeps doing its
    # job; only the sub-second boundary became inclusive).
    next_second = datetime.fromtimestamp(minted + 1.95, tz=timezone.utc)
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "tokens_valid_after": next_second},
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None and "predates" in err


def test_v2_stamp_as_sqlite_string_is_parsed(monkeypatch):
    """SQLite returns the column as a string — same comparison semantics."""
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "tokens_valid_after": future},
    )
    ok, _uid, err = manager.validate_token(token)
    assert ok is False and "predates" in err


def test_v1_token_rejected_while_stamp_set(monkeypatch):
    """v1 carries no mint time — a set stamp refuses it outright (R-5)."""
    manager = _manager_with_secret()
    random_part = "cafe1234"
    signature = hashlib.sha256(f"7:3100:{random_part}:{'s' * 64}".encode()).hexdigest()[:16]
    token = f"7:3100:{random_part}:{signature}"
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {
            "id": uid,
            "is_active": True,
            "tokens_valid_after": datetime.now(timezone.utc) - timedelta(hours=1),
        },
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "predates" in err


def test_v1_token_passes_when_stamp_unset(monkeypatch):
    manager = _manager_with_secret()
    random_part = "cafe1234"
    signature = hashlib.sha256(f"7:3100:{random_part}:{'s' * 64}".encode()).hexdigest()[:16]
    token = f"7:3100:{random_part}:{signature}"
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "tokens_valid_after": None},
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is True and user_id == 7


def test_unparseable_stamp_fails_closed(monkeypatch):
    assert _tokens_valid_after_epoch("not-a-date") == float("inf")
    assert _tokens_valid_after_epoch(None) is None
    assert _tokens_valid_after_epoch("") is None
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "tokens_valid_after": "garbage"},
    )
    ok, _uid, err = manager.validate_token(token)
    assert ok is False and "predates" in err


# ── R-12: one user lookup per validation, row handed to the caller ──────


def test_validate_token_with_user_uses_a_single_lookup(monkeypatch):
    calls = []

    def _counting_lookup(uid):
        calls.append(uid)
        return {"id": uid, "is_active": True, "role": "user", "must_change_password": False}

    monkeypatch.setattr(wm, "_webui_token_user", _counting_lookup)
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)

    ok, user_id, err, row = manager.validate_token_with_user(token)
    assert ok is True and user_id == 7 and err is None
    assert row["id"] == 7
    assert calls == [7]  # exactly ONE lookup served the status check AND the row

    # The legacy 3-tuple wrapper delegates — one MORE lookup for one MORE
    # validation, never a second lookup inside a single validation.
    assert manager.validate_token(token) == (True, 7, None)
    assert calls == [7, 7]


def test_fs_webui_user_uses_the_validated_row_without_a_second_query(app, monkeypatch):
    """R-12 caller check: fs.get_webui_user must not re-fetch the user."""
    from app.routes import fs as fs_mod

    manager = MagicMock()
    manager.validate_token_with_user.return_value = (
        True,
        7,
        None,
        {"id": 7, "username": "bob", "role": "user", "must_change_password": False},
    )
    monkeypatch.setattr(wm, "get_webui_manager", lambda: manager)
    monkeypatch.setattr(
        fs_mod.user_repo.__class__,
        "get_user_by_id",
        lambda self, uid: (_ for _ in ()).throw(AssertionError("second get_user_by_id")),
    )
    with app.test_request_context("/api/fs?token=v2:7:x"):
        user, err, status = fs_mod.get_webui_user()
    assert status == 200 and err is None
    assert user["id"] == 7 and user["username"] == "bob"


def test_session_access_webui_token_path_uses_the_validated_row(app, monkeypatch):
    """R-12 caller check: the remote-access loader must not re-fetch."""
    from app.modules.workspace import session_access as sa

    manager = MagicMock()
    manager.validate_token_with_user.return_value = (
        True,
        7,
        None,
        {"id": 7, "username": "bob", "role": "user", "must_change_password": False},
    )
    monkeypatch.setattr(wm, "get_webui_manager", lambda: manager)
    monkeypatch.setattr(
        "app.repositories.user_repo.UserRepository.get_user_by_id",
        lambda self, uid: (_ for _ in ()).throw(AssertionError("second get_user_by_id")),
    )
    with app.test_request_context("/remote?token=v2:7:x"):
        from flask import g

        assert sa._set_user_from_webui_token() is True
        assert g.user["id"] == 7


# ── R-1: the teardown must not pin the manager lock ─────────────────────


def test_stop_teardown_runs_outside_the_manager_lock(monkeypatch):
    """A ~90s sandbox destroy must not freeze other users' validations.

    Pure-threading timing assertion (#2457 lesson: no gevent hub). The
    manager's gevent RLock is swapped for a threading.RLock so cross-thread
    contention is real; if the teardown ever moves back under `with
    self._lock:` the two concurrent probes below block for the whole sleep.
    """
    manager = _manager_with_secret()
    manager._lock = threading.RLock()

    stopped_instance = WebUIInstance(user_id=7, system_account="u7", port=3100)
    live_instance = WebUIInstance(
        user_id=8,
        system_account="u8",
        port=3101,
        form=wm.WEBUI_FORM_SANDBOXED,
        token_secret="i" * 64,
        launcher=SimpleNamespace(health_check=lambda **kw: True),
    )
    manager._instances[7] = stopped_instance
    manager._instances[8] = live_instance

    def slow_teardown(instance):
        time.sleep(2.0)

    manager._finish_stop_instance = slow_teardown

    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": True})

    # A sandboxed-form token for user 8: verification walks
    # _find_sandboxed_instance -> `with self._lock:` — exactly the path the
    # old in-lock teardown used to block.
    payload = f"v2:8:3101:{int(time.time())}:{_secrets.token_hex(8)}"
    sig = hashlib.sha256(f"{payload}:{'i' * 64}".encode()).hexdigest()[:16]
    token = f"{payload}:{sig}"

    stopper = threading.Thread(target=lambda: manager.stop_user_webui(7))
    stopper.start()
    time.sleep(0.3)  # the stopper has popped user 7 and entered the teardown

    t0 = time.monotonic()
    ok, user_id, _err = manager.validate_token(token)
    instance = manager.get_user_instance(8)
    elapsed = time.monotonic() - t0

    assert ok is True and user_id == 8
    assert instance is live_instance
    assert elapsed < 1.0, f"validation/lookup blocked {elapsed:.2f}s behind the teardown"

    stopper.join(timeout=5)
    assert not stopper.is_alive()
    assert manager.get_user_instance(7) is None  # the pop itself did happen


def test_stop_user_webui_reports_whether_anything_was_stopped():
    manager = _manager_with_secret()
    assert manager.stop_user_webui(404) is False  # nothing registered

    instance = WebUIInstance(user_id=7, system_account="u7", port=3100)
    manager._instances[7] = instance
    teardowns = []
    manager._finish_stop_instance = teardowns.append
    assert manager.stop_user_webui(7) is True
    assert teardowns == [instance]
    assert manager.stop_user_webui(7) is False  # idempotent: already gone


def test_stop_user_webui_leaves_the_single_user_shared_instance_alone():
    """R-6: the shared instance serves every user — one user's deactivation
    must not stop it; stop_user_webui only answers for the per-user registry."""
    manager = _manager_with_secret()
    shared = WebUIInstance(user_id=1, system_account="shared", port=3100)
    manager._single_user_instance = shared
    teardowns = []
    manager._finish_stop_instance = teardowns.append

    assert manager.stop_user_webui(1) is False
    assert manager._single_user_instance is shared  # untouched
    assert teardowns == []


# ── deactivation routes: schedule the workspace stop, truthfully ────────


def _delete_user_env(monkeypatch, stub):
    _peek_returns(monkeypatch, stub)
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": 2,
        "is_active": True,
    }
    repo.delete_all_sessions_for_user.return_value = dict(SESSION_COUNTS)
    repo.set_tokens_valid_after.return_value = True
    repo.delete_user.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)
    return repo


def _put_user_env(monkeypatch, stub, *, old_active=True):
    _peek_returns(monkeypatch, stub)
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": 2,
        "role": "user",
        "is_active": old_active,
    }
    repo.update_user.return_value = True
    repo.delete_all_sessions_for_user.return_value = dict(SESSION_COUNTS)
    repo.set_tokens_valid_after.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)
    return repo


def _run_admin(client):
    """Execute the request; returns (response, audit_mock)."""

    def _inner(method, path, json=None):
        with patch("app.routes.admin.audit_logger") as audit:
            audit.log_action.return_value = True
            client.set_cookie("session_token", "t")
            with (
                patch(
                    "app.auth.decorators._authenticate",
                    # "user_id" is what _load_user_from_token reads into
                    # g.user_id — without it the actor is anonymous and the
                    # R-7 self-deactivation guard can never fire in tests.
                    return_value=(True, {**MOCK_ADMIN, "id": 1, "user_id": 1}),
                ),
                patch("app.routes.admin.same_tenant_user_required", lambda f: f),
            ):
                from flask import g

                with client.application.test_request_context("/"):
                    g.user_id = 1
                    if method == "DELETE":
                        resp = client.delete(path)
                    elif method == "POST":
                        resp = client.post(path, json=json)
                    else:
                        resp = client.put(path, json=json)
        return resp, audit

    return _inner


def test_delete_user_stops_webui_instance(app, client, monkeypatch):
    stub = _ManagerStub()
    repo = _delete_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert resp.status_code == 200
    assert repo.delete_all_sessions_for_user.called
    repo.set_tokens_valid_after.assert_called_once()  # R-5 stamp on soft delete
    # R-1: the teardown was SCHEDULED, not run inline…
    assert len(spawned) == 1
    assert stub.stopped == []
    # …and running the scheduled greenlet performs the stop.
    spawned[0]()
    assert stub.stopped == [7]
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stop_scheduled") is True
    assert details.get("tokens_invalidated") is True


def test_delete_user_proceeds_when_stop_fails(app, client, monkeypatch):
    """Fail-soft: a stop hiccup inside the greenlet must not surface in the
    response — the audit records the scheduling, the failure is logged."""

    class _Exploding(_ManagerStub):
        def stop_user_webui(self, user_id):
            raise RuntimeError("manager locked")

    stub = _Exploding()
    repo = _delete_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert resp.status_code == 200
    assert repo.delete_user.called
    spawned[0]()  # the exception is swallowed inside the greenlet body
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stop_scheduled") is True


def test_delete_user_skips_stop_when_singleton_replaced(app, client, monkeypatch):
    """R-1 identity re-check (the _reap_dead_sandboxed_async precedent): the
    scheduled greenlet re-resolves the singleton; a replaced manager holds no
    live instances, so the stale stop is a no-op."""
    stub = _ManagerStub()
    _delete_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, _audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert resp.status_code == 200
    replacement = _ManagerStub()
    _peek_returns(monkeypatch, replacement)  # singleton swapped in the meantime
    spawned[0]()
    assert stub.stopped == []
    assert replacement.stopped == []


def test_delete_user_does_not_construct_a_manager(app, client, monkeypatch):
    """R-11: peek semantics — with no singleton present the route must not
    construct one (a construction mints a token secret and starts the
    cleanup greenlet)."""
    constructions = []

    def _recording_ctor(*args, **kwargs):
        constructions.append((args, kwargs))
        return _ManagerStub()

    monkeypatch.setattr(wm, "WebUIManager", _recording_ctor)
    monkeypatch.setattr(wm, "_manager", None)  # no singleton
    repo = MagicMock()
    repo.get_user_by_id.return_value = {"id": 7, "username": "bob", "tenant_id": 2}
    repo.delete_all_sessions_for_user.return_value = dict(SESSION_COUNTS)
    repo.delete_user.return_value = True
    repo.set_tokens_valid_after.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)

    resp, audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert resp.status_code == 200
    assert constructions == []  # peeked, never constructed
    assert wm._manager is None
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stop_scheduled") is False


def test_delete_user_records_counts_and_order(app, client, monkeypatch):
    """R-4/R-8: soft delete FIRST, then session revocation, then the stop;
    the audit carries the real per-table counts."""
    order = []
    stub = _ManagerStub()
    repo = _delete_user_env(monkeypatch, stub)

    def _soft_delete(uid):
        order.append("soft_delete")
        return True

    def _revoke(uid):
        order.append("revoke_sessions")
        return dict(SESSION_COUNTS)

    def _stamp(uid, when=None):
        order.append("stamp_tokens")
        return True

    repo.delete_user.side_effect = _soft_delete
    repo.delete_all_sessions_for_user.side_effect = _revoke
    repo.set_tokens_valid_after.side_effect = _stamp
    spawned = _sync_spawn(monkeypatch)

    stub.stop_user_webui = lambda uid: order.append("stop") or stub.stopped.append(uid) or True

    resp, audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert resp.status_code == 200
    spawned[0]()
    assert order == ["soft_delete", "stamp_tokens", "revoke_sessions", "stop"]
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("sessions_revoked") == SESSION_COUNTS


def test_deactivate_via_update_stops_webui_and_revokes_sessions(app, client, monkeypatch):
    """PUT /admin/users/<id> with is_active=false takes the same path."""
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": False})
    assert resp.status_code == 200
    assert repo.delete_all_sessions_for_user.called
    assert repo.set_tokens_valid_after.called
    spawned[0]()
    assert stub.stopped == [7]
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("status_change") == {"from": True, "to": False}
    assert details.get("workspace_stop_scheduled") is True
    assert details.get("sessions_revoked") == SESSION_COUNTS  # R-8: counts
    assert details.get("tokens_invalidated") is True


def test_reactivate_via_update_does_not_stop(app, client, monkeypatch):
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub, old_active=False)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": True})
    assert resp.status_code == 200
    assert stub.stopped == []
    assert spawned == []  # reactivation schedules no teardown
    assert not repo.delete_all_sessions_for_user.called
    assert not repo.set_tokens_valid_after.called  # R-5: the stamp survives
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("status_change") == {"from": False, "to": True}
    assert "workspace_stop_scheduled" not in details


def test_deactivate_via_is_active_zero_takes_the_full_path(app, client, monkeypatch):
    """R-3: JSON 0 for the boolean must take the full deactivation path."""
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, _audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": 0})
    assert resp.status_code == 200
    assert repo.update_user.call_args.kwargs["is_active"] is False
    assert repo.delete_all_sessions_for_user.called
    spawned[0]()
    assert stub.stopped == [7]


@pytest.mark.parametrize("payload", ["false", "False", "0", " FALSE "])
def test_deactivate_via_is_active_string_forms_take_the_full_path(
    app, client, monkeypatch, payload
):
    """R-3: bool("false") is True — these used to silently ACTIVATE (or
    half-deactivate); the explicit parser maps them to a real False."""
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": payload})
    assert resp.status_code == 200
    assert repo.update_user.call_args.kwargs["is_active"] is False
    assert repo.delete_all_sessions_for_user.called
    spawned[0]()
    assert stub.stopped == [7]
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("status_change") == {"from": True, "to": False}


@pytest.mark.parametrize("payload", ["yes", 2, 1.5, ["false"], {"v": False}])
def test_is_active_garbage_is_a_400_before_any_state_change(app, client, monkeypatch, payload):
    """R-3: garbage 400s at the very top — no tenant counter churn, no
    update_user, no revoke/stop side effects."""
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub)
    _sync_spawn(monkeypatch)
    repo.update_user.reset_mock()

    resp, _audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": payload})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "is_active must be a boolean"
    repo.update_user.assert_not_called()
    repo.delete_all_sessions_for_user.assert_not_called()
    repo.set_tokens_valid_after.assert_not_called()
    assert stub.stopped == []


def test_restore_rejects_garbage_is_active(app, client, monkeypatch):
    """R-3: api_restore_user shares the parser — garbage 400s up front."""
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": None,
        "deleted_at": "2026-09-01 00:00:00",
    }
    repo.delete_all_sessions_for_user.return_value = dict(SESSION_COUNTS)
    repo.restore_user_with_update.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)

    resp_bad, _audit = _run_admin(client)(
        "POST", "/api/admin/users/7/restore", {"is_active": "maybe"}
    )
    assert resp_bad.status_code == 400
    assert resp_bad.get_json()["error"] == "is_active must be a boolean"
    repo.restore_user_with_update.assert_not_called()

    resp_ok, _audit = _run_admin(client)(
        "POST", "/api/admin/users/7/restore", {"is_active": "true"}
    )
    assert resp_ok.status_code == 200
    assert repo.restore_user_with_update.call_args.kwargs["is_active"] is True


def test_restore_does_not_clear_tokens_valid_after(app, client, monkeypatch):
    """R-5 residual semantics: a restored user's old tokens stay dead — the
    restore path never stamps or clears the column (row-level proof in the
    sqlite tests below)."""
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": None,
        "deleted_at": "2026-09-01 00:00:00",
    }
    repo.delete_all_sessions_for_user.return_value = dict(SESSION_COUNTS)
    repo.restore_user_with_update.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)
    _sync_spawn(monkeypatch)

    resp, _audit = _run_admin(client)("POST", "/api/admin/users/7/restore", {})
    assert resp.status_code == 200
    # The restore call must not carry any stamp-clearing instruction, and no
    # new stamp is written on the restore path itself.
    kwargs = repo.restore_user_with_update.call_args.kwargs
    assert "tokens_valid_after" not in kwargs
    repo.set_tokens_valid_after.assert_not_called()


def test_deactivate_via_update_proceeds_when_stop_fails(app, client, monkeypatch):
    class _Exploding(_ManagerStub):
        def stop_user_webui(self, user_id):
            raise RuntimeError("manager locked")

    stub = _Exploding()
    repo = _put_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": False})
    assert resp.status_code == 200
    assert repo.delete_all_sessions_for_user.called  # session revoke still ran
    spawned[0]()  # swallowed inside the greenlet body
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stop_scheduled") is True
    assert details.get("sessions_revoked") == SESSION_COUNTS


def test_delete_soft_deletes_before_revoking_and_stopping(app, client, monkeypatch):
    """R-4 ordering: soft_delete → revoke_sessions → stop (the token door is
    the user-status check, so deleted_at must land before anything slow)."""
    order = []
    stub = _ManagerStub()
    repo = _delete_user_env(monkeypatch, stub)

    def _soft_delete(uid):
        order.append("soft_delete")
        return True

    def _revoke(uid):
        order.append("revoke_sessions")
        return dict(SESSION_COUNTS)

    repo.delete_user.side_effect = _soft_delete
    repo.delete_all_sessions_for_user.side_effect = _revoke
    repo.set_tokens_valid_after.side_effect = lambda uid, when=None: True
    spawned = _sync_spawn(monkeypatch)

    def _stop(uid):
        order.append("stop")
        stub.stopped.append(uid)
        return True

    stub.stop_user_webui = _stop

    resp, _audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert resp.status_code == 200
    spawned[0]()
    assert order == ["soft_delete", "revoke_sessions", "stop"]


def test_cannot_deactivate_yourself(app, client, monkeypatch):
    """R-7: the deactivation revokes the actor's own session on the spot — a
    lone admin would be locked out of the admin surface entirely."""
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)
    repo.get_user_by_id.return_value = {**MOCK_ADMIN, "id": 1, "is_active": True}

    resp, _audit = _run_admin(client)("PUT", "/api/admin/users/1", {"is_active": False})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Cannot deactivate yourself"
    repo.update_user.assert_not_called()
    repo.delete_all_sessions_for_user.assert_not_called()
    repo.set_tokens_valid_after.assert_not_called()
    assert spawned == []
    assert stub.stopped == []


def test_already_inactive_reput_still_revokes_and_stops(app, client, monkeypatch):
    """R-10: idempotent remediation — an already-inactive account that is
    re-sent is_active=false still owes the revoke+stop; status_change stays
    silent (no real transition)."""
    stub = _ManagerStub()
    repo = _put_user_env(monkeypatch, stub, old_active=False)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": False})
    assert resp.status_code == 200
    assert repo.delete_all_sessions_for_user.called
    assert repo.set_tokens_valid_after.called  # the stamp REFRESHES (R-10)
    spawned[0]()
    assert stub.stopped == [7]
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert "status_change" not in details  # no real transition recorded


def test_single_user_mode_records_shared_instance_not_stopped(app, client, monkeypatch):
    """R-6: single-user deployments share ONE instance; a per-user stop is a
    no-op there and the audit must say so instead of implying a stop."""
    stub = _ManagerStub(multi_user=False)
    repo = _delete_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("DELETE", "/api/admin/users/7")
    assert repo.delete_user.called  # the delete itself still completed
    assert resp.status_code == 200
    assert spawned == []  # nothing scheduled — the shared instance stays
    assert stub.stopped == []
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stopped") == "shared_instance_not_stopped"
    assert "workspace_stop_scheduled" not in details


def test_single_user_mode_put_records_shared_instance_not_stopped(app, client, monkeypatch):
    stub = _ManagerStub(multi_user=False)
    repo = _put_user_env(monkeypatch, stub)
    spawned = _sync_spawn(monkeypatch)

    resp, audit = _run_admin(client)("PUT", "/api/admin/users/7", {"is_active": False})
    assert repo.update_user.call_args.kwargs["is_active"] is False
    assert resp.status_code == 200
    assert spawned == []
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stopped") == "shared_instance_not_stopped"


# ── R-2/R-9: SSO denial branch ──────────────────────────────────────────


class _Auth:
    class user:
        provider_user_id = "pid-1"
        email = None
        username = "idp-user"
        to_dict = staticmethod(lambda: {})

    token = type("T", (), {"access_token": "a", "refresh_token": "r", "expires_in": 3600})


def _sso_env(monkeypatch, *, row, identity_uid=7, create_uid=None):
    """Patch the SSO module for _finalize_sso_login with EXPLICIT user rows
    (R-9: the MagicMock trap — an auto-attr row reads as truthy-deleted, so
    the old test passed via the wrong branch).

    identity_uid: what get_user_by_sso_identity resolves (None exercises the
    fresh-provisioning path, where _create_user_from_sso is patched to
    return create_uid)."""
    import app.routes.sso as sso_mod

    manager = MagicMock()
    manager.get_user_by_sso_identity.return_value = identity_uid
    manager.get_provider.return_value = None
    created = []
    manager.create_sso_session.side_effect = lambda **kw: created.append(kw) or "tok"
    monkeypatch.setattr(sso_mod, "get_sso_manager", lambda: manager)
    if identity_uid is None:
        assert create_uid is not None
        monkeypatch.setattr(sso_mod, "_create_user_from_sso", lambda user, provider: create_uid)

    monkeypatch.setattr(
        sso_mod,
        "UserRepository",
        lambda: MagicMock(get_user_by_id=lambda uid: row(uid)),
    )
    monkeypatch.setattr(
        sso_mod.user_repo,
        "get_user_by_id",
        lambda uid: row(uid),
    )
    audits = []

    def _audit(*args, **kwargs):
        audits.append(kwargs)
        return None

    audit_logger = MagicMock()
    audit_logger.log.side_effect = _audit
    monkeypatch.setattr(sso_mod, "get_audit_logger", lambda: audit_logger)
    return manager, created, audits


def test_sso_login_refused_for_deactivated_user(monkeypatch):
    """The identity lookup only reads sso_identities — without the status
    check a deactivated user re-establishes a fresh session on the next IdP
    callback, defeating the revocation. The denial is a 403 (no frontend URL)
    that AUDITS the refused user_id (R-2)."""
    import app.routes.sso as sso_mod

    manager, created, audits = _sso_env(
        monkeypatch, row=lambda uid: {"id": uid, "is_active": False}
    )
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp, status = sso_mod._finalize_sso_login("github", _Auth(), None)
    assert status == 403
    assert resp.get_json()["error"] == "account_disabled"
    assert created == []  # no SSO and no local session was issued
    manager.link_identity.assert_not_called()  # R-2: no binding onto a dead account
    denial = [a for a in audits if a.get("details", {}).get("denied_reason")]
    assert denial and denial[0]["user_id"] == 7  # the DENIED user is audited
    assert denial[0]["details"]["denied_reason"] == "account_disabled"
    assert denial[0].get("success") is False


def test_sso_login_refused_for_soft_deleted_user(monkeypatch):
    import app.routes.sso as sso_mod

    manager, created, audits = _sso_env(
        monkeypatch,
        row=lambda uid: {"id": uid, "is_active": True, "deleted_at": "2026-09-01"},
    )
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        _resp, status = sso_mod._finalize_sso_login("github", _Auth(), None)
    assert status == 403
    assert created == []
    manager.link_identity.assert_not_called()
    assert any(a.get("details", {}).get("denied_reason") == "account_disabled" for a in audits)


def test_sso_login_refused_for_missing_user_row(monkeypatch):
    """R-2 fail-closed: a vanished row denies (the old `or {}` passed it)."""
    import app.routes.sso as sso_mod

    manager, created, _audits = _sso_env(monkeypatch, row=lambda uid: None)
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        _resp, status = sso_mod._finalize_sso_login("github", _Auth(), None)
    assert status == 403
    assert created == []
    manager.link_identity.assert_not_called()


def test_sso_denial_redirects_to_frontend_with_error(monkeypatch):
    """R-2: browser flows land on the frontend with sso_error instead of a
    bare 200 success:true."""
    import app.routes.sso as sso_mod

    _manager, created, _audits = _sso_env(
        monkeypatch, row=lambda uid: {"id": uid, "is_active": False}
    )
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp = sso_mod._finalize_sso_login("github", _Auth(), "http://localhost:3000")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "http://localhost:3000?sso_error=account_disabled"
    assert created == []


def test_sso_email_linking_denied_before_binding(monkeypatch):
    """Round 3 (R-2): with email linking enabled, an IdP-asserted email that
    matches a DEACTIVATED account must be refused BEFORE link_identity binds
    the identity — the round-2 placement linked first, and the binding would
    come alive the moment an administrator re-enables the account.
    identity_uid=None forces the fresh-resolution path where the link is."""
    import app.routes.sso as sso_mod

    manager, created, audits = _sso_env(
        monkeypatch,
        row=lambda uid: {"id": uid, "is_active": False},
        identity_uid=None,
        create_uid=99,  # never reached: the email match denies first
    )
    monkeypatch.setattr(sso_mod, "_allow_email_linking", lambda provider: True)
    monkeypatch.setattr(
        sso_mod.user_repo,
        "get_user_by_email",
        lambda email, include_deleted=False: {"id": 42},
    )

    class _AuthColliding(_Auth):
        class user:
            provider_user_id = "idp-attacker-1"
            email = "admin@example.com"
            username = "idp-user"
            to_dict = staticmethod(lambda: {})

        token = type("T", (), {"access_token": "a", "refresh_token": "r", "expires_in": 3600})

    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp, status = sso_mod._finalize_sso_login("corp", _AuthColliding(), None)
    assert status == 403
    assert resp.get_json()["error"] == "account_disabled"
    # The binding must NOT have happened (the round-3 finding).
    manager.link_identity.assert_not_called()
    assert created == []
    denial = [a for a in audits if a.get("details", {}).get("denied_reason")]
    assert denial and denial[0]["user_id"] == 42
    # Round 4: a denial never binds — email_linked records the ACTUAL outcome
    # (success-path convention); the match itself is flagged separately.
    assert denial[0]["details"]["email_linked"] is False
    assert denial[0]["details"]["email_match_refused"] is True


def test_sso_active_control_case_issues_sessions_and_links(monkeypatch):
    """R-9 control: an ACTIVE user must pass the check, get linked, and
    receive the sessions — deleting the is_active condition in sso.py must
    fail this test (fresh-provisioning path, where the link happens)."""
    import app.routes.sso as sso_mod

    manager, created, audits = _sso_env(
        monkeypatch,
        row=lambda uid: {"id": uid, "is_active": True},
        identity_uid=None,
        create_uid=7,
    )
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        resp = sso_mod._finalize_sso_login("github", _Auth(), None)
    assert resp.status_code == 200
    assert created, "active user must receive SSO + local sessions"
    manager.link_identity.assert_called_once()
    assert not any(a.get("details", {}).get("denied_reason") for a in audits)


# ── R-4/R-5: real SQLite — the session row and the stamp column ─────────


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    """Isolated SQLite database loaded from the authoritative snapshot
    (the test_feishu_org_sync pattern)."""
    from app.repositories.database import Database
    from app.repositories.schema_init import load_schema_from_file
    from app.repositories.user_repo import UserRepository

    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)
    db = Database(db_url=f"sqlite:///{tmp_path / 'deactivate-3379.db'}")
    load_schema_from_file(db_url=db.db_url, dialect="sqlite")
    repo = UserRepository(db=db)

    db.execute(
        "INSERT INTO users (username, password_hash, email, role, is_active) "
        "VALUES ('alice', 'x', 'alice@example.com', 'user', 1)"
    )
    user = repo.get_user_by_username("alice")
    repo.create_session(
        user["id"],
        "tok-alice",
        datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    return repo, user["id"]


def test_session_of_active_user_validates(user_db):
    repo, uid = user_db
    session = repo.get_session_by_token("tok-alice")
    assert session is not None and session["user_id"] == uid


def test_session_refused_after_deactivation(user_db):
    """R-4 root cause: org-sync deactivation and every login race leave rows
    in sessions — the JOIN must refuse them, not resurrect them."""
    repo, uid = user_db
    assert repo.update_user(uid, is_active=False) is True
    assert repo.get_session_by_token("tok-alice") is None


def test_session_revived_only_by_real_reactivation(user_db):
    repo, uid = user_db
    repo.update_user(uid, is_active=False)
    assert repo.get_session_by_token("tok-alice") is None
    assert repo.update_user(uid, is_active=True) is True
    assert repo.get_session_by_token("tok-alice") is not None


def test_session_refused_after_soft_delete(user_db):
    repo, uid = user_db
    assert repo.delete_user(uid) is True  # soft delete
    assert repo.get_session_by_token("tok-alice") is None


def test_validate_session_refuses_deactivated_user(user_db):
    """The service funnel (/api/auth/me and every before_request) inherits the
    refusal through the same query."""
    from app.services.auth_service import AuthService

    repo, uid = user_db
    service = AuthService(user_repo=repo)
    ok, session = service.validate_session("tok-alice")
    assert ok is True

    repo.update_user(uid, is_active=False)
    ok, session = service.validate_session("tok-alice")
    assert ok is False
    assert session == {"error": "Invalid or expired session"}


def test_tokens_valid_after_stamped_and_refreshed(user_db):
    """R-5: the stamp is written (and refreshed on idempotent re-sends)."""
    repo, uid = user_db
    repo.set_tokens_valid_after(uid, when=datetime(2026, 9, 11, 0, 0, 0))
    row = repo.get_user_by_id(uid)
    assert row["tokens_valid_after"] == "2026-09-11 00:00:00"

    time.sleep(1.1)  # second-resolution timestamps: force a later value
    assert repo.set_tokens_valid_after(uid) is True  # default = now(UTC)
    assert repo.get_user_by_id(uid)["tokens_valid_after"] > "2026-09-11 00:00:00"


def test_update_user_deactivation_stamps_tokens_valid_after(user_db):
    """Round 3 (R-5 gap 1): org-sync paths call update_user(is_active=False)
    directly — the stamp sinks into the same UPDATE, so an org-sync
    deactivation kills the user's pre-deactivation URL tokens without any
    admin PUT. Reactivation still does not clear it."""
    repo, uid = user_db
    assert repo.get_user_by_id(uid)["tokens_valid_after"] is None
    assert repo.update_user(uid, is_active=False) is True
    stamped = repo.get_user_by_id(uid)["tokens_valid_after"]
    assert stamped is not None
    assert repo.update_user(uid, is_active=True) is True
    assert repo.get_user_by_id(uid)["tokens_valid_after"] == stamped


def test_tokens_valid_after_survives_reactivation_and_restore(user_db):
    """R-5 core semantics: neither reactivation nor restore clears the stamp —
    old leaked tokens stay dead; new ones are minted after it."""
    repo, uid = user_db
    repo.set_tokens_valid_after(uid)
    assert repo.update_user(uid, is_active=False) is True
    # Reactivation (PUT is_active=true) must not clear it.
    assert repo.update_user(uid, is_active=True) is True
    assert repo.get_user_by_id(uid)["tokens_valid_after"] is not None

    # Soft delete then restore must not clear it either.
    assert repo.delete_user(uid) is True
    assert repo.restore_user_with_update(uid, is_active=True) is True
    assert repo.get_user_by_id(uid)["tokens_valid_after"] is not None
    assert repo.get_user_by_id(uid)["deleted_at"] is None
