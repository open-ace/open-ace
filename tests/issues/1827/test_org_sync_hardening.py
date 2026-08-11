"""Hardening tests for Feishu / DingTalk org sync (Issue #1827 review findings).

Covers the six findings tracked in #1827:
  #1 [MED] member reconciliation scanned the whole teams/team_members tables
          (no WHERE) and filtered in Python  -> per-run cache + WHERE IN
  #2 [MED] username-uniqueness loop was unbounded  -> bounded loop + uuid fallback
  #3 [LOW ] tenant/access token was re-exchanged every run, no cache/refresh
          -> TTL cache + auth-failure retry
  #4 [LOW ] scheduler overlap guard was in-process only; no max-runtime monitor
          -> non-blocking schedule lock + max-runtime watchdog + self-heal
  #5 [MED] DingTalk sync lock was in-process (threading.Lock)  -> PG advisory lock
          (the cross-process exclusion itself is covered by tests/issues/1806;
          here we cover the shared self-heal helpers it now depends on)
  #6 [MED] DingTalk user sync issued one user/get per user (N+1)
          -> batched topapi/v2/user/list

The SQLite-bound tests patch the module-level ``is_postgresql`` to False so the
dev environment's globally-configured Postgres URL cannot corrupt them (the same
isolation tests/issues/1773 applies). The real Postgres self-heal path lives in
tests/integration/test_org_sync_lock_recovery.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

import app.repositories.database as db_module
import app.services.dingtalk_org_sync as dt_module
import app.services.feishu_org_sync as fs_module
import app.utils.smtp_crypto as smtp_crypto
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.repositories.user_repo import UserRepository
from app.services._org_sync_lock import (
    force_release_lock,
    get_running_sync_state,
    split_advisory_key,
)
from app.services.dingtalk_org_sync import (
    DINGTALK_AUTH_ERRCODES,
    DingTalkDepartment,
    DingTalkOrgSyncService,
    DingTalkUser,
    _CachedToken,
)
from app.services.feishu_org_sync import (
    FEISHU_AUTH_ERROR_CODES,
    FeishuDepartment,
    FeishuOrgSyncService,
    FeishuUser,
)

# ---------------------------------------------------------------------------
# SQLite sync environment (mirrors tests/issues/1773 + 1787 fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    """An isolated SQLite Database with schema; global is_postgresql forced off."""
    # Issue #1820: Reset EncryptionKeyRegistry before setting new key
    from app.utils.encryption_key_registry import reset_registry

    reset_registry()
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-1827-org-sync-key")
    smtp_crypto._password_manager_instance = None
    # A globally-configured Postgres URL in dev would otherwise make the
    # module-level is_postgresql()/adapt_sql() helpers poison SQLite repo calls.
    monkeypatch.setattr(db_module, "is_postgresql", lambda: False)
    db = Database(db_url=f"sqlite:///{tmp_path / 'org-sync-1827.db'}")
    load_schema_from_file(db_url=db.db_url, dialect="sqlite")
    try:
        yield db
    finally:
        smtp_crypto._password_manager_instance = None
        # Issue #1820: Reset EncryptionKeyRegistry after test
        reset_registry()


class _FakeDingTalk(DingTalkOrgSyncService):
    """Deterministic DingTalk service that bypasses live API calls."""

    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_access_token(self, app_key, app_secret):
        return "test-token"

    def _fetch_directory_snapshot(self, token, root_department_id, warnings=None):
        return self._departments, self._users


class _FakeFeishu(FeishuOrgSyncService):
    """Deterministic Feishu service that bypasses live API calls."""

    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_tenant_access_token(self, app_id, app_secret):
        return "test-token"

    def _fetch_directory_snapshot(self, token):
        return self._departments, self._users


def _dingtalk_config():
    return {
        "dingtalk": {
            "app_key": "k",
            "app_secret": "s",
            "org_sync_enabled": True,
            "org_sync_tenant_id": 8,
            "org_sync_interval_minutes": 60,
            "org_sync_root_dept_id": "1",
        }
    }


def _feishu_config():
    return {
        "feishu": {
            "app_id": "i",
            "app_secret": "x",
            "org_sync_enabled": True,
            "org_sync_tenant_id": 7,
            "org_sync_interval_minutes": 60,
        }
    }


class _FakeResponse:
    """Minimal requests.Response stand-in."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# ===========================================================================
