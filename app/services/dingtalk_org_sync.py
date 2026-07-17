"""DingTalk organization sync service.

Synchronizes DingTalk departments and users into local Open ACE teams, team
memberships, and SSO identity mappings.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import requests

from app.modules.sso.manager import SSOManager
from app.modules.workspace.collaboration import CollaborationManager
from app.repositories.database import Database
from app.repositories.user_repo import UserRepository
from app.utils.config import get_config_value

logger = logging.getLogger(__name__)

DINGTALK_PROVIDER_NAME = "dingtalk"
DINGTALK_ROOT_DEPARTMENT_ID = "1"
DINGTALK_PLACEHOLDER_EMAIL_DOMAIN = "dingtalk.local"


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

        with self._sync_lock:
            self._ensure_supporting_tables()
            token = self._get_access_token(app_key, app_secret)
            departments, users = self._fetch_directory_snapshot(token, root_department_id)
            result.departments_seen = len(departments)
            result.users_seen = len(users)

            team_ids_by_department: dict[str, str] = {}
            for department in departments:
                team_id, created = self._upsert_department_team(department)
                team_ids_by_department[department.department_id] = team_id
                if created:
                    result.teams_created += 1
                else:
                    result.teams_updated += 1

            expected_memberships: set[tuple[str, int]] = set()
            for user in users:
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

            result.finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            return result

    def maybe_sync_from_scheduler(self) -> DingTalkOrgSyncResult | None:
        """Run scheduled sync when enabled and the interval has elapsed."""
        config = self._get_dingtalk_config()
        if not bool(config.get("org_sync_enabled", False)):
            return None

        interval_minutes = max(int(config.get("org_sync_interval_minutes") or 60), 5)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self._schedule_lock:
            if self._last_scheduled_sync_at and (now - self._last_scheduled_sync_at) < timedelta(
                minutes=interval_minutes
            ):
                return None

            result = self.sync_org(tenant_id=config.get("org_sync_tenant_id"))
            self.__class__._last_scheduled_sync_at = now
            return result

    def _ensure_supporting_tables(self) -> None:
        """Ensure dependent tables exist before syncing."""
        self.sso_manager._ensure_tables()
        self.collaboration_manager._ensure_tables()

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
        return config

    def _get_access_token(self, app_key: str, app_secret: str) -> str:
        """Exchange app credentials for a DingTalk access token."""
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
        return str(token)

    def _fetch_directory_snapshot(
        self, token: str, root_department_id: str
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

            direct_users = self._fetch_department_users(token, current_department_id)
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

    def _fetch_department_users(self, token: str, department_id: str) -> list[DingTalkUser]:
        """Fetch users directly under a DingTalk department."""
        user_ids: list[str] = []
        cursor = 0

        while True:
            data = self._request_oapi(
                "https://oapi.dingtalk.com/topapi/user/listid",
                token=token,
                json_payload={
                    "dept_id": self._coerce_dept_id(department_id),
                    "cursor": cursor,
                    "size": 100,
                },
            )
            result = data.get("result") if isinstance(data.get("result"), dict) else data
            if not isinstance(result, dict):
                result = {}
            values = result.get("userid_list") or result.get("userids") or []
            if isinstance(values, list):
                user_ids.extend(str(value) for value in values if value)
            if not result.get("has_more"):
                break
            next_cursor = result.get("next_cursor")
            if next_cursor is None:
                break
            cursor = int(next_cursor)

        users: list[DingTalkUser] = []
        for user_id in sorted(set(user_ids)):
            user_info = self._fetch_user_info(token, user_id)
            if not user_info:
                continue

            department_ids = user_info.get("dept_id_list") or user_info.get("dept_id_list_ext")
            if not isinstance(department_ids, list):
                department_ids = [department_id]

            email = user_info.get("email") or user_info.get("org_email")
            status = {
                "active": user_info.get("active"),
                "admin": user_info.get("admin"),
                "boss": user_info.get("boss"),
            }

            users.append(
                DingTalkUser(
                    user_id=user_id,
                    name=str(
                        user_info.get("name")
                        or user_info.get("nick")
                        or user_info.get("nickname")
                        or user_id
                    ),
                    email=str(email) if email else None,
                    department_ids=[str(dep_id) for dep_id in department_ids if dep_id],
                    status={k: v for k, v in status.items() if v is not None},
                )
            )

        return users

    def _fetch_user_info(self, token: str, user_id: str) -> dict[str, Any]:
        """Fetch a DingTalk user detail record."""
        data = self._request_oapi(
            "https://oapi.dingtalk.com/topapi/v2/user/get",
            token=token,
            json_payload={"userid": user_id},
        )
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def _request_oapi(
        self,
        url: str,
        token: str,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a DingTalk oapi endpoint and return the response payload."""
        response = self.http.post(
            url,
            params={"access_token": token},
            json=json_payload or {},
            timeout=15,
        )
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        if payload.get("errcode", 0) != 0:
            raise RuntimeError(f"DingTalk API request failed: {payload}")
        return payload

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

    def _upsert_department_team(self, department: DingTalkDepartment) -> tuple[str, bool]:
        """Create or update a local team representing a DingTalk department."""
        existing_teams = self._load_synced_teams()
        existing = existing_teams.get(department.department_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        settings = {
            "sync_source": DINGTALK_PROVIDER_NAME,
            "dingtalk_department_id": department.department_id,
            "dingtalk_parent_department_id": department.parent_department_id,
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
                if email_user.get("tenant_id") not in (None, tenant_id):
                    result.warnings.append(
                        f"Skipped DingTalk user {user.user_id}: email {user.email} is already "
                        f"owned by tenant {email_user.get('tenant_id')}"
                    )
                    return None, False, False, False
                existing_user = email_user
                existing_user_id = int(email_user["id"])
                linked = True

        if existing_user is None:
            username = self._build_username(user.name, user.email, user.user_id)
            email = user.email or f"{user.user_id}@{DINGTALK_PLACEHOLDER_EMAIL_DOMAIN}"
            existing_user_id = self.user_repo.create_user(
                username=username,
                email=email,
                password_hash="",
                role="user",
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

        all_rows = self.db.fetch_all("SELECT team_id, user_id FROM team_members")
        current_memberships = {
            (str(row["team_id"]), int(row["user_id"]))
            for row in all_rows
            if str(row["team_id"]) in synced_team_ids
        }

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
            self.db.execute(
                """
                INSERT INTO team_members (team_id, user_id, username, role, joined_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    user_id,
                    username,
                    "member",
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                ),
            )
            result.memberships_added += 1

    def _build_username(self, display_name: str, email: str | None, user_id: str) -> str:
        """Generate a stable, unique username for a synced DingTalk user."""
        base = ""
        if email and "@" in email:
            base = email.split("@", 1)[0]
        if not base:
            base = display_name or f"dingtalk_{user_id[-8:]}"

        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._-").lower()
        if not slug:
            slug = f"dingtalk_{user_id[-8:]}"

        candidate = slug
        counter = 1
        while self.user_repo.get_user_by_username(candidate):
            candidate = f"{slug}_{counter}"
            counter += 1
        return candidate
