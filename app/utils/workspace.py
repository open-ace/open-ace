"""Workspace base-directory resolution.

Single source of truth for the ``WORKSPACE_BASE_DIR`` env var so the directory
browser's allowed-prefix logic is consistent across routes (fs / admin /
workspace). When the env var is unset, the default is the current user's home
directory (e.g. ``/Users/<user>`` on macOS, ``/home/<user>`` on Linux) so the
browser works out of the box on any platform; Docker/server deployments set
the env explicitly (e.g. ``/workspace``).
"""

import logging
import os
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "get_workspace_base_dir",
    "get_workspace_base_dirs",
    "run_as_root_if_needed",
    "ensure_system_user",
    "get_recorded_system_uid",
    "record_system_uid",
    "ensure_user_workspace",
    "SHARED_GROUP_NAME",
    "SHARED_TENANT_GROUP_PREFIX",
    "shared_tenant_group_name",
    "ensure_shared_group",
    "ensure_shared_namespace_root",
    "add_user_to_shared_group",
    "remove_user_from_shared_group",
    "setup_shared_project_permissions",
    "revoke_shared_project_access",
    "estimate_file_count_fast",
    "setup_permissions_with_depth_limit",
    "verify_setgid_support",
    "get_user_project_active_sessions",
]

# ============================================================================
# Shared-project OS groups (Issue #2730 origin, tenant-scoped since #3396)
# ============================================================================
# Two-tier model:
#
# - ``openace-shared`` (global, unchanged): membership ONLY grants the right
#   to create a project directory inside the sticky shared-namespace root
#   ``<base>/shared`` (root:openace-shared 3770, sticky bit from #3389,
#   others bits dropped 3775->3770). It is no longer the group-owner of any
#   project CONTENT.
#
# - ``openace-shared-<tenant_id>`` (per tenant): group-owner of the tenant's
#   shared project directories, mode 2770 (dirs) / 660 (files) — setgid kept
#   for group inheritance, "others" bits DROPPED so accounts of other
#   tenants (who are global-group members for namespace creation) get
#   EACCES on the project content. Tenant-less users (platform admins,
#   tenant_id NULL) map to ``openace-shared-0``; tenant ids start at 1 so
#   the pseudo-id never collides.
SHARED_GROUP_NAME = "openace-shared"
SHARED_TENANT_GROUP_PREFIX = "openace-shared-"
# Linux group names are capped at 31 chars ([a-z_][a-z0-9_-]*); the prefix is
# 15 chars, leaving 16 digits of tenant-id headroom.
_MAX_LINUX_GROUP_NAME_LEN = 31