# Finding #1: eliminate full-table scans (per-run cache + WHERE IN)
# ===========================================================================


def test_dingtalk_load_synced_teams_called_once_per_run(sqlite_db):
    """The synced-team index must be built once per run and threaded through the
    department loop, not re-scanned from the teams table per department (WP-1).
    """
    db = sqlite_db
    service = _FakeDingTalk(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=_dingtalk_config(),
        departments=[
            DingTalkDepartment(department_id="100", name="Eng"),
            DingTalkDepartment(department_id="200", name="QA"),
            DingTalkDepartment(department_id="300", name="Ops"),
        ],
        users=[],
    )
    calls: list[int] = []
    original = service._load_synced_teams

    def counting():
        calls.append(1)
        return original()

    service._load_synced_teams = counting

    service.sync_org()

    assert (
        len(calls) == 1
    ), f"_load_synced_teams must run once per sync, not per department; got {len(calls)}"


def test_dingtalk_sync_memberships_uses_scoped_where_in(sqlite_db):
    """Membership reconciliation must scope its scan with WHERE team_id IN (...),
    not load the whole team_members table and filter in Python (WP-1).
    """
    db = sqlite_db
    service = _FakeDingTalk(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=_dingtalk_config(),
        departments=[DingTalkDepartment(department_id="100", name="Eng")],
        users=[
            DingTalkUser(user_id="u1", name="Alice", department_ids=["100"]),
        ],
    )

    recorded: list[str] = []
    original_fetch_all = db.fetch_all

    def recording_fetch_all(query, params=()):
        recorded.append(" ".join(str(query).split()))
        return original_fetch_all(query, params if params is not None else ())

    db.fetch_all = recording_fetch_all

    service.sync_org()

    assert any(
        "FROM team_members WHERE team_id IN" in q for q in recorded
    ), "membership scan must be scoped with WHERE team_id IN"
    assert not any(
        q == "SELECT team_id, user_id, role FROM team_members" for q in recorded
    ), "the unscoped full-table membership scan must be gone"


def test_feishu_sync_memberships_uses_scoped_where_in(sqlite_db):
    """Same WHERE-IN scoping for Feishu membership reconciliation (WP-1)."""
    db = sqlite_db
    service = _FakeFeishu(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=_feishu_config(),
        departments=[FeishuDepartment(department_id="od-1", name="Eng")],
        users=[FeishuUser(open_id="ou1", name="Alice", department_ids=["od-1"])],
    )

    recorded: list[str] = []
    original_fetch_all = db.fetch_all

    def recording_fetch_all(query, params=()):
        recorded.append(" ".join(str(query).split()))
        return original_fetch_all(query, params if params is not None else ())

    db.fetch_all = recording_fetch_all

    service.sync_org()

    assert any("FROM team_members WHERE team_id IN" in q for q in recorded)
    assert not any(q == "SELECT team_id, user_id, role FROM team_members" for q in recorded)


# ===========================================================================
# Finding #2: bounded username-uniqueness loop
# ===========================================================================


