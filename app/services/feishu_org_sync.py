"""
Feishu organization sync service.

Synchronizes Feishu departments and users into local Open ACE teams, team
memberships, and SSO identity mappings.
"""

from __future__ import annotations

import json
import logging
import re
import threading
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

logger = logging.getLogger(__name__)

FEISHU_PROVIDER_NAME = "feishu"
FEISHU_ROOT_DEPARTMENT_ID = "0"
FEISHU_PLACEHOLDER_EMAIL_DOMAIN = "feishu.local"
# Provenance marker written to users.system_account for auto-provisioned users
# so they can be distinguished from human-created accounts.
FEISHU_PROVISIONED_SYSTEM_ACCOUNT = "feishu_org_sync"
# Stable key for the Postgres advisory lock guarding sync_org so that multiple
# workers cannot run concurrent syncs. Picked as a fixed constant so all
# workers contend on the same lock; fits in a signed int64.
_FEISHU_SYNC_LOCK_KEY = 88342611905720321
# Bound on the username-collision retry loop in _build_username; beyond this we
# fall back to a uuid-suffixed candidate rather than looping without limit.
_USERNAME_MAX_ATTEMPTS = 100
# Feishu API codes that mean the cached tenant_access_token is bad/expired ->
# invalidate the cached token and retry the call once with a freshly-exchanged
# token (WP-3 auth-fail retry). 99991661/99991664 = invalid/expired token,
# 99991663 = invalid app secret, 99991668 = token missing.
FEISHU_AUTH_ERROR_CODES = {99991661, 99991663, 99991664, 99991668}


