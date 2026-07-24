"""DingTalk organization sync service.

Synchronizes DingTalk departments and users into local Open ACE teams, team
memberships, and SSO identity mappings.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import requests

from app.modules.sso.manager import SSOManager
from app.modules.workspace.collaboration import CollaborationManager
from app.repositories.database import Database, release_postgresql_connection
from app.repositories.user_repo import UserRepository
from app.services._org_sync_lock import force_release_lock, get_running_sync_state
from app.utils.config import get_config_value

# Injectable sleep used by the page-level transient retry so tests can avoid
# real waiting (WP-6). Reassigned via monkeypatch, never call time.sleep directly.
_TRANSIENT_SLEEP = time.sleep

logger = logging.getLogger(__name__)

DINGTALK_PROVIDER_NAME = "dingtalk"
DINGTALK_ROOT_DEPARTMENT_ID = "1"
DINGTALK_PLACEHOLDER_EMAIL_DOMAIN = "dingtalk.local"
# Stable key for the Postgres advisory lock guarding DingTalk sync_org so that
# multiple workers cannot run concurrent syncs. Picked as a fixed constant so
# all workers contend on the same lock; fits in a signed int64. WARNING: do NOT
# change this once deployed, and it MUST differ from Feishu's
# _FEISHU_SYNC_LOCK_KEY (88342611905720321) and tenant_aggregation's
# AGGREGATION_LOCK_ID (12345) -- shared/adjacent keys would let unrelated locks
# block each other or two providers race on the same lock.
_DINGTALK_SYNC_LOCK_KEY = 61740164982374657
# Bound on the username-collision retry loop in _build_username; beyond this we
# fall back to a uuid-suffixed candidate rather than looping without limit.
_USERNAME_MAX_ATTEMPTS = 100
# DingTalk errcodes that mean the cached access token is bad -> invalidate and
# retry once with a freshly-exchanged token (WP-3 auth-fail retry).
DINGTALK_AUTH_ERRCODES = {40001, 40014, 42001}
# DingTalk errcodes that are transient (rate-limit / momentary) -> bounded retry
# with backoff at the call site (WP-6 page-level retry).
DINGTALK_TRANSIENT_ERRCODES = {-1}
# How many times a transient page error is retried, and the base backoff.
_TRANSIENT_MAX_RETRIES = 3
_TRANSIENT_BACKOFF_BASE = 0.2


class DingTalkApiError(RuntimeError):
    """Raised when a DingTalk oapi call returns a non-zero errcode.

    Carries only ``errcode``/``errmsg`` (never the raw payload) so transient
    errors are debuggable without echoing request bodies. Subclasses
    RuntimeError so existing ``pytest.raises(RuntimeError)`` still match.
    """

    def __init__(self, errcode: Any, errmsg: str):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"DingTalk API request failed (errcode={errcode}): {errmsg}")


@dataclass
class _CachedToken:
    """A cached provider access token with an absolute expiry timestamp."""

    value: str
    expires_at: datetime


@dataclass
class DingTalkDepartment:
    """DingTalk department record used during synchronization."""

    department_id: str
    name: str
    parent_department_id: str | None = None
    order: int | None = None


@dataclass
class DingTalkUser:
    """DingTalk user record used during synchronization."""

    user_id: str
    name: str
    email: str | None = None
    department_ids: list[str] = field(default_factory=list)
    status: dict[str, Any] = field(default_factory=dict)


@dataclass
class DingTalkOrgSyncResult:
    """Summary returned to admin/API callers after a sync run."""

    tenant_id: int
    departments_seen: int = 0
    users_seen: int = 0
    teams_created: int = 0
    teams_updated: int = 0
    users_created: int = 0
    users_linked: int = 0
    users_updated: int = 0
    memberships_added: int = 0
    memberships_removed: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to a JSON-friendly dictionary."""
        return {
            "tenant_id": self.tenant_id,
            "departments_seen": self.departments_seen,
            "users_seen": self.users_seen,
            "teams_created": self.teams_created,
            "teams_updated": self.teams_updated,
            "users_created": self.users_created,
            "users_linked": self.users_linked,
            "users_updated": self.users_updated,
            "memberships_added": self.memberships_added,
            "memberships_removed": self.memberships_removed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "warnings": list(self.warnings),
        }