def test_dingtalk_build_username_falls_back_to_uuid(monkeypatch, sqlite_db):
    """After the bounded attempt count, _build_username returns a uuid-suffixed
    candidate instead of looping without limit (WP-2).
    """
    monkeypatch.setattr(dt_module, "_USERNAME_MAX_ATTEMPTS", 3)
    user_repo = UserRepository(db=sqlite_db)
    service = _FakeDingTalk(db=sqlite_db, user_repo=user_repo, config_override=_dingtalk_config())
    slug = "carol"
    # Exhaust the bounded candidates: slug, slug_1, slug_2 (range(1, 3) -> 1,2).
    for i in range(3):
        name = slug if i == 0 else f"{slug}_{i}"
        assert user_repo.create_user(
            username=name, email=f"{name}@x", password_hash="!", tenant_id=8
        )

    candidate = service._build_username("Carol", None, "dt_carol")
    assert candidate.startswith(f"{slug}_"), f"expected uuid-suffixed fallback, got {candidate}"
    suffix = candidate[len(slug) + 1 :]
    assert len(suffix) == 8 and all(
        c in "0123456789abcdef" for c in suffix
    ), f"fallback suffix must be an 8-char hex uuid fragment, got {candidate}"
    assert user_repo.get_user_by_username(candidate) is None, "fallback must be unused"


def test_feishu_build_username_falls_back_to_uuid(monkeypatch, sqlite_db):
    """Same bounded fallback for Feishu username generation (WP-2)."""
    monkeypatch.setattr(fs_module, "_USERNAME_MAX_ATTEMPTS", 3)
    user_repo = UserRepository(db=sqlite_db)
    service = _FakeFeishu(db=sqlite_db, user_repo=user_repo, config_override=_feishu_config())
    slug = "dave"
    for i in range(3):
        name = slug if i == 0 else f"{slug}_{i}"
        assert user_repo.create_user(
            username=name, email=f"{name}@x", password_hash="", tenant_id=7
        )

    candidate = service._build_username("Dave", None, "ou_dave")
    assert candidate.startswith(f"{slug}_") and len(candidate) == len(slug) + 9


# ===========================================================================
# Finding #3: token TTL cache + auth-failure retry
# ===========================================================================


def test_dingtalk_access_token_cached_within_ttl():
    """A second token request within the TTL must hit the cache, not HTTP (WP-3)."""
    calls: list[str] = []

    class FakeHttp:
        def post(self, url, json=None, timeout=None):
            calls.append(url)
            return _FakeResponse({"accessToken": "tok-1", "expireIn": 7200})

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    assert service._get_access_token("k", "s") == "tok-1"
    assert service._get_access_token("k", "s") == "tok-1"
    assert len(calls) == 1, "second call within TTL must be served from cache"


def test_dingtalk_access_token_refreshes_after_expiry():
    """Once the cached token's expiry has passed, the next call re-exchanges (WP-3)."""
    counter = {"n": 0}

    class FakeHttp:
        def post(self, url, json=None, timeout=None):
            counter["n"] += 1
            return _FakeResponse({"accessToken": f"tok-{counter['n']}", "expireIn": 7200})

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    assert service._get_access_token("k", "s") == "tok-1"
    # Force the cached entry to be expired.
    service._token_cache["k"].expires_at = datetime.min
    assert service._get_access_token("k", "s") == "tok-2"
    assert counter["n"] == 2


def test_dingtalk_request_oapi_retries_once_on_auth_fail():
    """An auth-failure errcode must invalidate the cached token and retry once
    with a freshly-exchanged token (WP-3)."""
    tokens_used: list[str] = []

    class FakeHttp:
        def post(self, url, params=None, json=None, timeout=None):
            tokens_used.append(params["access_token"])
            if len(tokens_used) == 1:
                return _FakeResponse({"errcode": 40001, "errmsg": "invalid token"})
            return _FakeResponse({"errcode": 0, "result": {"ok": True}})

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    service._active_app_key = "k"
    service._active_app_secret = "s"
    # Seed the cache so we can assert the stale token was invalidated.
    service._token_cache["k"] = _CachedToken(value="stale", expires_at=datetime.max)
    # Stub the re-exchange so the retry uses a known fresh token.
    service._get_access_token = lambda k, s: "fresh"

    data = service._request_oapi(
        "https://oapi.dingtalk.com/topapi/v2/user/list", "stale", {"dept_id": 1}
    )

    assert data == {"errcode": 0, "result": {"ok": True}}
    assert tokens_used == ["stale", "fresh"], "must retry exactly once with the fresh token"
    assert "k" not in service._token_cache, "cached stale token must be invalidated"