class FeishuApiError(RuntimeError):
    """Raised when a Feishu API call returns a non-zero code.

    Carries only ``code``/``msg`` (never the raw payload) so transient errors are
    debuggable without echoing request bodies into logs/exceptions. Subclasses
    RuntimeError so existing ``pytest.raises(RuntimeError)`` still match.
    """

    def __init__(self, code: Any, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Feishu API request failed (code={code}): {msg}")


@dataclass
class _CachedToken:
    """A cached provider access token with an absolute expiry timestamp."""

    value: str
    expires_at: datetime


@dataclass
class FeishuDepartment:
    """Feishu department record used during synchronization."""

    department_id: str
    name: str
    parent_department_id: str | None = None
    leader_user_id: str | None = None
    order: int | None = None


@dataclass
class FeishuUser:
    """Feishu user record used during synchronization."""

    open_id: str
    name: str
    email: str | None = None
    department_ids: list[str] = field(default_factory=list)
    status: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeishuOrgSyncResult:
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


class FeishuOrgSyncService:
    """Synchronize Feishu org data into local users and collaboration teams."""

    _sync_lock = threading.Lock()
    _schedule_lock = threading.Lock()
    _last_scheduled_sync_at: datetime | None = None
    # Per-process start timestamp of the in-flight sync; used only as the
    # SQLite fallback for the max-runtime watchdog (single-process). On Postgres
    # the authoritative "is a sync running / for how long" signal is derived
    # cross-process from pg_locks + pg_stat_activity (see _org_sync_lock), so
    # this class var is NOT relied on for multi-worker detection.
    _sync_started_at: datetime | None = None
    # Class-level handle exposed for tests/observability; see
    # _acquire_sync_lock. WARNING: do NOT change this value once deployed. It
    # is the Postgres advisory-lock key shared by every worker process for
    # cross-process mutual exclusion; changing it would make old and new
    # workers lock on different keys and run overlapping syncs (the two
    # pg_try_advisory_lock calls would both succeed).
    _DB_SYNC_LOCK_KEY: int = _FEISHU_SYNC_LOCK_KEY

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
        # credentials for the in-flight sync (used by _request_json's auth-fail
        # retry). Token caching avoids re-exchanging every sync run (WP-3).
        self._token_cache: dict[str, _CachedToken] = {}
        self._active_app_id: str | None = None
        self._active_app_secret: str | None = None

    def sync_org(self, tenant_id: int | None = None) -> FeishuOrgSyncResult:
        """Run a full Feishu org sync."""
        config = self._get_feishu_config()
        app_id = str(config.get("app_id") or "").strip()
        app_secret = str(config.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret must be configured before syncing")

        # Issue #2179: Fail-Closed - 必须显式配置同步目标租户
        effective_tenant_id = tenant_id or config.get("org_sync_tenant_id")
        if effective_tenant_id is None:
            raise ValueError(
                "飞书同步未配置 org_sync_tenant_id。"
                "请在租户设置中配置同步目标租户。"
            )
        effective_tenant_id = int(effective_tenant_id)
        result = FeishuOrgSyncResult(
            tenant_id=effective_tenant_id,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        )

        # Publish the active credentials so _request_json can re-exchange the
        # tenant token on an auth-failure code (WP-3). Cleared in the finally
        # below so a later direct call (e.g. in tests) does not pick up stale creds.
        self._active_app_id = app_id
        self._active_app_secret = app_secret
        try:
            with self._acquire_sync_lock():
                # Record the in-flight start for the SQLite max-runtime watchdog
                # fallback (single-process). On Postgres the authoritative signal
                # is derived cross-process from pg_locks (see _org_sync_lock).
                self.__class__._sync_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                try:
                    self._ensure_supporting_tables()
                    token = self._get_tenant_access_token(app_id, app_secret)
                    departments, users = self._fetch_directory_snapshot(token)
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
                        seen_provider_user_ids.add(user.open_id)
                        user_id, created, linked, updated = self._resolve_local_user(
                            user=user,
                            tenant_id=effective_tenant_id,
                            result=result,
                        )
                        if user_id is None:
                            continue
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

                    self._reconcile_removed_users(
                        provider_name=FEISHU_PROVIDER_NAME,
                        seen_provider_user_ids=seen_provider_user_ids,
                        tenant_id=effective_tenant_id,
                        result=result,
                    )

                    result.finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    return result
                finally:
                    self.__class__._sync_started_at = None
        finally:
            self._active_app_id = None
            self._active_app_secret = None

    def maybe_sync_from_scheduler(self) -> FeishuOrgSyncResult | None:
        """Run scheduled sync when enabled, the interval has elapsed, and no other
        tick is already in flight.

        The schedule lock is acquired **non-blocking**: if another scheduler tick
        already holds it (a prior sync is still running, or ticks overlap), this
        tick skips entirely instead of queueing behind it. A max-runtime watchdog
        runs first: if the previous sync still appears to be running past the
        configured ceiling, it is logged and (when ``org_sync_auto_recover`` is
        opted in) forcibly released before this run proceeds.
        """
        config = self._get_feishu_config()
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
            "Feishu org sync appears hung (running ~%.0fs, max=%ss, pid=%s)",
            hold_seconds,
            max_runtime_seconds,
            pid,
        )
        if not auto_recover:
            return
        released = force_release_lock(self.db, self._DB_SYNC_LOCK_KEY)
        logger.warning(
            "Feishu org sync auto-recover force_release_lock(key=%s) -> %s",
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
        The lock is bound to a single dedicated connection that is held open
        across the entire critical section (the ``yield``) and only released
        afterwards; this is essential because ``Database`` commits per statement
        and returns pooled connections after every call, so a transaction-level
        lock (``pg_try_advisory_xact_lock``) would be freed the instant the
        ``fetch_one`` returned -- before the sync body even started. The
        in-process threading.Lock is still acquired first as a cheap fence to
        avoid needless DB round-trips within a single worker. On SQLite (and
        other non-Postgres backends) the advisory lock is unavailable, so the
        threading.Lock remains the only guard; SQLite deployments are
        single-process by nature.
        """
        with self._sync_lock:
            conn = None
            if self.db.is_postgresql:
                # Hold a dedicated connection for the whole critical section so
                # the session-level advisory lock stays bound to this backend
                # connection and is visible to every other worker's backend.
                conn = self.db.get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT pg_try_advisory_lock(%s)",
                        (self._DB_SYNC_LOCK_KEY,),
                    )
                    # Database.get_connection() returns a PgConnectionWrapper
                    # that forces cursor_factory=RealDictCursor, so fetchone()
                    # yields a dict (RealDictRow) keyed by the SELECT column
                    # name -- NOT a positional tuple. Indexing it as [0] raises
                    # KeyError: 0 and would crash every Postgres sync at lock
                    # acquisition. Read the scalar via its string key instead.
                    ok = cur.fetchone()["pg_try_advisory_lock"]
                    if not ok:
                        raise RuntimeError("Another Feishu org sync is already running")
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
                                "Failed to release Feishu org sync advisory "
                                "lock key=%s; the lock may leak and block "
                                "future syncs until the connection is closed",
                                self._DB_SYNC_LOCK_KEY,
                                exc_info=True,
                            )
                        try:
                            conn.commit()
                        except Exception:
                            logger.warning(
                                "Failed to commit after releasing Feishu org "
                                "sync advisory lock key=%s",
                                self._DB_SYNC_LOCK_KEY,
                                exc_info=True,
                            )
                finally:
                    if conn is not None:
                        release_postgresql_connection(conn)
            else:
                yield

    def _reconcile_removed_users(
        self,
        provider_name: str,
        seen_provider_user_ids: set[str],
        tenant_id: int,
        result: FeishuOrgSyncResult,
    ) -> None:
        """Deactivate Feishu-provisioned users who disappeared from the directory.

        Users previously linked via the Feishu SSO provider but absent from the
        current snapshot are considered departed; their local accounts are
        deactivated so they can no longer authenticate, shrinking the stale
        access surface. Identities for other providers are left untouched.
        """
        if not seen_provider_user_ids:
            # Nothing was seen this run; do not mass-deactivate on an empty
            # snapshot (likely an API outage) to avoid a destructive blunder.
            return

        rows = self.db.fetch_all(
            """
            SELECT si.provider_user_id, si.user_id
            FROM sso_identities si
            WHERE si.provider_name = ?
            """,
            (provider_name,),
        )
        for row in rows:
            provider_user_id = row.get("provider_user_id")
            if not provider_user_id or provider_user_id in seen_provider_user_ids:
                continue
            user_id = row.get("user_id")
            if user_id is None:
                continue
            existing = self.user_repo.get_user_by_id(int(user_id))
            if not existing:
                continue
            if existing.get("tenant_id") not in (None, tenant_id):
                continue
            if existing.get("is_active") in (False, 0):
                continue
            self.user_repo.update_user(user_id=int(user_id), is_active=False)
            result.warnings.append(
                f"Deactivated Feishu user {provider_user_id}: no longer present "
                f"in directory snapshot"
            )

    def _get_feishu_config(self) -> dict[str, Any]:
        """Load Feishu config from override or app config."""
        if self.config_override:
            if "feishu" in self.config_override:
                config = dict(self.config_override.get("feishu") or {})
            else:
                config = dict(self.config_override)
        else:
            config = {
                "app_id": get_config_value("feishu", "app_id", ""),
                "app_secret": get_config_value("feishu", "app_secret", ""),
                "org_sync_enabled": bool(get_config_value("feishu", "org_sync_enabled", False)),
                # Issue #2179: Fail-Closed - 不再默认为 1，必须显式配置
                "org_sync_tenant_id": get_config_value("feishu", "org_sync_tenant_id"),
                "org_sync_interval_minutes": int(
                    get_config_value("feishu", "org_sync_interval_minutes", 60) or 60
                ),
            }

        config.setdefault("org_sync_enabled", False)
        # Issue #2179: Fail-Closed - 不再设置默认值
        # config.setdefault("org_sync_tenant_id", 1)
        config.setdefault("org_sync_interval_minutes", 60)
        # Watchdog ceiling for a single sync run; a run exceeding this is treated
        # as hung. Opt-in auto-recover force-releases the advisory lock so the next
        # tick can proceed (default off -- destructive, only for known-stuck deploys).
        config.setdefault("org_sync_max_runtime_seconds", 1800)
        config.setdefault("org_sync_auto_recover", False)
        return config

    def _get_tenant_access_token(self, app_id: str, app_secret: str) -> str:
        """Exchange app credentials for a Feishu tenant access token, cached with a TTL.

        Feishu tenant tokens are valid for ~2 hours; re-exchanging on every sync
        run (and on every auth-fail retry) is wasteful. The token is cached per
        app_id until one minute before its real expiry (``expire``, in seconds).
        """
        cache_key = app_id
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cached = self._token_cache.get(cache_key)
        if cached and cached.expires_at > now:
            return cached.value

        response = self.http.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(f"Failed to get Feishu tenant access token: {data}")
        token = str(data["tenant_access_token"])

        # Feishu returns expire in seconds; cap it so a bogus value can't pin the
        # cache forever, and floor it so a near-zero value can't thrash.
        expire = data.get("expire") or 7200
        try:
            expire = int(expire)
        except (TypeError, ValueError):
            expire = 7200
        expire = min(max(expire, 60), 7200)
        # Refresh a minute before the real expiry so a call made near the deadline
        # doesn't race the token's remaining lifetime.
        self._token_cache[cache_key] = _CachedToken(
            value=token,
            expires_at=now + timedelta(seconds=max(expire - 60, 30)),
        )
        return token

    def _invalidate_tenant_access_token(self, app_id: str) -> None:
        """Drop the cached tenant token so the next call re-exchanges it (auth-fail)."""
        self._token_cache.pop(app_id, None)

    def _fetch_directory_snapshot(
        self, token: str
    ) -> tuple[list[FeishuDepartment], list[FeishuUser]]:
        """Recursively fetch departments and users starting from the root department."""
        departments: dict[str, FeishuDepartment] = {}
        users: dict[str, FeishuUser] = {}

        queue: deque[str] = deque([FEISHU_ROOT_DEPARTMENT_ID])
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

            if current_department_id == FEISHU_ROOT_DEPARTMENT_ID:
                continue

            direct_users = self._fetch_department_users(token, current_department_id)
            for user in direct_users:
                existing = users.get(user.open_id)
                if existing is None:
                    users[user.open_id] = user
                    continue

                merged_departments = set(existing.department_ids)
                merged_departments.update(user.department_ids)
                existing.department_ids = sorted(merged_departments)
                if not existing.email and user.email:
                    existing.email = user.email
                if user.name:
                    existing.name = user.name
                if user.status:
                    existing.status = user.status

        sorted_departments = sorted(
            departments.values(),
            key=lambda d: (
                d.parent_department_id or "",
                d.order if d.order is not None else 0,
                d.name.lower(),
            ),
        )
        sorted_users = sorted(users.values(), key=lambda u: (u.name.lower(), u.open_id))
        return sorted_departments, sorted_users

    def _fetch_child_departments(self, token: str, department_id: str) -> list[FeishuDepartment]:
        """Fetch immediate child departments for a Feishu department."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params = {
                "department_id_type": "open_department_id",
                "page_size": 100,
            }
            if page_token:
                params["page_token"] = page_token

            data = self._request_json(
                method="GET",
                url=f"https://open.feishu.cn/open-apis/contact/v3/departments/{department_id}/children",
                token=token,
                params=params,
            )
            items.extend(self._extract_items(data, ("items", "departments")))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break

        departments: list[FeishuDepartment] = []
        for item in items:
            department_id_value = item.get("open_department_id") or item.get("department_id")
            if not department_id_value:
                continue

            parent_ids = item.get("parent_department_ids") or []
            parent_department_id = None
            if isinstance(parent_ids, list) and parent_ids:
                parent_department_id = str(parent_ids[0])
            elif item.get("parent_department_id"):
                parent_department_id = str(item["parent_department_id"])
            elif department_id != FEISHU_ROOT_DEPARTMENT_ID:
                parent_department_id = department_id

            departments.append(
                FeishuDepartment(
                    department_id=str(department_id_value),
                    name=str(item.get("name") or department_id_value),
                    parent_department_id=parent_department_id,
                    leader_user_id=(
                        str(item.get("leader_user_id"))
                        if item.get("leader_user_id") is not None
                        else None
                    ),
                    order=int(item["order"]) if item.get("order") is not None else None,
                )
            )

        return departments

    def _fetch_department_users(self, token: str, department_id: str) -> list[FeishuUser]:
        """Fetch users directly under a Feishu department."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params = {
                "department_id": department_id,
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
                "page_size": 100,
            }
            if page_token:
                params["page_token"] = page_token

            data = self._request_json(
                method="GET",
                url="https://open.feishu.cn/open-apis/contact/v3/users/find_by_department",
                token=token,
                params=params,
            )
            items.extend(self._extract_items(data, ("items", "users")))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break

        users: list[FeishuUser] = []
        for item in items:
            open_id = item.get("open_id") or item.get("user_id")
            if not open_id:
                continue

            department_ids = item.get("department_ids") or [department_id]
            if not isinstance(department_ids, list):
                department_ids = [department_id]
            raw_status = item.get("status")
            status = cast("dict[str, Any]", raw_status) if isinstance(raw_status, dict) else {}

            users.append(
                FeishuUser(
                    open_id=str(open_id),
                    name=str(item.get("name") or open_id),
                    email=str(item.get("email")) if item.get("email") else None,
                    department_ids=[str(dep_id) for dep_id in department_ids if dep_id],
                    status=status,
                )
            )

        return users

    def _request_json(
        self,
        method: str,
        url: str,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a Feishu JSON API and return its data payload.

        On an auth-failure code (bad/expired token) the cached tenant token is
        invalidated and the call retried once with a freshly-exchanged token --
        but only when active credentials are known (a real sync is driving the
        call) AND a token was sent (the token-exchange endpoint itself takes no
        Authorization header, so an auth code there is not a stale-token issue).
        """
        return self._request_json_once(method, url, token, params, json_payload, retried=False)

    def _request_json_once(
        self,
        method: str,
        url: str,
        token: str | None,
        params: dict[str, Any] | None,
        json_payload: dict[str, Any] | None,
        retried: bool,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = self.http.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_payload,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        code = payload.get("code")
        if code == 0:
            return payload.get("data") or {}
        if (
            not retried
            and code in FEISHU_AUTH_ERROR_CODES
            and token
            and self._active_app_id
            and self._active_app_secret
        ):
            self._invalidate_tenant_access_token(self._active_app_id)
            fresh = self._get_tenant_access_token(self._active_app_id, self._active_app_secret)
            return self._request_json_once(method, url, fresh, params, json_payload, retried=True)
        # Surface only code/msg (never the whole payload) so transient errors are
        # debuggable without echoing request bodies into logs/exceptions.
        msg = payload.get("msg") or payload.get("message") or "unknown error"
        raise FeishuApiError(code, msg)

    @staticmethod
    def _extract_items(data: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        """Pull the first list-valued key from a Feishu API payload."""
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _upsert_department_team(
        self,
        department: FeishuDepartment,
        existing_teams: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[str, bool]:
        """Create or update a local team representing a Feishu department.

        ``existing_teams`` is the per-run synced-team index (built once by
        ``_load_synced_teams`` and threaded through the department loop). When a
        new team is created it is appended to that dict so later iterations in
        the same run -- and the membership reconcile -- see it without re-scanning
        the whole ``teams`` table (WP-1).
        """
        if existing_teams is None:
            existing_teams = self._load_synced_teams()
        existing = existing_teams.get(department.department_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        settings = {
            "sync_source": FEISHU_PROVIDER_NAME,
            "feishu_department_id": department.department_id,
            "feishu_parent_department_id": department.parent_department_id,
            "feishu_leader_user_id": department.leader_user_id,
        }
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
        """Return existing teams that are owned by Feishu org sync.

        Issue #2174 F1/F5: Optimized with WHERE clause and database index
        to avoid full table scan.
        """
        # Use WHERE clause to filter at database level
        # This leverages the idx_teams_feishu_sync partial index
        query = """
            SELECT team_id, name, settings
            FROM teams
            WHERE settings->>'sync_source' = ?
        """
        rows = self.db.fetch_all(query, (FEISHU_PROVIDER_NAME,))

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
            # Extract department_id (already filtered by sync_source at DB level)
            department_id = settings.get("feishu_department_id")
            if department_id:
                synced[str(department_id)] = row

        return synced

    def _resolve_local_user(
        self,
        user: FeishuUser,
        tenant_id: int,
        result: FeishuOrgSyncResult,
    ) -> tuple[int | None, bool, bool, bool]:
        """Resolve or provision a local user for a Feishu user."""
        existing_user_id = self.sso_manager.get_user_by_sso_identity(
            FEISHU_PROVIDER_NAME,
            user.open_id,
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
                f"Skipped Feishu user {user.open_id}: linked local user belongs to tenant "
                f"{existing_user.get('tenant_id')}, expected tenant {tenant_id}"
            )
            return None, False, False, False

        if existing_user is None and user.email:
            email_user = self.user_repo.get_user_by_email(user.email)
            if email_user:
                # SECURITY: Feishu emails are NOT verified, so we must not bind
                # an SSO identity onto an unrelated pre-existing password
                # account (account takeover via unverified email). Skip
                # auto-linking; a fresh local user is provisioned below. We do
                # surface a warning when the email is already claimed by another
                # tenant so the operator can investigate the collision.
                if email_user.get("tenant_id") not in (None, tenant_id):
                    result.warnings.append(
                        f"Skipped Feishu user {user.open_id}: email {user.email} is already "
                        f"owned by tenant {email_user.get('tenant_id')}"
                    )
                    return None, False, False, False

        if existing_user is None:
            username = self._build_username(user.name, user.email, user.open_id)
            email = user.email or f"{user.open_id}@{FEISHU_PLACEHOLDER_EMAIL_DOMAIN}"
            existing_user_id = self.user_repo.create_user(
                username=username,
                email=email,
                password_hash="",
                role="user",
                # Provision inactive until the account is explicitly activated
                # (e.g. via a verified Feishu SSO login flow). Auto-provisioned
                # users have no password and must not be usable for login out
                # of the box.
                is_active=False,
                system_account=FEISHU_PROVISIONED_SYSTEM_ACCOUNT,
                tenant_id=tenant_id,
            )
            if existing_user_id is None:
                result.warnings.append(
                    f"Failed to create local user for Feishu user {user.open_id}"
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
            and (not current_email or current_email.endswith(f"@{FEISHU_PLACEHOLDER_EMAIL_DOMAIN}"))
        ):
            if self.user_repo.update_user(user_id=existing_user_id, email=user.email):
                updated = True

        provider_data = {
            "open_id": user.open_id,
            "name": user.name,
            "email": next_email,
            "department_ids": list(user.department_ids),
            "status": user.status,
            "synced_by": "feishu_org_sync",
            "synced_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
        self.sso_manager.link_identity(
            user_id=existing_user_id,
            provider_name=FEISHU_PROVIDER_NAME,
            provider_user_id=user.open_id,
            provider_data=provider_data,
        )
        return existing_user_id, created, linked or not created, updated

    def _sync_memberships(
        self,
        expected_memberships: set[tuple[str, int]],
        synced_team_ids: set[str],
        result: FeishuOrgSyncResult,
    ) -> None:
        """Reconcile team memberships for Feishu-synced teams."""
        if not synced_team_ids:
            return

        # Scope the membership scan to the synced teams only (WP-1): a full
        # ``team_members`` scan filters every unrelated team's rows in Python.
        placeholders = ",".join("?" for _ in synced_team_ids)
        rows = self.db.fetch_all(
            f"SELECT team_id, user_id, role FROM team_members WHERE team_id IN ({placeholders})",
            tuple(synced_team_ids),
        )
        role_by_membership: dict[tuple[str, int], str] = {}
        current_memberships: set[tuple[str, int]] = set()
        for row in rows:
            key = (str(row["team_id"]), int(row["user_id"]))
            current_memberships.add(key)
            role_by_membership[key] = str(row.get("role") or "member")

        to_remove = current_memberships - expected_memberships
        # Preserve any manually-promoted role (owner/leader/...) so that when a
        # user is removed from one synced team and (re)added to another within
        # the same run, the promotion is not silently downgraded to 'member'.
        # Promotions are tracked per user (a leader is a leader of the person,
        # not of a single row) and restored when that user is re-inserted.
        preserved_roles: dict[int, str] = {}
        for team_id, user_id in sorted(to_remove):
            prior_role = role_by_membership.get((team_id, user_id))
            if prior_role and prior_role != "member":
                preserved_roles.setdefault(user_id, prior_role)
            self.db.execute(
                "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
                (team_id, user_id),
            )
            result.memberships_removed += 1

        to_add = expected_memberships - current_memberships
        for team_id, user_id in sorted(to_add):
            local_user = self.user_repo.get_user_by_id(user_id)
            username = str(local_user.get("username") or "") if local_user else ""
            role = preserved_roles.get(user_id, "member")
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

    def _build_username(self, display_name: str, email: str | None, open_id: str) -> str:
        """Generate a stable, unique username for a synced Feishu user.

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
            base = display_name or f"feishu_{open_id[-8:]}"

        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._-").lower()
        if not slug:
            slug = f"feishu_{open_id[-8:]}"

        candidate = slug
        for counter in range(1, _USERNAME_MAX_ATTEMPTS):
            if not self.user_repo.get_user_by_username(candidate):
                return candidate
            candidate = f"{slug}_{counter}"
        # Exhausted bounded attempts: append a short uuid fragment so the final
        # candidate is unique with overwhelming probability (no unbounded loop).
        return f"{slug}_{uuid.uuid4().hex[:8]}"