class DingTalkOrgSyncService:
    """Synchronize DingTalk org data into local users and collaboration teams."""

    _sync_lock = threading.Lock()
    _schedule_lock = threading.Lock()
    _last_scheduled_sync_at: datetime | None = None
    # Per-process start timestamp of the in-flight sync; used only as the
    # SQLite fallback for the max-runtime watchdog (single-process). On Postgres
    # the authoritative "is a sync running / for how long" signal is derived
    # cross-process from pg_locks + pg_stat_activity (see _org_sync_lock), so
    # this class var is NOT relied on for multi-worker detection.
    _sync_started_at: datetime | None = None
    # Class-level handle exposed for tests/observability; see _acquire_sync_lock.
    # WARNING: do NOT change this value once deployed. It is the Postgres
    # advisory-lock key shared by every worker process for cross-process mutual
    # exclusion; changing it would make old and new workers lock on different
    # keys and run overlapping syncs. Must remain distinct from Feishu's
    # _FEISHU_SYNC_LOCK_KEY and tenant_aggregation's AGGREGATION_LOCK_ID.
    _DB_SYNC_LOCK_KEY: int = _DINGTALK_SYNC_LOCK_KEY

    def __init__(
        self,
        db: Database | None = None,
        user_repo: UserRepository | None = None,
        sso_manager: SSOManager | None = None,
        collaboration_manager: CollaborationManager | None = None,
        config_override: dict[str, Any] | None = None,
        http_session=None,
    ):
        self.db = db or Database()
        self.user_repo = user_repo or UserRepository(db=self.db)
        self.sso_manager = sso_manager or SSOManager(db=self.db)
        self.collaboration_manager = collaboration_manager or CollaborationManager()
        self.config_override = config_override
        self.http = http_session or requests
        # Per-instance TTL token cache keyed by app credential, plus the active
        # credentials for the in-flight sync (used by _request_oapi's auth-fail
        # retry). Token caching avoids re-exchanging every sync run (WP-3).
        self._token_cache: dict[str, _CachedToken] = {}
        self._active_app_key: str | None = None
        self._active_app_secret: str | None = None

    def sync_org(self, tenant_id: int | None = None) -> DingTalkOrgSyncResult:
        """Run a full DingTalk org sync."""
        config = self._get_dingtalk_config()
        app_key = str(config.get("app_key") or "").strip()
        app_secret = str(config.get("app_secret") or "").strip()
        if not app_key or not app_secret:
            raise ValueError("DingTalk app_key and app_secret must be configured before syncing")

        effective_tenant_id = int(tenant_id or config.get("org_sync_tenant_id") or 1)
        root_department_id = str(config.get("org_sync_root_dept_id") or DINGTALK_ROOT_DEPARTMENT_ID)
        result = DingTalkOrgSyncResult(
            tenant_id=effective_tenant_id,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        )

        # Publish the active credentials so _request_oapi can re-exchange the token
        # on an auth-failure errcode (WP-3). Cleared in the finally below so a later
        # direct call to _request_oapi (e.g. in tests) does not pick up stale creds.
        self._active_app_key = app_key
        self._active_app_secret = app_secret
        try:
            with self._acquire_sync_lock():
                # Record the in-flight start for the SQLite max-runtime watchdog
                # fallback (single-process). On Postgres the authoritative signal is
                # derived cross-process from pg_locks (see _org_sync_lock).
                self.__class__._sync_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                try:
                    self._ensure_supporting_tables()
                    token = self._get_access_token(app_key, app_secret)
                    departments, users = self._fetch_directory_snapshot(
                        token, root_department_id, warnings=result.warnings
                    )
                    result.departments_seen = len(departments)
                    result.users_seen = len(users)

                    # Cache the synced-team index once per run instead of re-scanning
                    # the whole teams table per department (WP-1). Newly created teams
                    # are appended so later iterations in the same run see them.
                    synced_teams_index = self._load_synced_teams()
                    team_ids_by_department: dict[str, str] = {}
                    for department in departments:
                        team_id, created = self._upsert_department_team(
                            department, existing_teams=synced_teams_index
                        )
                        team_ids_by_department[department.department_id] = team_id
                        if created:
                            result.teams_created += 1
                        else:
                            result.teams_updated += 1

                    expected_memberships: set[tuple[str, int]] = set()
                    seen_provider_user_ids: set[str] = set()
                    for user in users:
                        user_id, created, linked, updated = self._resolve_local_user(
                            user=user,
                            tenant_id=effective_tenant_id,
                            result=result,
                        )
                        if user_id is None:
                            continue
                        seen_provider_user_ids.add(user.user_id)
                        if created:
                            result.users_created += 1
                        elif linked:
                            result.users_linked += 1
                        if updated:
                            result.users_updated += 1

                        for department_id in user.department_ids:
                            membership_team_id = team_ids_by_department.get(department_id)
                            if membership_team_id:
                                expected_memberships.add((membership_team_id, user_id))

                    self._sync_memberships(
                        expected_memberships=expected_memberships,
                        synced_team_ids=set(team_ids_by_department.values()),
                        result=result,
                    )

                    # Deactivate/unlink DingTalk users that were synced previously but are no
                    # longer in the directory. DingTalk recycles userids, so leaving a stale
                    # SSO identity row would let a recycled id re-resolve to the old account.
                    self._deactivate_departed_users(
                        tenant_id=effective_tenant_id,
                        seen_provider_user_ids=seen_provider_user_ids,
                        result=result,
                    )

                    result.finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    return result
                finally:
                    self.__class__._sync_started_at = None
        finally:
            self._active_app_key = None
            self._active_app_secret = None

    def maybe_sync_from_scheduler(self) -> DingTalkOrgSyncResult | None:
        """Run scheduled sync when enabled, the interval has elapsed, and no other
        tick is already in flight.

        The schedule lock is acquired **non-blocking**: if another scheduler tick
        already holds it (a prior sync is still running, or ticks overlap), this
        tick skips entirely instead of queueing behind it -- overlapping ticks
        would only pile up load without producing an earlier sync. A max-runtime
        watchdog runs first: if the previous sync still appears to be running
        past the configured ceiling, it is logged and (when ``org_sync_auto_recover``
        is opted in) forcibly released before this run proceeds.
        """
        config = self._get_dingtalk_config()
        if not bool(config.get("org_sync_enabled", False)):
            return None

        interval_minutes = max(int(config.get("org_sync_interval_minutes") or 60), 5)
        max_runtime_seconds = int(config.get("org_sync_max_runtime_seconds") or 1800)
        auto_recover = bool(config.get("org_sync_auto_recover", False))
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        if not self._schedule_lock.acquire(blocking=False):
            return None
        try:
            if self._last_scheduled_sync_at and (now - self._last_scheduled_sync_at) < timedelta(
                minutes=interval_minutes
            ):
                return None

            self._check_stale_sync(max_runtime_seconds, auto_recover)

            result = self.sync_org(tenant_id=config.get("org_sync_tenant_id"))
            self.__class__._last_scheduled_sync_at = now
            return result
        finally:
            self._schedule_lock.release()

    def _check_stale_sync(self, max_runtime_seconds: int, auto_recover: bool) -> None:
        """Detect a sync that has run past ``max_runtime_seconds`` and recover it.

        On Postgres the running state is derived cross-process from ``pg_locks``
        (authoritative across workers); on SQLite it falls back to the in-process
        ``_sync_started_at`` timestamp. A stale run is always logged; when
        ``auto_recover`` is set, ``force_release_lock`` terminates the holder pid
        (Postgres only) so the next sync can proceed.
        """
        state = get_running_sync_state(self.db, self._DB_SYNC_LOCK_KEY)
        if state is None:
            # Non-Postgres: use the in-process start timestamp fallback.
            started = self.__class__._sync_started_at
            if started is None:
                return
            hold_seconds = (
                datetime.now(timezone.utc).replace(tzinfo=None) - started
            ).total_seconds()
            pid = None
        else:
            hold_seconds = float(state.get("hold_seconds", 0.0) or 0.0)
            pid = state.get("pid")

        if hold_seconds <= max_runtime_seconds:
            return
        logger.warning(
            "DingTalk org sync appears hung (running ~%.0fs, max=%ss, pid=%s)",
            hold_seconds,
            max_runtime_seconds,
            pid,
        )
        if not auto_recover:
            return
        released = force_release_lock(self.db, self._DB_SYNC_LOCK_KEY)
        logger.warning(
            "DingTalk org sync auto-recover force_release_lock(key=%s) -> %s",
            self._DB_SYNC_LOCK_KEY,
            released,
        )

    def _ensure_supporting_tables(self) -> None:
        """Ensure dependent tables exist before syncing."""
        self.sso_manager._ensure_tables()
        self.collaboration_manager._ensure_tables()

    @contextmanager
    def _acquire_sync_lock(self):
        """Acquire mutual-exclusion for sync_org.

        On PostgreSQL a **session-level** advisory lock is taken so that
        concurrent workers (separate processes) cannot run overlapping syncs.
        The lock is bound to a single dedicated connection held open across the
        entire critical section (the ``yield``) and only released afterwards;
        this is essential because ``Database`` commits per statement and returns
        pooled connections after every call, so a transaction-level lock would
        be freed the instant ``fetch_one`` returned -- before the sync body
        started. The in-process threading.Lock is still acquired first as a
        cheap fence to avoid needless DB round-trips within a single worker. On
        SQLite (and other non-Postgres backends) the advisory lock is
        unavailable, so the threading.Lock remains the only guard; SQLite
        deployments are single-process by nature.
        """
        with self._sync_lock:
            conn = None
            if self.db.is_postgresql:
                conn = self.db.get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT pg_try_advisory_lock(%s)",
                        (self._DB_SYNC_LOCK_KEY,),
                    )
                    # Database.get_connection() returns a PgConnectionWrapper
                    # that forces cursor_factory=RealDictCursor, so fetchone()
                    # yields a dict keyed by the SELECT column name -- NOT a
                    # positional tuple. Indexing it as [0] raises KeyError: 0.
                    ok = cur.fetchone()["pg_try_advisory_lock"]
                    if not ok:
                        raise RuntimeError("Another DingTalk org sync is already running")
                    try:
                        yield
                    finally:
                        # Session-level locks survive transaction end, so they
                        # MUST be released explicitly on the same connection.
                        # A failure here would leak the advisory lock and wedge
                        # every future sync until the backend connection dies,
                        # so we log loudly instead of silently swallowing it.
                        try:
                            unlock_cur = conn.cursor()
                            unlock_cur.execute(
                                "SELECT pg_advisory_unlock(%s)",
                                (self._DB_SYNC_LOCK_KEY,),
                            )
                            unlocked = unlock_cur.fetchone()
                            if not unlocked or not unlocked.get("pg_advisory_unlock"):
                                logger.warning(
                                    "pg_advisory_unlock(%s) reported the lock "
                                    "was not held by this session; a prior "
                                    "error may have leaked it",
                                    self._DB_SYNC_LOCK_KEY,
                                )
                        except Exception:
                            logger.warning(
                                "Failed to release DingTalk org sync advisory "
                                "lock key=%s; the lock may leak and block "
                                "future syncs until the connection is closed",
                                self._DB_SYNC_LOCK_KEY,
                                exc_info=True,
                            )
                        try:
                            conn.commit()
                        except Exception:
                            logger.warning(
                                "Failed to commit after releasing DingTalk org "
                                "sync advisory lock key=%s",
                                self._DB_SYNC_LOCK_KEY,
                                exc_info=True,
                            )
                finally:
                    if conn is not None:
                        release_postgresql_connection(conn)
            else:
                yield

    def _get_dingtalk_config(self) -> dict[str, Any]:
        """Load DingTalk config from override or app config."""
        if self.config_override:
            if "dingtalk" in self.config_override:
                config = dict(self.config_override.get("dingtalk") or {})
            else:
                config = dict(self.config_override)
        else:
            config = {
                "app_key": get_config_value("dingtalk", "app_key", ""),
                "app_secret": get_config_value("dingtalk", "app_secret", ""),
                "org_sync_enabled": bool(get_config_value("dingtalk", "org_sync_enabled", False)),
                "org_sync_tenant_id": int(
                    get_config_value("dingtalk", "org_sync_tenant_id", 1) or 1
                ),
                "org_sync_interval_minutes": int(
                    get_config_value("dingtalk", "org_sync_interval_minutes", 60) or 60
                ),
                "org_sync_root_dept_id": str(
                    get_config_value("dingtalk", "org_sync_root_dept_id", "1") or "1"
                ),
            }

        config.setdefault("org_sync_enabled", False)
        config.setdefault("org_sync_tenant_id", 1)
        config.setdefault("org_sync_interval_minutes", 60)
        config.setdefault("org_sync_root_dept_id", DINGTALK_ROOT_DEPARTMENT_ID)
        # Watchdog ceiling for a single sync run; a run exceeding this is treated
        # as hung. Opt-in auto-recover force-releases the advisory lock so the next
        # tick can proceed (default off -- destructive, only for known-stuck deploys).
        config.setdefault("org_sync_max_runtime_seconds", 1800)
        config.setdefault("org_sync_auto_recover", False)
        return config

    def _get_access_token(self, app_key: str, app_secret: str) -> str:
        """Exchange app credentials for a DingTalk access token, cached with a TTL.

        DingTalk tokens are valid for ~2 hours; re-exchanging on every sync run
        (and on every auth-fail retry) is wasteful and rate-limit-prone. The token
        is cached per app_key until one minute before its real expiry.
        """
        cache_key = app_key
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cached = self._token_cache.get(cache_key)
        if cached and cached.expires_at > now:
            return cached.value

        response = self.http.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": app_key, "appSecret": app_secret},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("accessToken") or data.get("access_token")
        if not token:
            raise RuntimeError(f"Failed to get DingTalk access token: {data}")
        token = str(token)

        # DingTalk documents expireIn in milliseconds, but some paths return
        # seconds; disambiguate by magnitude (>100000 => ms) and cap at the 2h
        # ceiling so a bogus value can't pin the cache forever.
        expire_in = data.get("expireIn") or data.get("expires_in") or 7200
        try:
            expire_in = int(expire_in)
        except (TypeError, ValueError):
            expire_in = 7200
        if expire_in > 100000:
            expire_in = expire_in // 1000
        expire_in = min(max(expire_in, 60), 7200)
        # Refresh a minute before the real expiry so a call made near the deadline
        # doesn't race the token's remaining lifetime.
        self._token_cache[cache_key] = _CachedToken(
            value=token,
            expires_at=now + timedelta(seconds=max(expire_in - 60, 30)),
        )
        return token

    def _invalidate_access_token(self, app_key: str) -> None:
        """Drop the cached token so the next call re-exchanges it (auth-fail path)."""
        self._token_cache.pop(app_key, None)

    def _fetch_directory_snapshot(
        self,
        token: str,
        root_department_id: str,
        warnings: list[str] | None = None,
    ) -> tuple[list[DingTalkDepartment], list[DingTalkUser]]:
        """Recursively fetch departments and users starting from the configured root."""
        departments: dict[str, DingTalkDepartment] = {}
        users: dict[str, DingTalkUser] = {}

        queue: deque[str] = deque([root_department_id])
        visited: set[str] = set()

        while queue:
            current_department_id = queue.popleft()
            if current_department_id in visited:
                continue
            visited.add(current_department_id)

            child_departments = self._fetch_child_departments(token, current_department_id)
            for department in child_departments:
                if department.department_id not in departments:
                    departments[department.department_id] = department
                    queue.append(department.department_id)

            direct_users = self._fetch_department_users(
                token, current_department_id, warnings=warnings
            )
            for user in direct_users:
                existing = users.get(user.user_id)
                if existing is None:
                    users[user.user_id] = user
                    continue

                merged_departments = set(existing.department_ids)
                merged_departments.update(user.department_ids)
                existing.department_ids = sorted(merged_departments)
                if not existing.email and user.email:
                    existing.email = user.email
                if user.name:
                    existing.name = user.name
                if user.status:
                    existing.status.update(user.status)

        sorted_departments = sorted(
            departments.values(),
            key=lambda d: (
                d.parent_department_id or "",
                d.order if d.order is not None else 0,
                d.name.lower(),
            ),
        )
        sorted_users = sorted(users.values(), key=lambda u: (u.name.lower(), u.user_id))
        return sorted_departments, sorted_users

    def _fetch_child_departments(self, token: str, department_id: str) -> list[DingTalkDepartment]:
        """Fetch immediate child departments for a DingTalk department."""
        payload: dict[str, Any] = {"dept_id": self._coerce_dept_id(department_id)}
        data = self._request_oapi(
            "https://oapi.dingtalk.com/topapi/v2/department/listsub",
            token=token,
            json_payload=payload,
        )
        items = self._extract_items(data, ("result", "dept_list", "departments"))

        departments: list[DingTalkDepartment] = []
        for item in items:
            department_id_value = item.get("dept_id") or item.get("id")
            if department_id_value is None:
                continue

            parent_id = item.get("parent_id")
            if parent_id is None and department_id != DINGTALK_ROOT_DEPARTMENT_ID:
                parent_id = department_id

            departments.append(
                DingTalkDepartment(
                    department_id=str(department_id_value),
                    name=str(item.get("name") or department_id_value),
                    parent_department_id=str(parent_id) if parent_id is not None else None,
                    order=int(item["order"]) if item.get("order") is not None else None,
                )
            )

        return departments

    def _fetch_department_users(
        self,
        token: str,
        department_id: str,
        warnings: list[str] | None = None,
    ) -> list[DingTalkUser]:
        """Fetch users directly under a DingTalk department.

        Uses the batched ``topapi/v2/user/list`` endpoint (one call per page of up
        to 100 users) instead of the prior ``user/listid`` + per-user ``user/get``
        N+1 pattern: each page already returns full user detail records, so no
        follow-up per-user call is needed. Page-level transient errors (rate-limit
        ``errcode -1``) are retried with bounded backoff; a non-transient error on
        a page warns and stops paging that department without aborting the run.
        """
        users: list[DingTalkUser] = []
        cursor = 0
        while True:
            data = self._fetch_user_page(token, department_id, cursor, warnings=warnings)
            if data is None:
                # Page failed (non-transient errcode or retries exhausted): keep
                # whatever users were already collected and stop paging this dept.
                return users
            result = data.get("result") if isinstance(data.get("result"), dict) else data
            if not isinstance(result, dict):
                result = {}
            page = result.get("list") or result.get("userlist") or []
            if isinstance(page, list):
                for detail in page:
                    if not isinstance(detail, dict):
                        continue
                    user = self._user_from_detail(detail, default_department_id=department_id)
                    if user is not None:
                        users.append(user)
            if not result.get("has_more"):
                break
            next_cursor = result.get("next_cursor")
            if next_cursor is None:
                break
            cursor = int(next_cursor)
        return users

    def _fetch_user_page(
        self,
        token: str,
        department_id: str,
        cursor: int,
        warnings: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one page of department users.

        Retries transient errcodes (``DINGTALK_TRANSIENT_ERRCODES``) with bounded
        exponential backoff. Returns the parsed payload on success, or None if the
        page ultimately failed (non-transient errcode, retries exhausted, or a
        transport-level error) -- in which case a warning is appended.
        """
        last_err: DingTalkApiError | None = None
        for attempt in range(_TRANSIENT_MAX_RETRIES + 1):
            try:
                return self._request_oapi(
                    "https://oapi.dingtalk.com/topapi/v2/user/list",
                    token=token,
                    json_payload={
                        "dept_id": self._coerce_dept_id(department_id),
                        "cursor": cursor,
                        "size": 100,
                    },
                )
            except DingTalkApiError as exc:
                last_err = exc
                if exc.errcode in DINGTALK_TRANSIENT_ERRCODES and attempt < _TRANSIENT_MAX_RETRIES:
                    # Exponential backoff: base * 2^attempt (0.2, 0.4, 0.8, ...).
                    _TRANSIENT_SLEEP(_TRANSIENT_BACKOFF_BASE * (2**attempt))
                    continue
                break
            except Exception as exc:  # network / JSON parse / transport error
                msg = f"Skipped DingTalk department {department_id} users: {exc}"
                logger.warning(msg)
                if warnings is not None:
                    warnings.append(msg)
                return None
        # Non-transient errcode or transient retries exhausted.
        msg = (
            f"Skipped DingTalk department {department_id} users "
            f"(errcode={last_err.errcode if last_err else '?'}): "
            f"{last_err.errmsg if last_err else 'unknown error'}"
        )
        logger.warning(msg)
        if warnings is not None:
            warnings.append(msg)
        return None

    def _user_from_detail(
        self,
        detail: dict[str, Any],
        default_department_id: str,
    ) -> DingTalkUser | None:
        """Build a DingTalkUser from a v2/user/list (or user/get) detail record."""
        user_id = detail.get("userid") or detail.get("user_id")
        if not user_id:
            return None
        user_id = str(user_id)

        department_ids = detail.get("dept_id_list") or detail.get("dept_id_list_ext")
        if not isinstance(department_ids, list):
            department_ids = [default_department_id]

        email = detail.get("email") or detail.get("org_email")
        status = {
            "active": detail.get("active"),
            "admin": detail.get("admin"),
            "boss": detail.get("boss"),
        }

        return DingTalkUser(
            user_id=user_id,
            name=str(detail.get("name") or detail.get("nick") or detail.get("nickname") or user_id),
            email=str(email) if email else None,
            department_ids=[str(dep_id) for dep_id in department_ids if dep_id],
            status={k: v for k, v in status.items() if v is not None},
        )

    def _request_oapi(
        self,
        url: str,
        token: str,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a DingTalk oapi endpoint and return the response payload.

        On an auth-failure errcode (bad/expired token) the cached token is
        invalidated and the call retried once with a freshly-exchanged token --
        but only when active credentials are known (a real sync is driving the
        call). Without active credentials (e.g. a direct unit-test call) the
        error surfaces immediately, preserving the prior raise-on-error behavior.
        """
        return self._request_oapi_once(url, token, json_payload, retried=False)

    def _request_oapi_once(
        self,
        url: str,
        token: str,
        json_payload: dict[str, Any] | None,
        retried: bool,
    ) -> dict[str, Any]:
        response = self.http.post(
            url,
            params={"access_token": token},
            json=json_payload or {},
            timeout=15,
        )
        response.raise_for_status()
        payload = cast("dict[str, Any]", response.json())
        errcode = payload.get("errcode", 0)
        if errcode == 0:
            return payload
        # Surface only errcode/errmsg (never the whole payload) so transient errors
        # are debuggable without echoing request bodies.
        errmsg = payload.get("errmsg") or payload.get("message") or "unknown error"
        if (
            not retried
            and errcode in DINGTALK_AUTH_ERRCODES
            and self._active_app_key
            and self._active_app_secret
        ):
            self._invalidate_access_token(self._active_app_key)
            fresh = self._get_access_token(self._active_app_key, self._active_app_secret)
            return self._request_oapi_once(url, fresh, json_payload, retried=True)
        raise DingTalkApiError(errcode, errmsg)

    @staticmethod
    def _extract_items(data: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        """Pull the first list-valued key from a DingTalk API payload."""
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = DingTalkOrgSyncService._extract_items(value, keys)
                if nested:
                    return nested
        return []

    @staticmethod
    def _coerce_dept_id(value: str) -> int | str:
        """Return an integer department id when possible for DingTalk APIs."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    def _upsert_department_team(
        self,
        department: DingTalkDepartment,
        existing_teams: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[str, bool]:
        """Create or update a local team representing a DingTalk department.

        ``existing_teams`` is the per-run synced-team index (built once by
        ``_load_synced_teams`` and threaded through the department loop). When a
        new team is created it is appended to that dict so later iterations in the
        same run -- and the membership reconcile -- see it without re-scanning the
        whole ``teams`` table (WP-1).
        """
        if existing_teams is None:
            existing_teams = self._load_synced_teams()
        existing = existing_teams.get(department.department_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        settings: dict[str, Any] = {
            "sync_source": DINGTALK_PROVIDER_NAME,
            "dingtalk_department_id": department.department_id,
            "dingtalk_parent_department_id": department.parent_department_id,
        }
        # Preserve the promoted-role stash across team updates so transient dept
        # moves don't lose manually-assigned owner/leader roles.
        if existing:
            existing_settings: Any = None
            existing_settings_raw = existing.get("settings")
            try:
                existing_settings = (
                    json.loads(existing_settings_raw)
                    if isinstance(existing_settings_raw, str)
                    else existing_settings_raw
                )
            except (TypeError, json.JSONDecodeError):
                existing_settings = {}
            if isinstance(existing_settings, dict):
                preserved = existing_settings.get("dingtalk_preserved_roles")
                if isinstance(preserved, dict) and preserved:
                    settings["dingtalk_preserved_roles"] = preserved
        settings_json = json.dumps(settings, ensure_ascii=False)

        if existing:
            self.db.execute(
                """
                UPDATE teams
                SET name = ?, settings = ?, updated_at = ?
                WHERE team_id = ?
                """,
                (
                    department.name,
                    settings_json,
                    now,
                    existing["team_id"],
                ),
            )
            return str(existing["team_id"]), False

        team_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO teams (team_id, name, description, owner_id, settings, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_id,
                department.name,
                "",
                None,
                settings_json,
                now,
                now,
            ),
        )
        # Make the new team visible to later iterations of this run without another
        # full-table scan of teams (WP-1).
        existing_teams[department.department_id] = {
            "team_id": team_id,
            "name": department.name,
            "settings": settings_json,
        }
        return team_id, True

    def _load_synced_teams(self) -> dict[str, dict[str, Any]]:
        """Return existing teams that are owned by DingTalk org sync."""
        rows = self.db.fetch_all("SELECT team_id, name, settings FROM teams")
        synced: dict[str, dict[str, Any]] = {}
        for row in rows:
            settings_raw = row.get("settings")
            if not settings_raw:
                continue
            try:
                settings = (
                    json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
                )
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(settings, dict):
                continue
            if settings.get("sync_source") != DINGTALK_PROVIDER_NAME:
                continue
            department_id = settings.get("dingtalk_department_id")
            if department_id:
                synced[str(department_id)] = row
        return synced

    def _resolve_local_user(
        self,
        user: DingTalkUser,
        tenant_id: int,
        result: DingTalkOrgSyncResult,
    ) -> tuple[int | None, bool, bool, bool]:
        """Resolve or provision a local user for a DingTalk user."""
        existing_user_id = self.sso_manager.get_user_by_sso_identity(
            DINGTALK_PROVIDER_NAME,
            user.user_id,
        )
        created = False
        linked = False
        updated = False

        existing_user = (
            self.user_repo.get_user_by_id(existing_user_id)
            if existing_user_id is not None
            else None
        )
        if existing_user and existing_user.get("tenant_id") not in (None, tenant_id):
            result.warnings.append(
                f"Skipped DingTalk user {user.user_id}: linked local user belongs to tenant "
                f"{existing_user.get('tenant_id')}, expected tenant {tenant_id}"
            )
            return None, False, False, False

        if existing_user is None and user.email:
            email_user = self.user_repo.get_user_by_email(user.email)
            if email_user:
                # Do NOT silently bind a DingTalk SSO identity to a pre-existing local
                # account just because the (unverified) email matches. DingTalk emails
                # are not confirmed, so auto-linking is a privilege-escalation footgun.
                if email_user.get("tenant_id") not in (None, tenant_id):
                    result.warnings.append(
                        f"Skipped DingTalk user {user.user_id}: email {user.email} is already "
                        f"owned by tenant {email_user.get('tenant_id')}"
                    )
                    return None, False, False, False
                result.warnings.append(
                    f"Skipped linking DingTalk user {user.user_id} to existing account "
                    f"{email_user.get('username')!r}: email {user.email} is unverified; "
                    f"provisioned a separate local user instead."
                )
                # Fall through to provisioning a distinct local user below.

        if existing_user is None:
            username = self._build_username(user.name, user.email, user.user_id)
            email = user.email or f"{user.user_id}@{DINGTALK_PLACEHOLDER_EMAIL_DOMAIN}"
            # Provision as inactive with an unusable password hash. The DingTalk SSO
            # identity link is what authorizes them; an active account with an empty
            # password would be a passwordless-login bypass.
            existing_user_id = self.user_repo.create_user(
                username=username,
                email=email,
                password_hash="!",
                role="user",
                is_active=False,
                tenant_id=tenant_id,
            )
            if existing_user_id is None:
                result.warnings.append(
                    f"Failed to create local user for DingTalk user {user.user_id}"
                )
                return None, False, False, False
            existing_user = self.user_repo.get_user_by_id(existing_user_id)
            created = True
        else:
            existing_user_id = int(existing_user["id"])

        if existing_user is None:
            return None, created, linked, updated

        current_email = str(existing_user.get("email") or "")
        next_email = user.email or current_email
        if (
            user.email
            and user.email != current_email
            and (
                not current_email or current_email.endswith(f"@{DINGTALK_PLACEHOLDER_EMAIL_DOMAIN}")
            )
        ):
            if self.user_repo.update_user(user_id=existing_user_id, email=user.email):
                updated = True

        provider_data = {
            "user_id": user.user_id,
            "name": user.name,
            "email": next_email,
            "department_ids": list(user.department_ids),
            "status": user.status,
            "synced_by": "dingtalk_org_sync",
            # Record the tenant this identity was synced under so the departed-user
            # deactivation pass can scope itself to the syncing tenant. Without this
            # marker a multi-tenant deployment would let tenant A's sync deactivate
            # tenant B's DingTalk identities (cross-tenant leak).
            "tenant_id": tenant_id,
            "synced_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
        self.sso_manager.link_identity(
            user_id=existing_user_id,
            provider_name=DINGTALK_PROVIDER_NAME,
            provider_user_id=user.user_id,
            provider_data=provider_data,
        )
        return existing_user_id, created, linked or not created, updated

    def _sync_memberships(
        self,
        expected_memberships: set[tuple[str, int]],
        synced_team_ids: set[str],
        result: DingTalkOrgSyncResult,
    ) -> None:
        """Reconcile team memberships for DingTalk-synced teams."""
        if not synced_team_ids:
            return

        # Scope the membership scan to the synced teams only (WP-1): a full
        # ``team_members`` scan filters every unrelated team's rows in Python.
        placeholders = ",".join("?" for _ in synced_team_ids)
        rows = self.db.fetch_all(
            f"SELECT team_id, user_id, role FROM team_members WHERE team_id IN ({placeholders})",
            tuple(synced_team_ids),
        )
        current_memberships = {(str(row["team_id"]), int(row["user_id"])) for row in rows}

        # Persist any manually-promoted role for members we are about to remove, so a
        # user who leaves then rejoins a synced department does not silently lose an
        # owner/leader role. Stored on the team's settings JSON (keyed by user_id).
        preserved_by_team = self._load_preserved_roles(synced_team_ids)
        for row in rows:
            team_id = str(row["team_id"])
            role = str(row.get("role") or "member")
            if role == "member":
                continue
            key = (team_id, int(row["user_id"]))
            if key in (current_memberships - expected_memberships):
                preserved_by_team.setdefault(team_id, {})[str(int(row["user_id"]))] = role

        to_remove = current_memberships - expected_memberships
        for team_id, user_id in sorted(to_remove):
            self.db.execute(
                "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
                (team_id, user_id),
            )
            result.memberships_removed += 1

        to_add = expected_memberships - current_memberships
        for team_id, user_id in sorted(to_add):
            local_user = self.user_repo.get_user_by_id(user_id)
            username = str(local_user.get("username") or "") if local_user else ""
            role = preserved_by_team.get(team_id, {}).get(str(user_id), "member")
            self.db.execute(
                """
                INSERT INTO team_members (team_id, user_id, username, role, joined_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    user_id,
                    username,
                    role,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                ),
            )
            result.memberships_added += 1

        # Persist any newly-stashed promoted roles so they survive across runs, and
        # clear a team's stash once every preserved member is back on the team.
        self._save_preserved_roles(preserved_by_team, synced_team_ids, expected_memberships)

    def _load_preserved_roles(self, synced_team_ids: set[str]) -> dict[str, dict[str, str]]:
        """Load the promoted-role stash (per-team, keyed by user_id) from team settings."""
        if not synced_team_ids:
            return {}
        placeholders = ",".join("?" for _ in synced_team_ids)
        rows = self.db.fetch_all(
            f"SELECT team_id, settings FROM teams WHERE team_id IN ({placeholders})",
            tuple(synced_team_ids),
        )
        stash: dict[str, dict[str, str]] = {}
        for row in rows:
            team_id = str(row["team_id"])
            settings_raw = row.get("settings")
            try:
                settings = (
                    json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
                )
            except (TypeError, json.JSONDecodeError):
                settings = {}
            if isinstance(settings, dict):
                preserved = settings.get("dingtalk_preserved_roles")
                if isinstance(preserved, dict) and preserved:
                    stash[team_id] = {
                        str(k): str(v) for k, v in preserved.items() if v and v != "member"
                    }
        return stash

    def _save_preserved_roles(
        self,
        preserved_by_team: dict[str, dict[str, str]],
        synced_team_ids: set[str],
        expected_memberships: set[tuple[str, int]],
    ) -> None:
        """Persist the promoted-role stash back onto team settings JSON.

        Drops a team's entry once all its preserved members are back in the expected
        membership set (so the stash doesn't grow unbounded).
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        for team_id in synced_team_ids:
            stash = dict(preserved_by_team.get(team_id, {}))
            rejoin = {str(uid) for (tid, uid) in expected_memberships if str(tid) == team_id}
            for uid in list(stash.keys()):
                if uid in rejoin:
                    stash.pop(uid, None)

            row = self.db.fetch_one("SELECT settings FROM teams WHERE team_id = ?", (team_id,))
            settings_raw = row.get("settings") if row else None
            try:
                settings = (
                    json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
                )
            except (TypeError, json.JSONDecodeError):
                settings = {}
            if not isinstance(settings, dict):
                settings = {}

            if stash:
                settings["dingtalk_preserved_roles"] = stash
            else:
                settings.pop("dingtalk_preserved_roles", None)

            settings_json = json.dumps(settings, ensure_ascii=False)
            self.db.execute(
                "UPDATE teams SET settings = ?, updated_at = ? WHERE team_id = ?",
                (settings_json, now, team_id),
            )

    def _deactivate_departed_users(
        self,
        tenant_id: int,
        seen_provider_user_ids: set[str],
        result: DingTalkOrgSyncResult,
    ) -> None:
        """Deactivate and unlink DingTalk-synced users absent from the current snapshot.

        DingTalk recycles userids after deletion, so a stale SSO identity row would
        let a recycled id re-resolve to the previous local account on a later sync.
        We detect previously-synced identities via the ``synced_by`` marker stored in
        ``provider_data`` and drop the ones whose provider_user_id is no longer seen.

        The pass is scoped to the syncing tenant: only identities whose
        ``provider_data.tenant_id`` matches (or, for legacy rows without that marker,
        whose linked local user belongs to this tenant) are eligible. Without this
        filter a multi-tenant deployment would let tenant A's sync deactivate tenant
        B's DingTalk identities.
        """
        rows = self.db.fetch_all(
            """
            SELECT user_id, provider_user_id, provider_data
            FROM sso_identities
            WHERE provider_name = ?
            """,
            (DINGTALK_PROVIDER_NAME,),
        )
        for row in rows:
            provider_user_id = str(row.get("provider_user_id") or "")
            if not provider_user_id or provider_user_id in seen_provider_user_ids:
                continue

            # Only touch identities this org sync created (avoid clobbering identities
            # linked by interactive SSO login flows).
            provider_data_raw = row.get("provider_data")
            try:
                provider_data = (
                    json.loads(provider_data_raw)
                    if isinstance(provider_data_raw, str)
                    else provider_data_raw
                )
            except (TypeError, json.JSONDecodeError):
                provider_data = {}
            if not isinstance(provider_data, dict):
                provider_data = {}
            if provider_data.get("synced_by") != "dingtalk_org_sync":
                continue

            local_user_id = row.get("user_id")
            local_user = (
                self.user_repo.get_user_by_id(int(local_user_id))
                if local_user_id is not None
                else None
            )

            # Scope deactivation to the syncing tenant. Prefer the tenant_id stamp
            # recorded in provider_data; fall back to the linked local user's
            # tenant_id for legacy identity rows that predate the stamp (and treat
            # a missing/None tenant as belonging to this tenant, preserving the
            # original lenient behavior for single-tenant deployments).
            identity_tenant_id = provider_data.get("tenant_id")
            if identity_tenant_id is None and local_user is not None:
                identity_tenant_id = local_user.get("tenant_id")
            if identity_tenant_id is not None:
                try:
                    identity_tenant_id = int(identity_tenant_id)
                except (TypeError, ValueError):
                    identity_tenant_id = None
            if identity_tenant_id is not None and identity_tenant_id != int(tenant_id):
                # Belongs to a different tenant; must not be touched here.
                continue

            if (
                local_user_id is not None
                and local_user is not None
                and local_user.get("tenant_id") in (None, tenant_id)
            ):
                self.user_repo.update_user(user_id=int(local_user_id), is_active=False)

            self.db.execute(
                "DELETE FROM sso_identities WHERE provider_name = ? AND provider_user_id = ?",
                (DINGTALK_PROVIDER_NAME, provider_user_id),
            )
            result.warnings.append(
                f"Deactivated and unlinked departed DingTalk user {provider_user_id}"
            )

    def _build_username(self, display_name: str, email: str | None, user_id: str) -> str:
        """Generate a stable, unique username for a synced DingTalk user.

        The check-then-insert loop is inherently racy across processes, so it is
        bounded: after ``_USERNAME_MAX_ATTEMPTS`` collisions we fall back to a
        uuid-suffixed candidate (effectively globally unique) instead of looping
        forever. ``create_user`` still swallows a last-hop unique-constraint
        violation (returns None) and ``_resolve_local_user`` warns-and-skips.
        """
        base = ""
        if email and "@" in email:
            base = email.split("@", 1)[0]
        if not base:
            base = display_name or f"dingtalk_{user_id[-8:]}"

        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._-").lower()
        if not slug:
            slug = f"dingtalk_{user_id[-8:]}"

        candidate = slug
        for counter in range(1, _USERNAME_MAX_ATTEMPTS):
            if not self.user_repo.get_user_by_username(candidate):
                return candidate
            candidate = f"{slug}_{counter}"
        # Exhausted bounded attempts: append a short uuid fragment so the final
        # candidate is unique with overwhelming probability (no unbounded loop).
        return f"{slug}_{uuid.uuid4().hex[:8]}"