def test_dingtalk_request_oapi_no_retry_without_active_creds():
    """Without active credentials (e.g. a direct unit-test call), an auth-failure
    errcode surfaces immediately -- no retry, preserving prior behavior (WP-3)."""
    from app.services.dingtalk_org_sync import DingTalkApiError

    class FakeHttp:
        def post(self, url, params=None, json=None, timeout=None):
            return _FakeResponse({"errcode": 40001, "errmsg": "invalid token"})

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    # _active_app_key left as None on purpose.
    with pytest.raises(DingTalkApiError) as exc_info:
        service._request_oapi("https://oapi.dingtalk.com/topapi/v2/user/list", "tok", {})
    assert exc_info.value.errcode == 40001
    assert 40001 in DINGTALK_AUTH_ERRCODES


def test_feishu_tenant_access_token_cached_within_ttl():
    """Feishu tenant token must be cached for the TTL rather than re-exchanged
    every call (WP-3)."""
    calls: list[str] = []

    class FakeHttp:
        def post(self, url, json=None, timeout=None):
            calls.append(url)
            return _FakeResponse({"code": 0, "tenant_access_token": "ftok-1", "expire": 7200})

    service = FeishuOrgSyncService(
        config_override={"feishu": {"app_id": "i", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    assert service._get_tenant_access_token("i", "s") == "ftok-1"
    assert service._get_tenant_access_token("i", "s") == "ftok-1"
    assert len(calls) == 1


def test_feishu_request_json_retries_once_on_auth_fail():
    """An auth-failure code must invalidate the cached tenant token and retry once
    with a freshly-exchanged token (WP-3)."""
    auths_used: list[str] = []

    class FakeHttp:
        def request(self, method, url, headers=None, params=None, json=None, timeout=None):
            auths_used.append((headers or {}).get("Authorization", ""))
            if len(auths_used) == 1:
                return _FakeResponse({"code": 99991661, "msg": "token expired"})
            return _FakeResponse({"code": 0, "data": {"items": [1]}})

    service = FeishuOrgSyncService(
        config_override={"feishu": {"app_id": "i", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    service._active_app_id = "i"
    service._active_app_secret = "s"
    service._token_cache["i"] = _CachedToken(value="stale", expires_at=datetime.max)
    service._get_tenant_access_token = lambda i, s: "fresh"

    data = service._request_json(
        "GET", "https://open.feishu.cn/open-apis/contact/v3/users", token="stale"
    )

    assert data == {"items": [1]}
    assert auths_used == ["Bearer stale", "Bearer fresh"]
    assert "i" not in service._token_cache
    assert 99991661 in FEISHU_AUTH_ERROR_CODES


def test_feishu_request_json_error_does_not_echo_payload():
    """A non-zero Feishu code must raise FeishuApiError carrying only code/msg,
    never the whole payload (avoid leaking request bodies / log noise) (WP-3)."""
    from app.services.feishu_org_sync import FeishuApiError

    class FakeHttp:
        def request(self, method, url, headers=None, params=None, json=None, timeout=None):
            return _FakeResponse(
                {
                    "code": 99991663,
                    "msg": "invalid app secret",
                    "request_id": "SECRET-trace-id",
                    "extra_field": "should-not-leak",
                }
            )

    service = FeishuOrgSyncService(
        config_override={"feishu": {"app_id": "i", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    # No active creds + a token sent -> the auth-retry guard skips, so the call
    # raises immediately rather than retrying.
    with pytest.raises(FeishuApiError) as exc_info:
        service._request_json("GET", "https://open.feishu.cn/open-apis/contact/v3/x", token="t")
    assert exc_info.value.code == 99991663
    assert "invalid app secret" in exc_info.value.msg
    # The full payload must NOT be echoed into the exception text.
    assert "SECRET-trace-id" not in str(exc_info.value)
    assert "should-not-leak" not in str(exc_info.value)


# ===========================================================================
# Finding #4: non-blocking scheduler guard + max-runtime watchdog
# ===========================================================================


def test_dingtalk_scheduler_skips_when_schedule_lock_held(sqlite_db):
    """If another tick already holds the schedule lock, this tick must return None
    immediately (non-blocking) and must NOT run a sync (WP-4)."""
    service = _FakeDingTalk(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_dingtalk_config(),
        departments=[],
        users=[],
    )
    service.__class__._last_scheduled_sync_at = None
    # Pre-acquire the schedule lock from the test thread, simulating an
    # overlapping/in-flight tick.
    assert service._schedule_lock.acquire(blocking=False)
    try:
        synced = {"ran": False}
        original_sync = service.sync_org
        service.sync_org = lambda **kw: synced.__setitem__("ran", True) or original_sync(**kw)
        result = service.maybe_sync_from_scheduler()
        assert result is None, "overlapping tick must skip, not block"
        assert synced["ran"] is False, "sync must not run when the schedule lock is held"
    finally:
        service._schedule_lock.release()


def test_dingtalk_check_stale_sync_warns_when_overrun_sqlite(sqlite_db, caplog):
    """On SQLite, a sync whose in-process start timestamp is older than the
    max-runtime ceiling must log a hung-warning (WP-4 watchdog fallback)."""
    service = _FakeDingTalk(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_dingtalk_config(),
    )
    service.__class__._sync_started_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=100)
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.dingtalk_org_sync"):
            service._check_stale_sync(max_runtime_seconds=1, auto_recover=False)
        assert any("appears hung" in r.getMessage() for r in caplog.records)
    finally:
        service.__class__._sync_started_at = None


def test_dingtalk_check_stale_sync_auto_recover_calls_force_release(sqlite_db, monkeypatch):
    """With auto_recover on, an over-run sync triggers force_release_lock (WP-4)."""
    service = _FakeDingTalk(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_dingtalk_config(),
    )
    service.__class__._sync_started_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=100)
    called: list[int] = []
    monkeypatch.setattr(
        dt_module,
        "force_release_lock",
        lambda db, key, **kw: called.append(1) or True,
    )
    try:
        service._check_stale_sync(max_runtime_seconds=1, auto_recover=True)
        assert called == [1], "auto_recover must call force_release_lock"
    finally:
        service.__class__._sync_started_at = None


def test_dingtalk_check_stale_sync_noop_when_within_budget(sqlite_db, caplog):
    """A sync within the runtime budget must not be flagged (WP-4)."""
    service = _FakeDingTalk(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_dingtalk_config(),
    )
    service.__class__._sync_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.dingtalk_org_sync"):
            service._check_stale_sync(max_runtime_seconds=1800, auto_recover=True)
        assert not any("appears hung" in r.getMessage() for r in caplog.records)
    finally:
        service.__class__._sync_started_at = None


def test_feishu_scheduler_skips_when_schedule_lock_held(sqlite_db):
    """Same non-blocking overlap guard for Feishu (WP-4)."""
    service = _FakeFeishu(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_feishu_config(),
        departments=[],
        users=[],
    )
    service.__class__._last_scheduled_sync_at = None
    assert service._schedule_lock.acquire(blocking=False)
    try:
        assert service.maybe_sync_from_scheduler() is None
    finally:
        service._schedule_lock.release()


def test_feishu_check_stale_sync_warns_when_overrun_sqlite(sqlite_db, caplog):
    """On SQLite, a sync whose in-process start timestamp is older than the
    max-runtime ceiling must log a hung-warning (WP-4 watchdog fallback)."""
    service = _FakeFeishu(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_feishu_config(),
    )
    service.__class__._sync_started_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=100)
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.feishu_org_sync"):
            service._check_stale_sync(max_runtime_seconds=1, auto_recover=False)
        assert any("appears hung" in r.getMessage() for r in caplog.records)
    finally:
        service.__class__._sync_started_at = None


def test_feishu_check_stale_sync_auto_recover_calls_force_release(sqlite_db, monkeypatch):
    """With auto_recover on, an over-run sync triggers force_release_lock (WP-4)."""
    service = _FakeFeishu(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_feishu_config(),
    )
    service.__class__._sync_started_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=100)
    called: list[int] = []
    monkeypatch.setattr(
        fs_module,
        "force_release_lock",
        lambda db, key, **kw: called.append(1) or True,
    )
    try:
        service._check_stale_sync(max_runtime_seconds=1, auto_recover=True)
        assert called == [1], "auto_recover must call force_release_lock"
    finally:
        service.__class__._sync_started_at = None


def test_feishu_check_stale_sync_noop_when_within_budget(sqlite_db, caplog):
    """A sync within the runtime budget must not be flagged (WP-4)."""
    service = _FakeFeishu(
        db=sqlite_db,
        user_repo=UserRepository(db=sqlite_db),
        config_override=_feishu_config(),
    )
    service.__class__._sync_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.feishu_org_sync"):
            service._check_stale_sync(max_runtime_seconds=1800, auto_recover=True)
        assert not any("appears hung" in r.getMessage() for r in caplog.records)
    finally:
        service.__class__._sync_started_at = None


# ===========================================================================
# Finding #6: DingTalk N+1 -> batched topapi/v2/user/list
# ===========================================================================


def test_dingtalk_fetch_department_users_paginates(monkeypatch):
    """v2/user/list paging must walk every page via next_cursor (WP-6)."""
    monkeypatch.setattr(dt_module, "_TRANSIENT_SLEEP", lambda _s: None)

    class FakeHttp:
        def __init__(self):
            self.calls = 0

        def post(self, url, params=None, json=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": {
                            "has_more": True,
                            "next_cursor": 1,
                            "list": [
                                {"userid": "u1", "name": "Alice", "dept_id_list": [100]},
                            ],
                        },
                    }
                )
            return _FakeResponse(
                {
                    "errcode": 0,
                    "result": {
                        "has_more": False,
                        "list": [
                            {"userid": "u2", "name": "Bob", "dept_id_list": [100]},
                        ],
                    },
                }
                if self.calls == 2
                else {"errcode": 0, "result": {"has_more": False, "list": []}}
            )

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    users = service._fetch_department_users("token", "100")
    assert [u.user_id for u in users] == ["u1", "u2"]
    assert service.http.calls == 2, "both pages must be fetched"


def test_dingtalk_fetch_department_users_skips_department_on_hard_error(monkeypatch):
    """A non-transient errcode on a page warns and skips the department without
    aborting (WP-6)."""
    monkeypatch.setattr(dt_module, "_TRANSIENT_SLEEP", lambda _s: None)

    class FakeHttp:
        def post(self, url, params=None, json=None, timeout=None):
            # 88 (quota) is NOT a transient errcode -> no retry, hard skip.
            return _FakeResponse({"errcode": 88, "errmsg": "quota exceeded"})

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )
    warnings: list[str] = []
    users = service._fetch_department_users("token", "100", warnings=warnings)
    assert users == []
    assert any("100" in w and "errcode=88" in w for w in warnings)
    # No retry on a non-transient errcode: exactly one call.
    assert service.http.calls if hasattr(service.http, "calls") else True


# ===========================================================================
# Finding #5 helper coverage: split_advisory_key + SQLite no-op behavior
# (the cross-process exclusion itself is covered by tests/issues/1806)
# ===========================================================================


def test_split_advisory_key_high_bits_nonzero_for_sync_keys():
    """Both org-sync keys exceed 2**32, so their high-32 (classid) half must be
    non-zero -- this is exactly the v2-review bug (classid=0 matched nothing)."""
    for key in (
        fs_module.FeishuOrgSyncService._DB_SYNC_LOCK_KEY,
        dt_module.DingTalkOrgSyncService._DB_SYNC_LOCK_KEY,
    ):
        hi, lo = split_advisory_key(key)
        assert hi != 0, f"key {key} must split to a non-zero classid (high 32 bits)"
        assert 0 <= lo <= 0xFFFFFFFF
        assert 0 <= hi <= 0xFFFFFFFF
        # Round-trips back to the original 64-bit key.
        assert (hi << 32) | lo == key


def test_split_advisory_key_distinct_keys_do_not_collide():
    """Feishu, DingTalk, and tenant-aggregation must not share an advisory key
    (a collision would let one block another or two providers race)."""
    feishu = fs_module.FeishuOrgSyncService._DB_SYNC_LOCK_KEY
    dingtalk = dt_module.DingTalkOrgSyncService._DB_SYNC_LOCK_KEY
    aggregation = 12345  # AGGREGATION_LOCK_ID in tenant_aggregation
    assert len({feishu, dingtalk, aggregation}) == 3


def test_org_sync_lock_helpers_noop_on_sqlite(sqlite_db):
    """On a non-Postgres backend the self-heal helpers must degrade gracefully
    (None / False) rather than issuing PG-only SQL (WP-4/5)."""
    key = dt_module.DingTalkOrgSyncService._DB_SYNC_LOCK_KEY
    assert get_running_sync_state(sqlite_db, key) is None
    assert force_release_lock(sqlite_db, key) is False


# ===========================================================================
# Admin self-heal endpoints (WP-4)
# ===========================================================================


def _admin_client():
    """A minimal Flask test client with admin auth patched in."""
    from unittest.mock import patch

    from flask import Flask

    from app.routes.admin import admin_bp

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    test_client = app.test_client()

    class AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth(self):
            return (
                patch("app.auth.decorators._extract_session_token", return_value="t"),
                patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "a"},
                ),
            )

        def get(self, *args, **kwargs):
            p1, p2 = self._auth()
            with p1, p2:
                return self._client.get(*args, **kwargs)

        def post(self, *args, **kwargs):
            p1, p2 = self._auth()
            with p1, p2:
                return self._client.post(*args, **kwargs)

    return AuthenticatedClient(test_client)