class _TenantUnresolved:
    """Sentinel: the caller could NOT determine the user's tenant.

    PR #3402 review (Issue #3396): ``tenant_id=None`` legitimately means
    "platform admin" (enrolled in the ``openace-shared-0`` pseudo-tenant's
    content group), so a tenant LOOKUP FAILURE must never be expressed as
    ``None`` — that fails OPEN into the platform admins' shared content.
    Passing ``TENANT_UNRESOLVED`` enrolls the account in the global
    namespace-creation group ONLY and skips the tenant content group
    entirely (fail-closed); the caller logs why. A later call that CAN
    resolve the tenant completes the enrollment.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "TENANT_UNRESOLVED"


TENANT_UNRESOLVED = _TenantUnresolved()
# Public annotation alias: enrollment helpers accept a real tenant id, the
# platform-admin None, or the unresolved-lookup sentinel.
TenantIdOrUnresolved = int | None | _TenantUnresolved

# Wrapper script paths (Issue #1855 + #2181)
OPENACE_USERADD_WRAPPER = "/usr/local/bin/openace-useradd"
OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
OPENACE_CAT_WRAPPER = "/usr/local/bin/openace-cat"
OPENACE_MKDIR_WRAPPER = "/usr/local/bin/openace-mkdir"
# Secure rm wrapper (Issue #2181): validates path, user, owner, and dangerous options
OPENACE_RM_WRAPPER = "/usr/local/bin/openace-rm"
# Cross-user file write wrapper (Issue #1916): used by the upload endpoint in
# Package non-root multi-user mode to write into a user's 0700 home directory.
# cp/tee/mv are NOT in the sudoers OPENACE_UTILS whitelist, so uploads delegate
# through this root-authorized wrapper (which drops to the target user via
# runuser). Docker multi-user runs as root and never hits this path.
OPENACE_WRITE_AS_WRAPPER = "/usr/local/bin/openace-write-as"


def _is_wrapper_available(wrapper_path: str) -> bool:
    """Check if a security wrapper script is available and executable."""
    return os.path.isfile(wrapper_path) and os.access(wrapper_path, os.X_OK)


def get_workspace_base_dir() -> str:
    """Get the workspace base directory. Configurable via WORKSPACE_BASE_DIR env var.

    Falls back to ``str(Path.home())`` when the env var is unset or empty, so
    the directory browser works on macOS (``/Users/<user>``) and Linux
    (``/home/<user>``) alike. Explicit env values — e.g. Docker's ``/workspace``
    — always win.
    """
    return os.environ.get("WORKSPACE_BASE_DIR") or str(Path.home())


def get_workspace_base_dirs() -> list[str]:
    """Get list of workspace base directories. Supports comma-separated WORKSPACE_BASE_DIR.

    Example: ``WORKSPACE_BASE_DIR=/workspace,/tools,/projects``
    Returns: ``['/workspace', '/tools', '/projects']``

    When unset, defaults to ``[str(Path.home())]`` — see
    :func:`get_workspace_base_dir`.
    """
    base_dir = get_workspace_base_dir()
    return [d.strip() for d in base_dir.split(",") if d.strip()]


def run_as_root_if_needed(cmd: list) -> subprocess.CompletedProcess:
    """以 root 权限执行命令（用于 useradd/chown/mkdir 等系统管理操作）。

    当服务以非 root 用户运行时（如 Package 版 ivyent），需要通过 sudo 执行
    需要 root 权限的系统命令。

    注意：此函数仅用于需要 root 权限的命令（useradd, chown, mkdir）。
    id 命令不应使用此函数，因为 id 命令任何用户都可以执行。

    Args:
        cmd: 命令列表，如 ["useradd", "-m", "-s", "/bin/bash", "username"]

    Returns:
        subprocess.CompletedProcess 结果。
    """
    if os.geteuid() != 0:
        return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, cwd="/tmp")
    return subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp")


def _is_docker_multi_user_mode() -> bool:
    """Check if running in Docker multi-user mode.

    Docker multi-user mode is indicated by:
    1. WORKSPACE_BASE_DIR is set (typically /workspace)
    2. Process is running as root (can create system users)

    Returns True if both conditions are met.
    """
    base_dir = os.environ.get("WORKSPACE_BASE_DIR", "")
    # Docker sets WORKSPACE_BASE_DIR=/workspace, Package version uses default (Path.home())
    is_docker_workspace = base_dir == "/workspace"
    # In Docker container, typically running as root
    is_root = os.geteuid() == 0
    return is_docker_workspace and is_root


def _lookup_uid_owner(uid: int) -> str | None:
    """Return the account NAME currently owning ``uid`` (None if unassigned).

    Issue #3390: reverse NSS lookup used by the uid-collision guard. Kept as
    a tiny separate function so tests can stub the passwd database without
    faking subprocess plumbing. Uses the ``pwd`` module (same NSS source as
    ``id``/``getent`` in a container); imported lazily because the module
    does not exist on Windows.
    """
    import pwd

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def get_recorded_system_uid(system_account: str) -> int | None:
    """Read the pinned OS uid for ``system_account`` from the users table.

    Issue #3390: scoped to ACTIVE, non-deleted rows — the same predicate as
    the (non-unique) partial index ``idx_users_system_account``; admin
    creation routes keep active system_accounts unique in practice, but
    nothing enforces it at the DB level, so multiple active rows sharing a
    name would share the pin (they share the OS account). A DEACTIVATED row
    keeps its recorded pin untouched — that is exactly what the entrypoint's
    placeholder accounts restore.
    """
    try:
        from app.repositories.database import adapt_sql, get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    "SELECT system_uid FROM users "
                    "WHERE system_account = ? AND deleted_at IS NULL AND is_active = true"
                ),
                (system_account,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            # Review on #3390 (🔴): PostgreSQL connections are wrapped with
            # cursor_factory=RealDictCursor (app/repositories/database.py),
            # so fetchone() yields a dict — positional row[0] raises KeyError
            # there and the except below swallowed it, making the pin
            # permanently unreadable on PG while record_system_uid kept
            # OVERWRITING it with auto-assigned uids. Dual-shape access,
            # same pattern as session_manager._count_session_messages
            # (sqlite tuple / sqlite Row / PG RealDictRow all work).
            value = row["system_uid"] if isinstance(row, dict) else row[0]
            return int(value) if value is not None else None
    except Exception as e:
        # Read failure must not block account creation — an unpinned useradd
        # is the pre-#3390 behavior, strictly no worse. The entrypoint sync
        # records the assigned uid afterwards, closing the gap next recreate.
        logger.warning(f"Could not read recorded system_uid for {system_account}: {e}")
        return None


def record_system_uid(system_account: str, uid: int) -> bool:
    """Persist the OS uid assigned to ``system_account`` back to its user row.

    Issue #3390: the pin is what makes uids stable across container
    recreation. Same scoping as ``get_recorded_system_uid`` (active,
    non-deleted rows only). Failures are logged, never raised — a missed
    record degrades to the legacy unpinned behavior until the next sync.
    """
    try:
        from app.repositories.database import adapt_sql, get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    "UPDATE users SET system_uid = ? "
                    "WHERE system_account = ? AND deleted_at IS NULL AND is_active = true"
                ),
                (uid, system_account),
            )
            conn.commit()
            return bool(cursor.rowcount > 0)
    except Exception as e:
        logger.warning(f"Could not record system_uid {uid} for {system_account}: {e}")
        return False


def _recorded_pin_uids(exclude_account: str) -> set[int] | None:
    """ALL uids recorded in the users table, regardless of account state.

    The auto-assign path must treat these as taken even when NO OS account
    in the current container carries them — e.g. the boot sync failed
    (#3399) and the pinned accounts are exactly the ones missing from
    /etc/passwd, so a plain useradd could otherwise land on a recorded pin,
    numerically own that user's directories, and pin the stolen uid to the
    new account. Review round 3 (PR #3400): the query deliberately has NO
    is_active/deleted_at filter — a DEACTIVATED or soft-deleted user's pin
    is precisely the uid the entrypoint's nologin placeholder reserves, and
    the inheritance boundary it protects; #3399-shaped sync failures make
    exactly those accounts absent from /etc/passwd, so they must be in the
    exclusion set or a new account steals the reserved uid on the app side.
    (get_recorded_system_uid stays active-scoped: it answers "which uid
    should THIS account use", where a deactivated row's pin belongs to its
    placeholder, not to a fresh assignment.) ``exclude_account`` drops the
    caller's own row (its pin, if any, was already consumed as the explicit
    ``-u``). Returns None on read failure so callers can fail soft.
    """
    try:
        from app.repositories.database import adapt_sql, get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    "SELECT system_uid FROM users "
                    "WHERE system_uid IS NOT NULL AND system_account != ?"
                ),
                (exclude_account,),
            )
            pins: set[int] = set()
            for row in cursor.fetchall() or []:
                # Dual row shape (PG RealDictCursor vs sqlite tuple) — see
                # get_recorded_system_uid.
                value = row["system_uid"] if isinstance(row, dict) else row[0]
                if value is not None:
                    pins.add(int(value))
            return pins
    except Exception as e:
        logger.warning(f"Could not read recorded system_uid pins: {e}")
        return None


def _passwd_uids() -> set[int] | None:
    """Every uid resolvable in /etc/passwd (None if unreadable)."""
    try:
        import pwd

        return {entry.pw_uid for entry in pwd.getpwall()}
    except Exception as e:
        logger.warning(f"Could not enumerate passwd uids: {e}")
        return None


def _pick_auto_uid(system_account: str) -> int | None:
    """Pick a free uid (>= 1001) for an unpinned account (review on #3390 ⚪).

    The exclusion set combines /etc/passwd AND the DB's recorded active pins
    (minus this account's own row), so an auto-assign can no longer steal a
    pin whose OS account is missing from this container. Returns None to
    fail soft (either source unreadable) — the caller then runs the plain
    unpinned ``useradd`` exactly as before this review fix.
    """
    recorded = _recorded_pin_uids(system_account)
    passwd = _passwd_uids()
    if recorded is None or passwd is None:
        return None
    taken = passwd | recorded
    # < 1000 is the reserved system range (rejected above); 1000 is the
    # image's default `open-ace` account.
    uid = 1001
    while uid in taken:
        uid += 1
    return uid


def _actual_uid_of(system_account: str) -> int | None:
    """Read the account's current numeric uid via ``id -u`` (None on failure)."""
    result = subprocess.run(["id", "-u", system_account], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _ensure_login_shell(system_account: str) -> None:
    """Issue #3390: upgrade a placeholder (nologin) account back to bash.

    On container recreation the entrypoint keeps nologin placeholder accounts
    for deactivated users; when such a user is REACTIVATED, their existing
    account (phase 1 re-creates it with the pinned uid, but the account may
    already exist from the placeholder pass) must get a real login shell
    back. Best-effort: failures are logged, never raised.
    """
    import pwd

    try:
        if pwd.getpwnam(system_account).pw_shell != "/usr/sbin/nologin":
            return
    except KeyError:
        return
    result = run_as_root_if_needed(["usermod", "-s", "/bin/bash", system_account])
    if result.returncode == 0:
        logger.info(f"Upgraded placeholder account {system_account} to /bin/bash (#3390)")
    else:
        logger.warning(f"Could not restore login shell for {system_account}: {result.stderr}")


def ensure_system_user(
    system_account: str, uid: int | None = None, tenant_id: TenantIdOrUnresolved = None
) -> bool:
    """确保系统用户存在，创建工作目录。

    Behavior differs by deployment mode:
    - Docker multi-user mode: Creates system user + workspace + .qwen dirs
    - Package single-user mode: Skips system user creation (returns True)

    此函数用于 Package 版 multi-user mode，当服务以非 root 用户运行时，
    通过 sudo 执行 useradd 和 chown 命令。

    Issue #3390 (uid pinning): when ``uid`` is not given explicitly, the
    pinned uid recorded in ``users.system_uid`` is used, and whatever uid
    the account ends up with is recorded back so it survives container
    recreation. A pinned/explicit uid already owned by a DIFFERENT account
    name fails loudly (logged error) instead of silently renumbering —
    renumbering would hand the other account's files to this user.

    Args:
        system_account: 用户名（必须符合 Linux useradd 要求）
        uid: 可选 UID，必须 >= 1000（系统保留 UID < 1000）；缺省时使用
            数据库记录的 pinned UID（Issue #3390）。
        tenant_id: 用户所属租户 ID。Issue #3396: 账号被加入「全局组
            openace-shared（仅授予在 <base>/shared 命名空间根内建目录的
            权限）+ 租户内容组 openace-shared-<tenant_id>」。PR #3402
            review: 传 ``TENANT_UNRESOLVED`` 表示租户查询失败——只加全局
            组、跳过租户内容组（fail-closed，绝不因查询失败落入
            openace-shared-0）。

    Returns:
        True 如果用户存在或创建成功。
    """
    # Issue #3130: 非 Docker 多用户模式跳过系统用户创建
    if not _is_docker_multi_user_mode():
        logger.debug(f"Skipping system user creation in non-Docker mode: {system_account}")
        return True

    # 用户名格式验证（Linux useradd 要求）
    # - Must start with a lowercase letter or underscore
    # - Can contain lowercase letters, digits, underscores, and dashes
    # - Maximum 32 characters
    # - No spaces or special characters
    if not system_account:
        logger.error("Empty username provided")
        return False

    if len(system_account) > 32:
        logger.error(f"Username too long (max 32 chars): {system_account}")
        return False

    # Linux username pattern: [a-z_][a-z0-9_-]*
    if not re.match(r"^[a-z_][a-z0-9_-]*$", system_account):
        logger.error(f"Invalid username format: {system_account}")
        return False

    # macOS 特殊处理（无 useradd）
    if platform.system() == "Darwin":
        logger.debug(f"Skipping system user creation on macOS for: {system_account}")
        return True

    # Issue #3390: no explicit uid -> fall back to the recorded pin. The pin
    # is also the value compared against on the exists-path below.
    recorded_uid = get_recorded_system_uid(system_account)
    if uid is None:
        uid = recorded_uid

    # Same reserved-range rule for the recorded pin: pins are only ever
    # written from useradd-assigned uids (>= 1000), so a lower value means a
    # tampered/corrupted row — refuse rather than create a system-range
    # account or silently ignore the pin.
    if uid is not None and uid < 1000:
        logger.error(f"UID {uid} for {system_account} is reserved for system users, rejected")
        return False

    base_dir = get_workspace_base_dir()

    # 检查用户是否存在（id 命令不需要 sudo，任何用户都可以执行）
    result = subprocess.run(["id", system_account], capture_output=True, text=True)
    if result.returncode == 0:
        logger.info(f"System user {system_account} already exists")
        # Issue #3390: converge the recorded pin to the account's actual uid.
        # For an existing account the OS is the truth (its dirs are chowned
        # to the actual uid); a stale differing pin would be dangerous on the
        # NEXT recreation (it would try to useradd -u <stale>), so record the
        # actual value and warn about the drift. Note this path intentionally
        # BYPASSES the create-path collision guard below: no useradd runs
        # here, and a stale pin that happens to collide with another account
        # is resolved BY this convergence, not blocked by it (review on
        # #3390: blocking here wedged the account on a pin the OS no longer
        # honors).
        actual_uid = _actual_uid_of(system_account)
        if actual_uid is not None:
            if recorded_uid is not None and recorded_uid != actual_uid:
                logger.warning(
                    f"System user {system_account} exists with uid {actual_uid} but the "
                    f"recorded pin is {recorded_uid}; updating the record to {actual_uid} "
                    f"(issue #3390 drift)"
                )
            if actual_uid != recorded_uid:
                record_system_uid(system_account, actual_uid)
        # Issue #3390: a placeholder (nologin) account must be upgraded back
        # to a login shell when its user is active and reaches this path
        # (reactivation across a container recreation).
        _ensure_login_shell(system_account)
        # Still ensure workspace directories exist
        _ensure_workspace_dirs(system_account, base_dir)

        # Issue #2730 + #3396: ensure group memberships (namespace-creation
        # global group + tenant content group)
        if _is_docker_multi_user_mode():
            if not add_user_to_shared_group(system_account, tenant_id=tenant_id):
                logger.warning(
                    f"Failed to add {system_account} to shared groups (tenant={tenant_id})"
                )

        return True

    # Issue #3390 collision guard (CREATE path only — the account is missing):
    # a pinned uid owned by a DIFFERENT account means re-creating this user
    # would either fail (useradd refuses) or, if we "fixed" it by
    # renumbering, silently move the boundary between two users' files. Fail
    # loudly for an administrator instead — never renumber.
    if uid is not None:
        uid_owner = _lookup_uid_owner(uid)
        if uid_owner is not None and uid_owner != system_account:
            logger.error(
                f"UID {uid} for system user {system_account} is already owned by "
                f"'{uid_owner}' (recorded pin conflict, issue #3390); refusing to "
                f"create/renumber — resolve the conflict manually "
                f"(this account keeps a placeholder pin in the database)"
            )
            return False

    # Review on #3390 (⚪): /etc/passwd is not the only reserved-uid source.
    # When the boot sync failed (#3399) the pinned accounts are exactly the
    # ones MISSING from the container, and the guard above (NSS lookup)
    # cannot see those pins — a plain auto-assigning useradd could land this
    # unpinned account on a recorded pin. Choose the auto uid explicitly,
    # excluding passwd uids AND DB-recorded active pins; on a read failure
    # fall back to the plain unpinned useradd (pre-review behavior).
    if uid is None:
        uid = _pick_auto_uid(system_account)

    # 创建用户（通过 wrapper 或 sudo）
    # Issue #1855: 优先使用安全 wrapper，wrapper 内部做参数校验和审计日志
    # Issue #2894: wrapper 脚本需要 root 权限（锁文件、系统用户创建等）
    # Issue #3390: -u <uid> pins the account to its recorded uid.
    if _is_wrapper_available(OPENACE_USERADD_WRAPPER):
        cmd = [OPENACE_USERADD_WRAPPER, system_account]
        if uid is not None:
            cmd.extend(["-u", str(uid)])
        logger.info(
            f"Creating system user via wrapper: {system_account}"
            + (f" (UID: {uid})" if uid else "")
        )
        result = run_as_root_if_needed(cmd)
    else:
        # Fallback: 使用传统 useradd 命令（需要 sudo）
        cmd = ["useradd", "-m", "-s", "/bin/bash"]
        if uid is not None:
            cmd.extend(["-u", str(uid)])
        cmd.append(system_account)
        logger.info(f"Creating system user: {system_account}" + (f" (UID: {uid})" if uid else ""))
        result = run_as_root_if_needed(cmd)

    if result.returncode != 0:
        logger.error(f"Failed to create system user {system_account}: {result.stderr}")
        return False

    logger.info(f"System user {system_account} created successfully")
    # Issue #3390: persist the assigned uid (explicit or auto-picked) so the
    # next container recreation pins this account to the same uid.
    actual_uid = _actual_uid_of(system_account)
    if actual_uid is not None:
        record_system_uid(system_account, actual_uid)
    _ensure_workspace_dirs(system_account, base_dir)

    # Issue #2730 + #3396: add user to shared groups (namespace-creation
    # global group + tenant content group)
    if _is_docker_multi_user_mode():
        if not add_user_to_shared_group(system_account, tenant_id=tenant_id):
            logger.warning(f"Failed to add {system_account} to shared groups (tenant={tenant_id})")

    return True


def _ensure_workspace_dirs(system_account: str, base_dir: str):
    """Ensure workspace directories exist with correct ownership."""
    # Issue #3379 (PR #3389 review round 2): in Docker multi-user mode
    # <base>/shared is the shared-project NAMESPACE ROOT (provisioned by the
    # entrypoint, group openace-shared, 3770). An account literally named
    # "shared" would map its workspace onto that root and this function's
    # chown loop would TAKE IT OVER — breaking shared-project creation for
    # everyone, hiding existing shared projects from the read-side home
    # filter, and handing the account rename-power over other users' project
    # directories (as the root's owner it is exempt from the sticky bit).
    # Fail closed: never touch the namespace root on behalf of an account.
    # Full name reservation across admin/SSO/org-sync creation paths is
    # tracked separately.
    if system_account == "shared" and _is_docker_multi_user_mode():
        logger.warning(
            "refusing to provision workspace dirs for account 'shared': "
            "%s/shared is the shared-project namespace root in multi-user "
            "mode (issue #3379); rename the account",
            base_dir,
        )
        return
    workspace_dir = f"{base_dir}/{system_account}"
    qwen_dir = f"{workspace_dir}/.qwen"

    # 创建目录（必要时通过 wrapper 或 sudo）
    for directory in [workspace_dir, qwen_dir]:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, mode=0o755, exist_ok=True)
            except PermissionError:
                # Issue #1855: 优先使用安全 wrapper
                # Issue #2894: wrapper 脚本需要 root 权限
                if _is_wrapper_available(OPENACE_MKDIR_WRAPPER):
                    result = run_as_root_if_needed(
                        [OPENACE_MKDIR_WRAPPER, system_account, directory],
                    )
                    if result.returncode != 0:
                        logger.warning(f"Cannot create {directory} via wrapper: {result.stderr}")
                        continue
                else:
                    # Fallback: 使用传统 mkdir 命令
                    result = run_as_root_if_needed(["mkdir", "-p", "-m", "755", directory])
                    if result.returncode != 0:
                        logger.warning(f"Cannot create {directory}: {result.stderr}")
                        continue

    # 获取 UID/GID（id 命令不需要 sudo，任何用户都可以执行）
    uid_result = subprocess.run(["id", "-u", system_account], capture_output=True, text=True)
    gid_result = subprocess.run(["id", "-g", system_account], capture_output=True, text=True)

    if uid_result.returncode == 0 and gid_result.returncode == 0:
        uid = int(uid_result.stdout.strip())
        gid = int(gid_result.stdout.strip())

        # 设置所有权（通过 wrapper 或 sudo）
        # Issue #1855: 优先使用安全 wrapper，wrapper 内部做路径校验和审计日志
        # Issue #2894: wrapper 脚本需要 root 权限
        for directory in [workspace_dir, qwen_dir]:
            if _is_wrapper_available(OPENACE_CHOWN_WRAPPER):
                result = run_as_root_if_needed([OPENACE_CHOWN_WRAPPER, f"{uid}:{gid}", directory])
                if result.returncode != 0:
                    logger.warning(f"Cannot chown {directory} via wrapper: {result.stderr}")
            else:
                # Fallback: 使用传统 chown 命令
                result = run_as_root_if_needed(["chown", f"{uid}:{gid}", directory])
                if result.returncode != 0:
                    logger.warning(f"Cannot chown {directory} to {uid}:{gid}: {result.stderr}")


def ensure_user_workspace(system_account: str, tenant_id: int | None = None) -> bool:
    """
    Ensure workspace directory exists for user login.
    Called during login to prepare workspace environment.

    Behavior differs by deployment mode:
    - Docker multi-user mode: Creates system user + workspace + .qwen dirs
    - Package single-user mode: Only creates .qwen in user's home

    Args:
        system_account: Username for the system account.
        tenant_id: Tenant ID for the tenant-scoped shared group (Issue #3396).

    Returns:
        True if workspace setup succeeded or was already ready.
    """
    if _is_docker_multi_user_mode():
        # Docker multi-user mode: ensure system user and workspace
        logger.info(f"Ensuring workspace for {system_account} in Docker multi-user mode")
        return ensure_system_user(system_account, tenant_id=tenant_id)
    else:
        # Package single-user mode: only create .qwen in home directory
        # system_account may not match actual OS user, use current user's home
        home_dir = str(Path.home())
        qwen_dir = f"{home_dir}/.qwen"

        if not os.path.exists(qwen_dir):
            try:
                os.makedirs(qwen_dir, mode=0o755, exist_ok=True)
                logger.info(f"Created .qwen directory at {qwen_dir}")
            except PermissionError as e:
                logger.warning(f"Cannot create .qwen directory: {e}")
                return False

        return True


# ============================================================================
# Shared Project Permission Management (Issue #2730; tenant-scoped #3396)
# ============================================================================


def shared_tenant_group_name(tenant_id: int | None) -> str:
    """Name of the tenant-scoped shared-content group (Issue #3396).

    ``openace-shared-<tenant_id>``; a NULL tenant_id (platform admins) maps
    to the pseudo-id 0 (real tenant ids start at 1, so no collision).

    Raises:
        ValueError: if the derived name cannot be a Linux group name
            (too long — the 31-char ``groupadd`` limit — or a non-decimal
            tenant id, which would be a caller bug).
    """
    suffix = 0 if tenant_id is None else int(tenant_id)
    if suffix < 0:
        raise ValueError(f"tenant_id must be non-negative, got {tenant_id!r}")
    name = f"{SHARED_TENANT_GROUP_PREFIX}{suffix}"
    if len(name) > _MAX_LINUX_GROUP_NAME_LEN:
        raise ValueError(
            f"tenant group name {name!r} exceeds the {_MAX_LINUX_GROUP_NAME_LEN}-char "
            "Linux group-name limit"
        )
    return name


def _ensure_linux_group(group_name: str) -> bool:
    """Idempotently ensure a Linux group exists (``groupadd -f``)."""
    result = subprocess.run(
        ["groupadd", "-f", group_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"Failed to create group {group_name}: {result.stderr}")
        return False
    return True


def ensure_shared_group(tenant_id: int | None = None) -> bool:
    """Ensure the tenant-scoped shared-content group exists.

    Issue #3396: shared project directories are group-owned by
    ``openace-shared-<tenant_id>`` (NOT the global ``openace-shared``), so
    only the tenant's members hold OS-level access to the content. The
    global group survives solely as the shared-namespace-root creation
    group (see the module-level group-model comment).

    Uses 'groupadd -f' to be idempotent and avoid race conditions.

    Args:
        tenant_id: Tenant whose content group to ensure. ``None`` (platform
            admins) maps to the ``openace-shared-0`` pseudo-tenant.

    Returns:
        True if group exists or was created successfully.
    """
    if not _is_docker_multi_user_mode():
        return True  # Skip in non-Docker mode

    try:
        group_name = shared_tenant_group_name(tenant_id)
    except ValueError as e:
        logger.error(f"Cannot derive shared group for tenant {tenant_id!r}: {e}")
        return False

    if not _ensure_linux_group(group_name):
        return False

    logger.info(f"Shared tenant group '{group_name}' ensured")
    return True


# Namespace-root mode, mirroring the Docker entrypoint's provisioning (Issue
# #3379 / PR #3389 review rounds): sticky + setgid with NO others bits. Sticky
# (1000) blocks cross-tenant rename/replace of another tenant's project dir at
# the root (rename(2) only needs write+search on the parent, and the global
# openace-shared creation group spans every tenant); setgid (2000) keeps new
# project dirs inheriting the creation group; dropping "others" keeps
# non-members out entirely.
SHARED_NAMESPACE_ROOT_MODE = "3770"


def ensure_shared_namespace_root(base_dir: str) -> tuple[bool, str]:
    """On-demand provisioning of the shared-namespace root (Issue #3393).

    The root ``<base>/shared`` is normally provisioned by the Docker
    entrypoint at boot. On a deployment where the entrypoint did not run, or
    where the volume/root went missing, the FIRST shared-project creation
    (``create_dir: true`` at ``<base>/shared/<name>``) ran ``mkdir -p`` as
    the creating user against a root-owned 0755 parent and EACCESed (403).
    This helper closes that gap from the API side: when the root does not
    exist, create it — root-owned, group ``openace-shared`` (created if
    missing, mirroring the entrypoint), mode 3770 (sticky+setgid, no others).

    Semantics deliberately mirrored from docker-entrypoint.sh:

    - idempotent and NON-INTRUSIVE: an EXISTING root is left untouched (no
      re-chgrp/chmod — a steady-state deployment must not ping-pong
      ownership, and a root that is already someone else's is never taken
      over from the request path);
    - fail-closed on the account-named-"shared" collision: if a real OS
      account ``shared`` exists, ``<base>/shared`` is its home root and must
      not be group-opened by an API request (the registration-time
      path_guard already rejects such paths — this is defense in depth);
    - every provisioning step runs as root (``run_as_root_if_needed``), so
      the root ends up root-owned like the entrypoint leaves it.

    Args:
        base_dir: Workspace base directory the namespace root lives under
            (the root is ``<base_dir>/shared``).

    Returns:
        Tuple of (success, error_message). error_message is empty on success
        (including the skip cases: non-multi-user mode, root already exists).
    """
    if not _is_docker_multi_user_mode():
        return (True, "")  # Skip in non-Docker mode

    if not base_dir or not os.path.isabs(base_dir):
        return (False, f"Workspace base directory must be absolute: {base_dir!r}")

    # Same single source of truth the registration-time validation uses
    # (path_guard.SHARED_NAMESPACE_DIRNAME); path_guard imports nothing from
    # this module, so the lazy import cannot cycle.
    from app.utils.path_guard import SHARED_NAMESPACE_DIRNAME

    root = f"{base_dir.rstrip('/')}/{SHARED_NAMESPACE_DIRNAME}"

    if os.path.exists(root):
        logger.debug(f"Shared namespace root already exists, leaving untouched: {root}")
        return (True, "")

    # Fail closed on the account-named-shared collision (see docstring).
    id_result = subprocess.run(["id", "shared"], capture_output=True, text=True)
    if id_result.returncode == 0:
        return (
            False,
            f"{root} collides with the home directory of a user account named "
            "'shared'; administrator intervention required",
        )

    try:
        # 1. Global creation group (idempotent; mirrors the entrypoint's
        #    `groupadd -f openace-shared`).
        result = run_as_root_if_needed(["groupadd", "-f", SHARED_GROUP_NAME])
        if result.returncode != 0:
            return (False, f"groupadd failed: {result.stderr.strip()}")

        # 2. Create the root (parent base dirs included) — as root, so the
        #    root is root-owned exactly as the entrypoint provisions it.
        result = run_as_root_if_needed(["mkdir", "-p", root])
        if result.returncode != 0:
            return (False, f"mkdir failed: {result.stderr.strip()}")

        # 3. Group-own it by the global creation group + sticky/setgid mode.
        result = run_as_root_if_needed(["chgrp", SHARED_GROUP_NAME, root])
        if result.returncode != 0:
            return (False, f"chgrp failed: {result.stderr.strip()}")
        result = run_as_root_if_needed(["chmod", SHARED_NAMESPACE_ROOT_MODE, root])
        if result.returncode != 0:
            return (False, f"chmod failed: {result.stderr.strip()}")
    except Exception as e:  # noqa: BLE001 - degrade to a clean error, never crash
        return (False, f"Unexpected provisioning failure: {e}")

    logger.info(
        "Provisioned shared namespace root %s (root-owned, group %s, mode %s) on demand (#3393)",
        root,
        SHARED_GROUP_NAME,
        SHARED_NAMESPACE_ROOT_MODE,
    )
    return (True, "")


def add_user_to_shared_group(system_account: str, tenant_id: TenantIdOrUnresolved = None) -> bool:
    """Enroll a user in BOTH shared-project groups (Issue #3396).

    1. Global ``openace-shared`` — grants only the right to create a project
       directory inside the sticky ``<base>/shared`` namespace root. Every
       tenant's accounts need this; it confers no content access (project
       dirs carry no others bits and are owned by tenant groups).
    2. ``openace-shared-<tenant_id>`` — grants OS read/write on the TENANT's
       shared project content (dirs 2770 / files 660).

    Uses 'usermod -aG' which is idempotent (safe to call multiple times).

    Args:
        system_account: Username to enroll.
        tenant_id: Tenant whose content group to join. ``None`` maps to the
            ``openace-shared-0`` pseudo-tenant (platform admins).
            ``TENANT_UNRESOLVED`` (PR #3402 review) enrolls ONLY the global
            namespace group — a failed tenant lookup must not fail open
            into the platform-admins' content group.

    Returns:
        True if the user was added (or already was) to both groups.
    """
    if not _is_docker_multi_user_mode():
        return True  # Skip in non-Docker mode

    if tenant_id is TENANT_UNRESOLVED:
        logger.warning(
            f"Tenant could not be resolved for {system_account}: enrolling ONLY the "
            "global shared group (tenant content group skipped — fail-closed)"
        )
        groups: tuple[str, ...] = (SHARED_GROUP_NAME,)
    else:
        # narrows the alias for mypy: the sentinel took the branch above
        assert not isinstance(tenant_id, _TenantUnresolved)
        try:
            tenant_group = shared_tenant_group_name(tenant_id)
        except ValueError as e:
            logger.error(f"Cannot derive shared group for tenant {tenant_id!r}: {e}")
            return False
        groups = (SHARED_GROUP_NAME, tenant_group)

    ok = True
    for group in groups:
        if not _ensure_linux_group(group):
            ok = False
            continue
        result = subprocess.run(
            ["usermod", "-aG", group, system_account],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(f"Failed to add {system_account} to group {group}: {result.stderr}")
            ok = False

    if ok:
        logger.info(f"User '{system_account}' enrolled in shared groups ({' + '.join(groups)})")
    return ok


def setup_shared_project_permissions(path: str, tenant_id: int | None = None) -> tuple[bool, str]:
    """Set up shared project directory permissions.

    Configures a directory for TENANT-scoped shared access (Issue #3396):
    1. Ensures the tenant shared group exists (openace-shared-<tenant_id>)
    2. Sets group ownership to the tenant group
    3. Sets permissions to 2770 (setgid + group rwx, NO others bits —
       cross-tenant accounts must get EACCES even though they are global
       openace-shared members for namespace-root creation)
    4. Recursively fixes existing subdirectories (2770) and files (660)

    Args:
        path: Absolute path to the project directory.
        tenant_id: Tenant whose group owns the shared content. ``None``
            maps to the ``openace-shared-0`` pseudo-tenant.

    Returns:
        Tuple of (success, error_message). error_message is empty on success.
    """
    if not _is_docker_multi_user_mode():
        return (True, "")  # Skip in non-Docker mode

    if not path:
        return (False, "Path is required")

    if not os.path.isabs(path):
        return (False, f"Path must be absolute: {path}")

    if not os.path.exists(path):
        return (False, f"Path does not exist: {path}")

    if not os.path.isdir(path):
        return (False, f"Path is not a directory: {path}")

    try:
        group_name = shared_tenant_group_name(tenant_id)
    except ValueError as e:
        return (False, str(e))

    try:
        # 1. Ensure the tenant shared group exists
        if not ensure_shared_group(tenant_id):
            return (False, f"Failed to create shared group for tenant {tenant_id!r}")

        # 2. Set group ownership
        result = subprocess.run(
            ["chown", f":{group_name}", path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, f"chown failed: {result.stderr}")

        # 3. Set permissions (setgid + 2770, no others bits)
        result = subprocess.run(
            ["chmod", "2770", path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, f"chmod failed: {result.stderr}")

        # 4. Recursively fix existing subdirectories and files
        # Use find to set permissions on existing content
        subprocess.run(
            ["find", path, "-type", "d", "-exec", "chmod", "2770", "{}", ";"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        subprocess.run(
            ["find", path, "-type", "f", "-exec", "chmod", "660", "{}", ";"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        logger.info(f"Shared project permissions set for: {path} (group {group_name})")
        return (True, "")

    except subprocess.TimeoutExpired:
        return (False, "Permission setup timed out")
    except Exception as e:
        return (False, str(e))


# ============================================================================
# Performance Optimization Functions (Issue #2746)
# ============================================================================


def estimate_file_count_fast(path: str, timeout: int = 5) -> int:
    """Fast file count estimation using sampling method.

    Samples only the first 3 directory levels and extrapolates total.
    This avoids traversing the entire directory tree for large projects.

    Args:
        path: Absolute path to the project directory.
        timeout: Maximum time (seconds) for estimation.

    Returns:
        Estimated total file count. Returns 50000 if estimation times out.
    """
    import time

    if not os.path.isabs(path) or not os.path.exists(path):
        return 50000  # Default to maximum

    try:
        start_time = time.time()

        # Count files in first 3 levels only
        result = subprocess.run(
            ["find", path, "-maxdepth", "3", "-type", "f"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            return 50000

        lines = result.stdout.strip().split("\n")
        count_3_levels = len([l for l in lines if l])  # Count non-empty lines

        # Extrapolate: assume each level doubles (typical tree structure)
        # For 10 levels: total ≈ count_3_levels * 2^(10-3) = count_3_levels * 128
        # Cap at reasonable maximum
        estimated_total = min(count_3_levels * 128, 50000)

        elapsed = time.time() - start_time
        logger.debug(
            f"Estimated {estimated_total} files from {count_3_levels} samples "
            f"(took {elapsed:.2f}s)"
        )

        return estimated_total

    except subprocess.TimeoutExpired:
        logger.warning(f"File count estimation timed out after {timeout}s")
        return 50000
    except Exception as e:
        logger.error(f"Error estimating file count: {e}")
        return 50000


def setup_permissions_with_depth_limit(
    path: str,
    depth_limit: int | None = None,
    timeout: int = 60,
    progress_callback=None,
    user_id: int | None = None,
    project_id: int | None = None,
    tenant_id: int | None = None,
) -> tuple[bool, str, int]:
    """Set permissions with optional recursion depth limit.

    Optimized version that:
    1. Uses batch processing (find | xargs) instead of -exec
    2. Limits recursion depth when specified
    3. Provides progress feedback via callback
    4. Has better timeout handling
    5. Records audit log for permission setup (Issue #2745)

    Issue #3396: the project directory is group-owned by the TENANT-scoped
    group ``openace-shared-<tenant_id>`` with dirs 2770 / files 660 (setgid
    kept for group inheritance; others bits dropped so members of OTHER
    tenants — who legitimately hold the global openace-shared membership for
    namespace-root creation — get EACCES on this content).

    Args:
        path: Absolute path to the project directory.
        depth_limit: Maximum recursion depth (None = no limit).
        timeout: Timeout in seconds for entire operation.
        progress_callback: Optional callback function(percent, processed, total).
        user_id: User ID who initiated the operation (for audit log).
        project_id: Project ID for audit log resource_id.
        tenant_id: Tenant whose shared-content group owns the directory.

    Returns:
        Tuple of (success, error_message, files_processed).
    """
    import time

    if not _is_docker_multi_user_mode():
        return (True, "", 0)

    if not path or not os.path.isabs(path) or not os.path.exists(path):
        return (False, "Invalid path", 0)

    try:
        group_name = shared_tenant_group_name(tenant_id)
    except ValueError as e:
        return (False, str(e), 0)

    operation_start_time = time.time()
    operation_start_datetime = datetime.now().isoformat()

    try:
        # Ensure the tenant shared group
        if not ensure_shared_group(tenant_id):
            return (False, f"Failed to create shared group for tenant {tenant_id!r}", 0)

        # Set root directory permissions (setgid + 2770, no others bits)
        subprocess.run(
            ["chown", f":{group_name}", path],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["chmod", "2770", path],
            capture_output=True,
            text=True,
            check=True,
        )

        # Build find command with optional depth limit
        find_cmd = ["find", path]
        if depth_limit:
            find_cmd.extend(["-maxdepth", str(depth_limit)])

        files_processed = 0

        # Process directories with batch chmod (more efficient than -exec)
        dir_cmd = find_cmd + ["-type", "d"]
        dir_result = subprocess.run(
            dir_cmd,
            capture_output=True,
            text=True,
            timeout=timeout // 2,
        )

        if dir_result.returncode == 0 and dir_result.stdout.strip():
            dirs = [d for d in dir_result.stdout.strip().split("\n") if d]
            # Batch process: use xargs to run chmod on multiple dirs at once
            chmod_process = subprocess.Popen(
                ["xargs", "-0", "chmod", "2770"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Use null-separated input for xargs
            chmod_process.communicate(input="\0".join(dirs), timeout=timeout // 2)
            files_processed += len(dirs)

            if progress_callback and len(dirs) > 100:
                progress_callback(50, files_processed, -1)

        # Process files with batch chmod
        file_cmd = find_cmd + ["-type", "f"]
        file_result = subprocess.run(
            file_cmd,
            capture_output=True,
            text=True,
            timeout=timeout // 2,
        )

        if file_result.returncode == 0 and file_result.stdout.strip():
            files = [f for f in file_result.stdout.strip().split("\n") if f]
            # Batch process files
            chmod_process = subprocess.Popen(
                ["xargs", "-0", "chmod", "660"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            chmod_process.communicate(input="\0".join(files), timeout=timeout // 2)
            files_processed += len(files)

            if progress_callback:
                progress_callback(100, files_processed, files_processed)

        logger.info(f"Set permissions for {files_processed} items in {path} (group {group_name})")

        # Record audit log for successful permission setup (Issue #2745)
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=True,
            files_processed=files_processed,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            depth_limit=depth_limit,
        )

        return (True, "", files_processed)

    except subprocess.TimeoutExpired:
        error_msg = f"Operation timed out after {timeout}s"
        # Record audit log for failed permission setup (Issue #2745)
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
            depth_limit=depth_limit,
        )
        return (False, error_msg, 0)
    except subprocess.CalledProcessError as e:
        error_msg = f"Command failed: {e.stderr}"
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
            depth_limit=depth_limit,
        )
        return (False, error_msg, 0)
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
            depth_limit=depth_limit,
        )
        return (False, error_msg, 0)


def verify_setgid_support(path: str) -> tuple[bool, str]:
    """Verify that setgid is supported and working on the filesystem.

    Creates a test subdirectory to verify that:
    1. setgid bit can be set on directories (2775)
    2. New subdirectories inherit the setgid bit

    Args:
        path: Path to test (will create a temporary subdirectory).

    Returns:
        Tuple of (supported, error_message).
    """
    import tempfile

    if not os.path.isabs(path) or not os.path.exists(path):
        return (False, "Path does not exist")

    test_dir = None
    try:
        # Create temporary test directory
        test_dir = tempfile.mkdtemp(prefix=".setgid_test_", dir=path)

        # Set setgid on test directory
        result = subprocess.run(
            ["chmod", "2775", test_dir],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, f"Failed to set setgid: {result.stderr}")

        # Create subdirectory to test inheritance
        subdir = os.path.join(test_dir, "test_subdir")
        os.makedirs(subdir)

        # Check if subdirectory inherited setgid
        stat_result = os.stat(subdir)
        mode = stat_result.st_mode

        # setgid bit is 0o2000 (octal)
        has_setgid = bool(mode & 0o2000)

        if has_setgid:
            logger.info(f"setgid inheritance verified at {path}")
            return (True, "")
        else:
            logger.warning(f"setgid not inherited at {path}")
            return (False, "setgid not inherited by new subdirectories")

    except Exception as e:
        return (False, f"Verification failed: {e}")
    finally:
        # Clean up test directory
        if test_dir and os.path.exists(test_dir):
            import shutil

            shutil.rmtree(test_dir, ignore_errors=True)


# ============================================================================
# Audit Log Helper Functions (Issue #2745)
# ============================================================================


def _log_permission_audit(
    user_id: int | None,
    project_id: int | None,
    path: str,
    success: bool,
    files_processed: int,
    operation_start_time: float,
    operation_start_datetime: str,
    error_message: str | None = None,
    depth_limit: int | None = None,
) -> None:
    """Record audit log for shared project permission setup.

    Uses a separate database connection to avoid transaction rollback issues.
    Failures are logged but never raise exceptions.

    Args:
        user_id: User ID who initiated the operation.
        project_id: Project ID for resource_id.
        path: Project path.
        success: Whether the operation succeeded.
        files_processed: Number of files processed.
        operation_start_time: Unix timestamp when operation started.
        operation_start_datetime: ISO format datetime when operation started.
        error_message: Error message if operation failed.
        depth_limit: Recursion depth limit used.
    """
    import time

    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audit_logger = AuditLogger()

        operation_end_time = time.time()
        duration_seconds = operation_end_time - operation_start_time

        details = {
            "path": path,
            "files_processed": files_processed,
            "operation_start_time": operation_start_datetime,
            "operation_end_time": datetime.now().isoformat(),
            "duration_seconds": round(duration_seconds, 2),
            "success": success,
        }

        if depth_limit is not None:
            details["depth_limit"] = depth_limit

        if error_message:
            details["error_message"] = error_message

        audit_logger.log_action(
            action=AuditAction.SHARED_PROJECT_PERMISSION_SETUP_COMPLETE,
            user_id=user_id,
            resource_type="project",
            resource_id=str(project_id) if project_id else None,
            details=details,
            success=success,
            error_message=error_message,
        )

        logger.debug(
            f"Recorded permission audit log: user_id={user_id}, project_id={project_id}, "
            f"path={path}, success={success}, files_processed={files_processed}"
        )

    except Exception as e:
        # Audit log failure should not affect main operation
        logger.error(f"Failed to record permission audit log: {e}")


# ============================================================================
# Shared Project User Management (Issue #3275; tenant-scoped #3396)
# ============================================================================


def remove_user_from_shared_group(system_account: str, tenant_id: int | None = None) -> bool:
    """Remove a user from the TENANT-scoped shared-content group.

    Issue #3396: only the tenant group is touched — the global
    ``openace-shared`` membership (namespace-root creation right) stays,
    matching the read-side model where shared projects are visible to a
    whole tenant, not to per-project user rows. Callers use this when a
    user LEAVES a tenant (admin tenant move) or is deactivated, not when a
    single project's visible-user row is removed.

    Uses 'gpasswd -d' to remove user from the group.
    Idempotent: if user is not in the group, returns True.

    Args:
        system_account: Username to remove from the group.
        tenant_id: Tenant whose content group to leave. ``None`` maps to
            the ``openace-shared-0`` pseudo-tenant.

    Returns:
        True if user was removed or not in group.
    """
    if not _is_docker_multi_user_mode():
        return True  # Skip in non-Docker mode

    try:
        group_name = shared_tenant_group_name(tenant_id)
    except ValueError as e:
        logger.error(f"Cannot derive shared group for tenant {tenant_id!r}: {e}")
        return False

    result = subprocess.run(
        ["gpasswd", "-d", system_account, group_name],
        capture_output=True,
        text=True,
    )

    # gpasswd -d exit codes:
    # 0 - success (user removed)
    # 3 - user not in group (treat as success for idempotency)
    # other - error
    if result.returncode == 3:
        logger.info(f"User '{system_account}' not in group {group_name}, already removed")
        return True

    if result.returncode != 0:
        logger.error(f"Failed to remove {system_account} from group {group_name}: {result.stderr}")
        return False

    logger.info(f"User '{system_account}' removed from shared group {group_name}")
    return True


def revoke_shared_project_access(
    path: str,
    owner_system_account: str,
    timeout: int = 60,
    user_id: int | None = None,
    project_id: int | None = None,
) -> tuple[bool, str]:
    """Reclaim OS-level access to a shared project on revocation (Issue #3396).

    ``PUT /api/projects/<id> {is_shared: false}`` used to flip only the DB
    flag: group-member accounts (including other tenants, pre-#3396) kept
    OS read/write. The project becomes the creator's PRIVATE project:

    1. ``chown -R <creator-uid>:<creator-gid>`` — drops the tenant-group
       ownership (root-side via the openace-chown wrapper when available;
       the wrapper validates /workspace|/home prefixes and uid/gid >= 1000).
    2. ``chmod`` dirs 0700 / files 0600 — removes every group/other bit, so
       ex-members get EACCES while the creator keeps full access.

    Args:
        path: Absolute path to the (revoked) project directory.
        owner_system_account: Creator's OS account (becomes the owner).
        timeout: Timeout in seconds for the batched chmod passes.
        user_id: Initiating user ID (for the audit log).
        project_id: Project ID (for the audit log).

    Returns:
        Tuple of (success, error_message). error_message is empty on success.
    """
    import time

    if not _is_docker_multi_user_mode():
        return (True, "")

    if not path or not os.path.isabs(path):
        return (False, f"Invalid path: {path!r}")

    if not os.path.exists(path):
        # Nothing on disk to reclaim (e.g. create_dir was false and the dir
        # was never materialized) — the DB flag is the whole revocation.
        logger.info(f"Revoked shared project path does not exist, nothing to reclaim: {path}")
        return (True, "")

    if not os.path.isdir(path):
        return (False, f"Path is not a directory: {path}")

    if not owner_system_account:
        return (False, "Owner system account is required to reclaim access")

    operation_start_time = time.time()
    operation_start_datetime = datetime.now().isoformat()

    try:
        # Resolve the creator's uid/gid (id needs no privileges)
        uid_result = subprocess.run(
            ["id", "-u", owner_system_account], capture_output=True, text=True
        )
        gid_result = subprocess.run(
            ["id", "-g", owner_system_account], capture_output=True, text=True
        )
        if uid_result.returncode != 0 or gid_result.returncode != 0:
            return (
                False,
                f"Cannot resolve owner account {owner_system_account}: "
                f"{uid_result.stderr or gid_result.stderr}".strip(),
            )
        ownership = f"{uid_result.stdout.strip()}:{gid_result.stdout.strip()}"

        # 1. Recursive chown to the creator (wrapper preferred — it
        #    validates the path prefix and the uid/gid range; plain chown
        #    fallback runs through run_as_root_if_needed either way).
        if _is_wrapper_available(OPENACE_CHOWN_WRAPPER):
            result = run_as_root_if_needed([OPENACE_CHOWN_WRAPPER, "-R", ownership, path])
        else:
            result = run_as_root_if_needed(["chown", "-R", ownership, path])
        if result.returncode != 0:
            return (False, f"chown failed: {result.stderr}")

        # 2. Strip group/other bits: dirs 0700 / files 0600 (batched like
        #    setup_permissions_with_depth_limit).
        # Review on #3396 (finding 7): the timeout must be a BUDGET across
        # all four subprocesses (2 find + 2 xargs-chmod) — each pass used to
        # get timeout//2, so a slow project could take 2x the declared
        # timeout before the overall TimeoutExpired fired. Deadline-based:
        # each pass gets whatever remains of the budget (at least 1s).
        # The xargs chmod's return code is checked — a partial strip (xargs
        # dies mid-batch) used to report success, leaving ex-members with
        # access to whatever entries survived the failed pass.
        deadline = operation_start_time + timeout
        for kind, mode in (("d", "0700"), ("f", "0600")):
            remaining = max(1, int(deadline - time.time()))
            list_result = subprocess.run(
                ["find", path, "-type", kind],
                capture_output=True,
                text=True,
                timeout=remaining,
            )
            if list_result.returncode != 0 or not list_result.stdout.strip():
                continue
            entries = [e for e in list_result.stdout.strip().split("\n") if e]
            remaining = max(1, int(deadline - time.time()))
            chmod_process = subprocess.Popen(
                ["xargs", "-0", "chmod", mode],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _, chmod_stderr = chmod_process.communicate(
                    input="\0".join(entries), timeout=remaining
                )
            except subprocess.TimeoutExpired:
                chmod_process.kill()
                chmod_process.communicate()
                raise
            if chmod_process.returncode != 0:
                return (
                    False,
                    f"chmod {mode} pass failed with rc={chmod_process.returncode}: "
                    f"{(chmod_stderr or '').strip()}",
                )

        logger.info(f"Revoked shared project access on {path} (owner {owner_system_account})")

        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=True,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
        )
        return (True, "")

    except subprocess.TimeoutExpired:
        error_msg = f"Revocation timed out after {timeout}s"
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
        )
        return (False, error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during revocation: {e}"
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
        )
        return (False, error_msg)


def get_user_project_active_sessions(user_id: int, project_id: int) -> int:
    """Get the number of active sessions for a user in a project.

    Args:
        user_id: User ID to check.
        project_id: Project ID to check.

    Returns:
        Number of active sessions for the user in the project.
    """
    try:
        from app.repositories.database import get_db_connection

        query = """
            SELECT COUNT(*) as count
            FROM agent_sessions
            WHERE user_id = ? AND project_id = ? AND status = 'active'
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (user_id, project_id))
            result = cursor.fetchone()
            if not result:
                return 0
            # Dual row shape (#3403): PG connections wrap cursors with
            # RealDictCursor, so positional result[0] raised KeyError there
            # and the except swallowed it into a permanent "0 sessions".
            # Same fix as get_recorded_system_uid (PR #3400).
            value = result["count"] if isinstance(result, dict) else result[0]
            return int(value)

    except Exception as e:
        logger.error(f"Failed to get active sessions: {e}")
        return 0