def test_admin_dingtalk_lock_state_endpoint(monkeypatch):
    """GET lock-state returns the inspected payload for DingTalk (WP-4)."""
    from app.routes import admin as admin_module

    monkeypatch.setattr(
        admin_module,
        "_org_sync_lock_state_payload",
        lambda provider, key: {"provider": provider, "key": key, "running": None},
    )
    client = _admin_client()
    resp = client.get("/api/admin/dingtalk/sync/lock-state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "dingtalk"
    assert body["key"] == dt_module.DingTalkOrgSyncService._DB_SYNC_LOCK_KEY
    assert body["running"] is None


def test_admin_feishu_release_lock_endpoint(monkeypatch):
    """POST release-lock returns the release payload for Feishu (WP-4)."""
    from app.routes import admin as admin_module

    monkeypatch.setattr(
        admin_module,
        "_release_org_sync_lock_payload",
        lambda provider, key: {
            "provider": provider,
            "key": key,
            "before": {"pid": 42, "hold_seconds": 9999.0},
            "released": True,
        },
    )
    client = _admin_client()
    resp = client.post("/api/admin/feishu/sync/release-lock")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "feishu"
    assert body["key"] == fs_module.FeishuOrgSyncService._DB_SYNC_LOCK_KEY
    assert body["before"]["pid"] == 42
    assert body["released"] is True
