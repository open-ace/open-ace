# Issue #3374 本地工作区隔离能力契约 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为本地交互工作区提供版本化多用户隔离能力契约(查询 API + 启动路径结构化拒绝),作为 issue #3374 的第一增量。

**Architecture:** 新增纯计算契约模块(从运行时真实状态推导隔离等级,不引入新配置),新蓝图端点暴露契约;`user-url` 增加可选 `required_isolation` 门闸(缺省行为零变化);WebUIManager 增加一个公开方法判定能否按用户 UID 启动。

**Tech Stack:** Flask Blueprint + dataclass,pytest(unit lane),无新依赖。

**Spec:** `docs/superpowers/specs/2026-09-11-issue-3374-workspace-isolation-contract-design.md`

## Global Constraints

- 缺省(无 `required_isolation` 参数)路径:除成功响应新增 `isolation` 回显字段外,响应键集合、状态码与全部既有行为不变——默认 UI 与单用户模式零退化。
- 契约必须诚实:仅 linux 可申报 os_user(darwin 的 `ensure_system_user` 跳过创建,`app/utils/workspace.py:163-165`);os_user 等级不宣称内核级隔离;resources/network_egress 永远列入 unsupported(交互路径)。
- 推导语义或入口矩阵变化时必须递增 `POLICY_REVISION`。
- 不新增环境变量/配置项;复用 `manager.config`、`_is_docker_multi_user_mode()`。
- reason message 不含部署细节(root/路径要求等),部署要求只写入能力矩阵文档。
- 测试置于 `tests/unit/`,统一 `pytest.mark.issue(3374)`;仅两个回归哨兵测试(username 回退兼容性、HTTPS 相对路径分支)追加 `pytest.mark.regression`。所有测试必须无 root/无 OS 用户依赖(stub/monkeypatch),CI unit lane 可跑;**不得依赖宿主 `~/.open-ace/config.json` 的内容**(涉及 manager 的断言一律 patch stub)。
- schema/ 与 migrations/ 不涉及(无 DB 变更),不触碰 `scripts/rebuild_schema_snapshots.py`。
- 提交信息:任务内小步提交;分支 `feat/3374-workspace-isolation-contract`。

---

### Task 1: 契约核心模块

**Files:**
- Create: `app/services/workspace_isolation_contract.py`
- Test: `tests/unit/test_workspace_isolation_contract_3374.py`

**Interfaces:**
- Produces: `POLICY_REVISION: str`;`SUPPORTED_ISOLATION_LEVELS: tuple`;`ALL_DIMENSIONS: tuple`;`BACKEND_PER_USER/BACKEND_SHARED: str`;`ISOLATION_LEVEL_NONE/OS_USER/SANDBOXED: str`;`DIMENSION_*: str`;`IsolationReason(code, message).public_dict()`;`IsolationCapabilitySnapshot.supported/backend/isolation_level/enforced/unsupported/reasons/policy_revision` 与 `.public_dict()`;`build_workspace_isolation_snapshot(manager=None) -> IsolationCapabilitySnapshot`;`evaluate_isolation_requirement(required_level, *, snapshot, system_account, manager) -> IsolationReason | None`;`_current_platform() -> str` 与模块级 `_is_docker_multi_user_mode`(均为可 monkeypatch 的模块属性)。
- Consumes: `manager.config.enabled/.multi_user_mode`(已存在);`manager.supports_per_user_launch`(Task 3 实现,本任务测试以 stub 提供)。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for the local workspace isolation capability contract (Issue #3374)."""

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]


class _StubConfig:
    def __init__(self, enabled=True, multi_user_mode=True):
        self.enabled = enabled
        self.multi_user_mode = multi_user_mode


class _StubManager:
    def __init__(self, enabled=True, multi_user_mode=True, launch_ok=True, launch_reason=None):
        self.config = _StubConfig(enabled, multi_user_mode)
        self._launch_ok = launch_ok
        self._launch_reason = launch_reason

    def supports_per_user_launch(self, system_account):
        return self._launch_ok, self._launch_reason


def _patch_deployment(monkeypatch, *, platform="linux", docker_multi_user=True):
    monkeypatch.setattr(wic, "_current_platform", lambda: platform)
    monkeypatch.setattr(wic, "_is_docker_multi_user_mode", lambda: docker_multi_user)


def _reason_codes(snapshot):
    return [r.code for r in snapshot.reasons]


def test_webui_disabled_reports_unsupported(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(enabled=False))
    assert snap.supported is False
    assert snap.isolation_level == wic.ISOLATION_LEVEL_NONE
    assert snap.backend == wic.BACKEND_SHARED
    assert _reason_codes(snap) == ["webui_disabled"]
    assert snap.enforced == ()
    assert set(snap.unsupported) == set(wic.ALL_DIMENSIONS)


@pytest.mark.parametrize("platform", ["windows", "darwin"])
def test_non_linux_platform_reports_unsupported(monkeypatch, platform):
    _patch_deployment(monkeypatch, platform=platform, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert snap.supported is False
    assert _reason_codes(snap) == ["platform_unsupported"]


def test_platform_reason_message_has_no_deployment_details(monkeypatch):
    _patch_deployment(monkeypatch, platform="darwin", docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    message = snap.reasons[0].message
    assert "/workspace" not in message and "root" not in message.lower()


def test_single_user_mode_reports_unsupported(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(multi_user_mode=False))
    assert snap.supported is False
    assert _reason_codes(snap) == ["multi_user_mode_disabled"]


def test_root_single_user_reports_mode_not_mapping(monkeypatch):
    # 顺序保证:multi_user_mode 关闭优先于 docker 多用户判定(root 单用户部署
    # 不误报 identity_mapping_unverified)。
    _patch_deployment(monkeypatch, docker_multi_user=False)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(multi_user_mode=False))
    assert _reason_codes(snap) == ["multi_user_mode_disabled"]


def test_non_docker_multi_user_reports_unverified(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=False)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert snap.supported is False
    assert _reason_codes(snap) == ["identity_mapping_unverified"]


def test_docker_multi_user_reports_os_user(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_OS_USER
    assert snap.backend == wic.BACKEND_PER_USER
    assert snap.enforced == (
        wic.DIMENSION_IDENTITY,
        wic.DIMENSION_FILESYSTEM,
        wic.DIMENSION_ENVIRONMENT,
        wic.DIMENSION_PROCESS,
    )
    assert snap.unsupported == (wic.DIMENSION_RESOURCES, wic.DIMENSION_NETWORK_EGRESS)
    assert snap.reasons == ()


def test_policy_revision_always_present(monkeypatch):
    _patch_deployment(monkeypatch)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(enabled=False))
    assert snap.policy_revision == wic.POLICY_REVISION


def test_public_dict_shape(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    data = wic.build_workspace_isolation_snapshot(_StubManager()).public_dict()
    assert set(data.keys()) == {
        "local_workspace_multi_user",
        "backend",
        "isolation_level",
        "enforced",
        "unsupported",
        "reasons",
        "entry_points",
        "policy_revision",
    }
    assert data["local_workspace_multi_user"] == "supported"
    assert data["entry_points"]["autonomous"] == "separate_contract"


def test_level_ordering_and_validation():
    assert wic.isolation_level_at_least("os_user", "none")
    assert wic.isolation_level_at_least("os_user", "os_user")
    assert not wic.isolation_level_at_least("none", "os_user")
    assert wic.is_valid_isolation_level("sandboxed")
    assert not wic.is_valid_isolation_level("strong")


def _supported_snapshot(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    return wic.build_workspace_isolation_snapshot(_StubManager())


def test_gate_rejects_invalid_level(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        "strong", snapshot=snap, system_account="alice_acct", manager=_StubManager()
    )
    assert reason.code == "invalid_isolation_level"


def test_gate_rejects_level_above_supported(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        wic.ISOLATION_LEVEL_SANDBOXED,
        snapshot=snap,
        system_account="alice_acct",
        manager=_StubManager(),
    )
    assert reason.code == "isolation_level_unsupported"


def test_gate_allows_none_without_further_checks(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    assert (
        wic.evaluate_isolation_requirement(
            wic.ISOLATION_LEVEL_NONE,
            snapshot=snap,
            system_account=None,
            manager=_StubManager(launch_ok=False),
        )
        is None
    )


def test_gate_rejects_missing_identity_mapping(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        wic.ISOLATION_LEVEL_OS_USER,
        snapshot=snap,
        system_account=None,
        manager=_StubManager(),
    )
    assert reason.code == "identity_mapping_missing"


def test_gate_rejects_shared_account_launch(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        wic.ISOLATION_LEVEL_OS_USER,
        snapshot=snap,
        system_account="alice_acct",
        manager=_StubManager(launch_ok=False, launch_reason="dev_directory_mode_shared_account"),
    )
    assert reason.code == "per_user_launch_unavailable"
    assert "dev_directory_mode_shared_account" in reason.message


def test_gate_passes_when_all_conditions_hold(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    assert (
        wic.evaluate_isolation_requirement(
            wic.ISOLATION_LEVEL_OS_USER,
            snapshot=snap,
            system_account="alice_acct",
            manager=_StubManager(),
        )
        is None
    )
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_workspace_isolation_contract_3374.py -v`
Expected: FAIL(collection error: `ModuleNotFoundError: No module named 'app.services.workspace_isolation_contract'`)

- [ ] **Step 3: 最小实现**

```python
"""Versioned capability contract for local interactive workspace multi-user
isolation (Issue #3374).

The contract reports what this deployment actually enforces, derived from
runtime state only (webui manager config, environment, euid, platform).
It deliberately never upgrades the reported isolation level: ``os_user``
means per-user OS accounts on a shared host kernel, not strong runtime
isolation (no namespaces, no egress policy). Reason messages stay free of
deployment specifics; see docs/workspace-isolation-capabilities.md for
deployment requirements. Bump POLICY_REVISION whenever derivation semantics
or the entry-point matrix change.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from app.utils.workspace import _is_docker_multi_user_mode  # module-level patch seam

POLICY_REVISION = "2026-09-11"

ISOLATION_LEVEL_NONE = "none"
ISOLATION_LEVEL_OS_USER = "os_user"
ISOLATION_LEVEL_SANDBOXED = "sandboxed"

_LEVEL_ORDER = {
    ISOLATION_LEVEL_NONE: 0,
    ISOLATION_LEVEL_OS_USER: 1,
    ISOLATION_LEVEL_SANDBOXED: 2,
}
SUPPORTED_ISOLATION_LEVELS = tuple(_LEVEL_ORDER)

DIMENSION_IDENTITY = "identity"
DIMENSION_FILESYSTEM = "filesystem"
DIMENSION_ENVIRONMENT = "environment"
DIMENSION_PROCESS = "process"
DIMENSION_RESOURCES = "resources"
DIMENSION_NETWORK_EGRESS = "network_egress"

ALL_DIMENSIONS = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
    DIMENSION_RESOURCES,
    DIMENSION_NETWORK_EGRESS,
)

_OS_USER_ENFORCED = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
)
_OS_USER_UNSUPPORTED = (DIMENSION_RESOURCES, DIMENSION_NETWORK_EGRESS)

BACKEND_PER_USER = "qwen-code-webui-per-user"
BACKEND_SHARED = "qwen-code-webui-shared"

# Static, revision-gated audit result (design doc §2.4). Values:
# enforced | partial | separate_contract.
ENTRY_POINT_STATUSES = {
    "webui": "enforced",
    "filesystem_api": "partial",
    "session_history": "enforced",
    "terminal": "partial",
    "vscode": "partial",
    "autonomous": "separate_contract",
}


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def is_valid_isolation_level(value: str) -> bool:
    return value in _LEVEL_ORDER


def isolation_level_at_least(have: str, want: str) -> bool:
    return _LEVEL_ORDER[have] >= _LEVEL_ORDER[want]


@dataclass(frozen=True)
class IsolationReason:
    code: str
    message: str

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class IsolationCapabilitySnapshot:
    supported: bool
    backend: str
    isolation_level: str
    enforced: tuple[str, ...]
    unsupported: tuple[str, ...]
    reasons: tuple[IsolationReason, ...]
    policy_revision: str = POLICY_REVISION

    def public_dict(self) -> dict[str, Any]:
        return {
            "local_workspace_multi_user": "supported" if self.supported else "unsupported",
            "backend": self.backend,
            "isolation_level": self.isolation_level,
            "enforced": list(self.enforced),
            "unsupported": list(self.unsupported),
            "reasons": [r.public_dict() for r in self.reasons],
            "entry_points": dict(ENTRY_POINT_STATUSES),
            "policy_revision": self.policy_revision,
        }


def _unsupported(reason_code: str, message: str) -> IsolationCapabilitySnapshot:
    return IsolationCapabilitySnapshot(
        supported=False,
        backend=BACKEND_SHARED,
        isolation_level=ISOLATION_LEVEL_NONE,
        enforced=(),
        unsupported=ALL_DIMENSIONS,
        reasons=(IsolationReason(reason_code, message),),
    )


def build_workspace_isolation_snapshot(
    manager: Any = None,
) -> IsolationCapabilitySnapshot:
    """Derive the isolation capability snapshot from live runtime state."""
    if manager is None:
        from app.services.webui_manager import get_webui_manager

        manager = get_webui_manager()

    config = getattr(manager, "config", None)
    if config is None or not getattr(config, "enabled", False):
        return _unsupported(
            "webui_disabled",
            "WebUI manager is disabled; no interactive workspace runtime "
            "is available.",
        )

    # Linux only: macOS skips system-user creation (utils/workspace.py), so
    # Open ACE cannot establish or verify the identity mapping there; Windows
    # forces a single shared instance.
    if _current_platform() != "linux":
        return _unsupported(
            "platform_unsupported",
            "Per-user workspace isolation is supported on Linux deployments "
            "only; this platform runs a single shared WebUI instance.",
        )

    if not getattr(config, "multi_user_mode", False):
        return _unsupported(
            "multi_user_mode_disabled",
            "Multi-user workspace mode is disabled; the interactive workspace "
            "intentionally runs one shared instance.",
        )

    if not _is_docker_multi_user_mode():
        return _unsupported(
            "identity_mapping_unverified",
            "Multi-user mode is enabled, but this deployment cannot create "
            "or verify per-user system accounts; see deployment requirements "
            "in the workspace isolation documentation.",
        )

    return IsolationCapabilitySnapshot(
        supported=True,
        backend=BACKEND_PER_USER,
        isolation_level=ISOLATION_LEVEL_OS_USER,
        enforced=_OS_USER_ENFORCED,
        unsupported=_OS_USER_UNSUPPORTED,
        reasons=(),
    )


def evaluate_isolation_requirement(
    required_level: str,
    *,
    snapshot: IsolationCapabilitySnapshot,
    system_account: str | None,
    manager: Any,
) -> IsolationReason | None:
    """Gate an explicit isolation requirement before launching a workspace.

    Returns None when the requirement is satisfiable, else the structured
    rejection reason. ``system_account`` must be the explicit DB mapping
    (no username fallback) — a missing mapping is a rejection, not a
    silent convention-derived account (Issue #3374).
    """
    if not is_valid_isolation_level(required_level):
        return IsolationReason(
            "invalid_isolation_level",
            f"Unknown isolation level '{required_level}'; expected one of "
            f"{', '.join(SUPPORTED_ISOLATION_LEVELS)}.",
        )
    if required_level == ISOLATION_LEVEL_NONE:
        return None
    if not isolation_level_at_least(snapshot.isolation_level, required_level):
        return IsolationReason(
            "isolation_level_unsupported",
            f"Requested isolation level '{required_level}' exceeds what this "
            f"deployment enforces ('{snapshot.isolation_level}'); refusing to "
            "silently launch with weaker isolation.",
        )
    if not system_account:
        return IsolationReason(
            "identity_mapping_missing",
            "User has no system_account mapping; refusing to fall back to a "
            "convention-derived or shared account under an explicit isolation "
            "requirement.",
        )
    launch_ok, launch_reason = manager.supports_per_user_launch(system_account)
    if not launch_ok:
        return IsolationReason(
            "per_user_launch_unavailable",
            f"Cannot launch the WebUI under system account "
            f"'{system_account}' ({launch_reason}).",
        )
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_workspace_isolation_contract_3374.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/workspace_isolation_contract.py tests/unit/test_workspace_isolation_contract_3374.py
git commit -m "feat(#3374): add workspace isolation capability contract module"
```

---

### Task 2: 契约查询端点

**Files:**
- Create: `app/routes/workspace_isolation.py`
- Modify: `app/__init__.py`(`register_blueprints()` 内注册,与 feature_flags 注册处相邻)
- Test: `tests/unit/test_workspace_isolation_api_3374.py`

**Interfaces:**
- Consumes: Task 1 的 `build_workspace_isolation_snapshot()`;`app.auth.decorators.auth_required`。
- Produces: `GET /api/workspace/isolation-capabilities`(200=契约 JSON;401 未认证;500 构造失败)。

**测试机制说明(评审修正):** 路由调用 `build_workspace_isolation_snapshot()` 且
manager 缺省走 `get_webui_manager()` → 读宿主 `~/.open-ace/config.json` 且会启动
清理线程——**正向断言必须 patch 路由模块内的
`app.routes.workspace_isolation.build_workspace_isolation_snapshot`**(与
`tests/unit/test_feature_flags_api.py:39-42` 对路由模块依赖打 patch 的惯例一致),
不得依赖宿主配置。鉴权 patch 目标为 `app.auth.decorators._authenticate`(该装饰器
在 decorators 模块内部调用它,对齐 test_feature_flags_api.py 实证有效的机制)。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for the workspace isolation capability endpoint (Issue #3374)."""

from unittest.mock import patch

import pytest
from flask import Flask

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]

MOCK_SESSION = (True, {"user_id": 42, "username": "alice", "role": "user", "tenant_id": 1})


class _StubConfig:
    enabled = True
    multi_user_mode = True


class _StubManager:
    config = _StubConfig()


@pytest.fixture
def isolation_app():
    from app.routes.workspace_isolation import workspace_isolation_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(workspace_isolation_bp)
    return app


@pytest.fixture
def stub_snapshot():
    return wic.build_workspace_isolation_snapshot(_StubManager())


def test_requires_authentication(isolation_app):
    client = isolation_app.test_client()
    resp = client.get("/workspace/isolation-capabilities")
    assert resp.status_code == 401


def test_returns_contract_for_authenticated_user(isolation_app, stub_snapshot):
    client = isolation_app.test_client()
    with patch(
        "app.routes.workspace_isolation.build_workspace_isolation_snapshot",
        return_value=stub_snapshot,
    ), patch("app.auth.decorators._authenticate", return_value=MOCK_SESSION):
        resp = client.get(
            "/workspace/isolation-capabilities",
            headers={"Authorization": "Bearer test-token"},
        )
    assert resp.status_code == 200
    assert resp.get_json() == stub_snapshot.public_dict()


def test_internal_error_is_structured(isolation_app):
    client = isolation_app.test_client()
    with patch(
        "app.routes.workspace_isolation.build_workspace_isolation_snapshot",
        side_effect=RuntimeError("boom"),
    ), patch("app.auth.decorators._authenticate", return_value=MOCK_SESSION):
        resp = client.get(
            "/workspace/isolation-capabilities",
            headers={"Authorization": "Bearer test-token"},
        )
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Internal server error"}
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_workspace_isolation_api_3374.py -v`
Expected: FAIL(`ModuleNotFoundError: app.routes.workspace_isolation`)

- [ ] **Step 3: 实现蓝图与注册**

`app/routes/workspace_isolation.py`(鉴权装饰器的确切导入路径与用法对照
`app/routes/feature_flags.py`):

```python
"""Isolation capability contract endpoint for local interactive workspaces.

Issue #3374: admins and trusted integrators query this versioned contract
instead of relying on README claims or client-side capability booleans.
"""

import logging

from flask import Blueprint, jsonify

from app.auth.decorators import auth_required
from app.services.workspace_isolation_contract import (
    build_workspace_isolation_snapshot,
)

logger = logging.getLogger(__name__)

workspace_isolation_bp = Blueprint("workspace_isolation", __name__)


@workspace_isolation_bp.route("/workspace/isolation-capabilities", methods=["GET"])
@auth_required
def get_isolation_capabilities():
    try:
        snapshot = build_workspace_isolation_snapshot()
    except Exception as e:
        logger.error(f"Failed to build isolation capability snapshot: {e}")
        return jsonify({"error": "Internal server error"}), 500
    return jsonify(snapshot.public_dict())
```

`app/__init__.py` 的 `register_blueprints()` 中,紧随 feature_flags 注册处增加
(懒导入风格与该函数内现有注册保持一致):

```python
from app.routes.workspace_isolation import workspace_isolation_bp

app.register_blueprint(workspace_isolation_bp, url_prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_workspace_isolation_api_3374.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/routes/workspace_isolation.py app/__init__.py tests/unit/test_workspace_isolation_api_3374.py
git commit -m "feat(#3374): expose isolation capability contract endpoint"
```

---

### Task 3: WebUIManager 按用户启动判定

**Files:**
- Modify: `app/services/webui_manager.py`(新增公开方法,置于 `stop_user_webui` 附近;补充 `shutil` 导入——当前未导入,`pwd`/`os` 已有)
- Test: `tests/unit/test_webui_manager_per_user_launch_3374.py`

**Interfaces:**
- Consumes: 已有 `self._platform`(`platform.system().lower()`,webui_manager.py:222)、`self._find_webui_executable()`(返回 `(cmd|None, dir|None)`)。
- Produces: `WebUIManager.supports_per_user_launch(system_account: str) -> tuple[bool, str | None]`,Task 4 的 route 经真实 manager 调用。

**测试机制说明(评审修正):** 不设死属性注入点;`pwd.getpwuid` 与 `shutil.which`
统一经 `app.services.webui_manager` 命名空间 monkeypatch,保证每个分支被真实命中
("已是目标用户"分支不靠环境巧合)。探针对成功解析的记忆化补充一条行为测试。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for WebUIManager.supports_per_user_launch (Issue #3374)."""

import pytest

from app.services.webui_manager import WebUIManager

pytestmark = [pytest.mark.issue(3374)]


class _PwEntry:
    def __init__(self, pw_name):
        self.pw_name = pw_name


class _LaunchProbeManager(WebUIManager):
    """Bypass __init__ (config/env side effects); stub launch-path inputs."""

    def __init__(self, platform, webui_cmd, webui_dir):
        self._platform = platform
        self._webui_cmd = webui_cmd
        self._webui_dir = webui_dir
        self._resolved_webui = None  # probe memoization slot

    def _find_webui_executable(self):
        return self._webui_cmd, self._webui_dir


def _make(platform="linux", webui_cmd="/usr/local/bin/qwen-code-webui", webui_dir=None):
    return _LaunchProbeManager(platform, webui_cmd, webui_dir)


def _patch_identity(monkeypatch, current_user="open-ace", sudo="/usr/bin/sudo"):
    monkeypatch.setattr(
        "app.services.webui_manager.pwd.getpwuid",
        lambda uid: _PwEntry(current_user),
    )
    monkeypatch.setattr(
        "app.services.webui_manager.shutil.which",
        lambda name: sudo if name == "sudo" else None,
    )


def test_rejects_unsupported_platform():
    ok, reason = _make(platform="windows").supports_per_user_launch("alice_acct")
    assert ok is False and reason == "platform_unsupported"


def test_rejects_missing_executable(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make(webui_cmd=None).supports_per_user_launch("alice_acct")
    assert ok is False and reason == "webui_executable_missing"


def test_allows_when_already_target_user(monkeypatch):
    _patch_identity(monkeypatch, current_user="alice_acct")
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is True and reason is None


def test_rejects_dev_directory_mode_for_other_user(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make(webui_dir="/srv/qwen-code-webui").supports_per_user_launch("alice_acct")
    assert ok is False and reason == "dev_directory_mode_shared_account"


def test_rejects_when_sudo_missing(monkeypatch):
    _patch_identity(monkeypatch, sudo=None)
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is False and reason == "sudo_unavailable"


def test_allows_sudo_path_for_global_executable(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is True and reason is None


def test_successful_resolution_is_memoized(monkeypatch):
    _patch_identity(monkeypatch)
    manager = _make()
    calls = []

    def _counting_find():
        calls.append(1)
        return "/usr/local/bin/qwen-code-webui", None

    manager._find_webui_executable = _counting_find
    assert manager.supports_per_user_launch("alice_acct")[0] is True
    assert manager.supports_per_user_launch("bob_acct")[0] is True
    assert len(calls) == 1


def test_failed_resolution_is_not_memoized(monkeypatch):
    _patch_identity(monkeypatch)
    manager = _make(webui_cmd=None)
    ok1, reason1 = manager.supports_per_user_launch("alice_acct")
    manager._webui_cmd = "/usr/local/bin/qwen-code-webui"
    ok2, reason2 = manager.supports_per_user_launch("alice_acct")
    assert ok1 is False and reason1 == "webui_executable_missing"
    assert ok2 is True and reason2 is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_webui_manager_per_user_launch_3374.py -v`
Expected: FAIL(`AttributeError: ... has no attribute 'supports_per_user_launch'`)

- [ ] **Step 3: 实现**

`app/services/webui_manager.py`:顶部补充 `import shutil`(若未有);类内新增:

```python
def supports_per_user_launch(self, system_account: str) -> tuple[bool, str | None]:
    """Report whether a WebUI for ``system_account`` would run as that OS user.

    Issue #3374 isolation gate: multi-user mode must not silently run user
    WebUIs under the shared service account. Returns ``(True, None)`` when a
    per-user launch is possible, else ``(False, reason_code)``.

    Resolution of the WebUI executable is memoized on success only: probing
    may trigger an expensive build detection (see _find_webui_executable),
    and a successful resolution is stable for the process lifetime, while a
    failed one must stay re-probeable (e.g. webui installed later).
    """
    if self._platform not in ("linux", "darwin"):
        return False, "platform_unsupported"
    if getattr(self, "_resolved_webui", None):
        webui_cmd, webui_dir = self._resolved_webui
    else:
        webui_cmd, webui_dir = self._find_webui_executable()
        if webui_cmd:
            self._resolved_webui = (webui_cmd, webui_dir)
    if not webui_cmd:
        return False, "webui_executable_missing"
    try:
        current_user = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        return False, "current_user_unresolved"
    if current_user == system_account:
        return True, None
    if webui_dir:
        # Dev-directory mode runs `node` as the service user with no UID switch.
        return False, "dev_directory_mode_shared_account"
    if shutil.which("sudo") is None:
        return False, "sudo_unavailable"
    return True, None
```

`__init__` 中初始化 `self._resolved_webui: tuple[str, str | None] | None = None`
(与相邻实例属性一起;`_LaunchProbeManager` 测试类已在 `__init__` 覆盖里同步初始化
该槽位)。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_webui_manager_per_user_launch_3374.py tests/unit/test_webui_manager.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/webui_manager.py tests/unit/test_webui_manager_per_user_launch_3374.py
git commit -m "feat(#3374): add WebUIManager per-user launch probe"
```

---

### Task 4: user-url 门闸与生效策略回显

**Files:**
- Modify: `app/routes/workspace.py:2337-2430`(`get_user_webui_url`)
- Test: `tests/unit/test_user_url_isolation_gate_3374.py`

**Interfaces:**
- Consumes: Task 1 `build_workspace_isolation_snapshot` / `evaluate_isolation_requirement`;Task 3 `supports_per_user_launch`;路由内已有的 `UserRepository`、`get_webui_manager`。
- Produces: `GET /api/workspace/user-url[?required_isolation=none|os_user|sandboxed]`;400 拒绝体 `{success:false, error, error_code, reasons, isolation}`;成功体增量字段 `"isolation"`。

**测试机制说明(评审修正,关键点):**
1. `workspace.py:21-26` 在**模块顶层** `from app.auth.decorators import _load_user_from_token`,before_request(workspace.py:289)消费的是 `app.routes.workspace` 命名空间内已绑定的函数对象——**patch 目标必须是 `app.routes.workspace._load_user_from_token`**,patch 定义处对蓝图无效。
2. 请求必须携带凭证:常规用例用 `client.set_cookie("session_token", "test-token")`(对齐 conftest.py:89-92 admin_client 惯例);**跨 host 的 HTTPS 用例必须改用 `Authorization: Bearer` header**——werkzeug cookie jar 按域匹配,localhost 域 cookie 不会发送给 `base_url="https://example.com/"` 的请求,用 cookie 会得到 401 假失败。
3. `g.user` 的键形以 workspace before_request 实际消费为准(实现时读 workspace.py:274-292 确认;route 读 `g.user.get("id")`,stub 同时提供 `id` 与 `user_id` 双键以稳妥)。
4. `UserRepository` 与 `get_webui_manager` 在 route 函数体内局部导入——patch 定义处:`app.repositories.user_repo.UserRepository`、`app.services.webui_manager.get_webui_manager`。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for the user-url isolation gate (Issue #3374)."""

from unittest.mock import patch

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]

MOCK_USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}


class _Config:
    enabled = True
    multi_user_mode = True


class _StubManager:
    def __init__(self):
        self.config = _Config()
        self.launched_with = None

    def get_user_webui_url(self, user_id, system_account, host_url):
        self.launched_with = system_account
        return "http://127.0.0.1:3100", "tok"

    def update_user_activity(self, user_id):
        pass

    def supports_per_user_launch(self, system_account):
        return True, None


def _db_user(system_account):
    return {"id": 7, "username": "alice", "system_account": system_account}


def _call(client, query=""):
    client.set_cookie("session_token", "test-token")
    with patch("app.routes.workspace._load_user_from_token", return_value=MOCK_USER):
        return client.get(f"/api/workspace/user-url{query}")


def _patch_stack(repo_user, stub):
    user_patch = patch(
        "app.repositories.user_repo.UserRepository"
    )
    manager_patch = patch(
        "app.services.webui_manager.get_webui_manager", return_value=stub
    )
    repo_cls = user_patch.start()
    repo_cls.return_value.get_user_by_id.return_value = repo_user
    manager_patch.start()
    return user_patch, manager_patch


def _deployment_supported(monkeypatch):
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    monkeypatch.setattr(wic, "_is_docker_multi_user_mode", lambda: True)


def test_unauthenticated_request_is_401(app, client):
    resp = client.get("/api/workspace/user-url")
    assert resp.status_code == 401


def test_unknown_user_is_404(app, client):
    stub = _StubManager()
    patches = _patch_stack(None, stub)
    try:
        resp = _call(client)
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 404


@pytest.mark.regression
def test_default_path_preserves_username_fallback(app, client, monkeypatch):
    # 回归保护:显式隔离要求缺席时,username 回退(既有映射约定)必须保留。
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client)
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["system_account"] == "alice"  # username 回退保留
    assert stub.launched_with == "alice"
    assert body["isolation"]["local_workspace_multi_user"] == "supported"


def test_gate_rejects_missing_identity_mapping(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "identity_mapping_missing"
    assert body["isolation"]["policy_revision"] == wic.POLICY_REVISION
    assert stub.launched_with is None  # 未启动


def test_gate_rejects_unsupported_level(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=sandboxed")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "isolation_level_unsupported"


def test_gate_rejects_invalid_level(app, client):
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=strong")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "invalid_isolation_level"


def test_gate_rejects_shared_account_launch(app, client, monkeypatch):
    _deployment_supported(monkeypatch)

    class _SharedLaunchManager(_StubManager):
        def supports_per_user_launch(self, system_account):
            return False, "dev_directory_mode_shared_account"

    stub = _SharedLaunchManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "per_user_launch_unavailable"


def test_gate_passes_with_explicit_mapping(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["system_account"] == "alice_acct"
    assert stub.launched_with == "alice_acct"


@pytest.mark.regression
def test_https_multi_user_returns_relative_webui_path(app, client, monkeypatch):
    # 回归保护:HTTPS + 多用户时 /webui/<port>/ 相对路径分支不因插入点受破坏。
    # 凭证用 Authorization header 而非 cookie:werkzeug cookie jar 按域匹配,
    # localhost 域的 cookie 不会随 base_url="https://example.com/" 请求发送
    # (_extract_token() 依次读 cookie → Bearer header,header 与 host 无关)。
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    stub.get_user_webui_url = lambda user_id, sa, host: "http://10.0.0.1:3100", "tok"
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        with patch(
            "app.routes.workspace._load_user_from_token", return_value=MOCK_USER
        ):
            resp = client.get(
                "/api/workspace/user-url",
                base_url="https://example.com/",
                headers={"Authorization": "Bearer test-token"},
            )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["url"] == "/webui/3100/"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_user_url_isolation_gate_3374.py -v`
Expected: FAIL(`error_code` 缺失 / 200 而非 400 等;401/404/HTTPS 用例应已通过——它们是回归哨兵)

- [ ] **Step 3: 修改 `get_user_webui_url`**

在 `app/routes/workspace.py:2366`(`system_account = ...`)处改写并插入门闸。
先把函数体内 `from flask import request as flask_request`(现位于 :2374)上移到
`user_repo.get_user_by_id` 判空之后、`system_account` 解析之前;然后:

```python
        raw_system_account = user.get("system_account")
        system_account = raw_system_account or user.get("username")

        # Issue #3374: explicit isolation requirements are gated BEFORE the
        # username fallback — a missing identity mapping is a structured
        # rejection, never a silent convention-derived/shared account.
        required_isolation = flask_request.args.get("required_isolation")
        isolation_snapshot = build_workspace_isolation_snapshot(manager)
        if required_isolation is not None:
            rejection = evaluate_isolation_requirement(
                required_isolation,
                snapshot=isolation_snapshot,
                system_account=raw_system_account,
                manager=manager,
            )
            if rejection is not None:
                reasons = [r.public_dict() for r in isolation_snapshot.reasons]
                if not reasons:
                    reasons = [rejection.public_dict()]
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": rejection.message,
                            "error_code": rejection.code,
                            "reasons": reasons,
                            "isolation": isolation_snapshot.public_dict(),
                        }
                    ),
                    400,
                )
```

局部导入区(与 `UserRepository`/`get_webui_manager` 同处)增加:

```python
    from app.services.workspace_isolation_contract import (
        build_workspace_isolation_snapshot,
        evaluate_isolation_requirement,
    )
```

成功响应的 jsonify dict 中追加一行:

```python
                "isolation": isolation_snapshot.public_dict(),
```

- [ ] **Step 4: 运行确认通过 + 既有回归**

Run: `python -m pytest tests/unit/test_user_url_isolation_gate_3374.py tests/unit/test_workspace_isolation_contract_3374.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/routes/workspace.py tests/unit/test_user_url_isolation_gate_3374.py
git commit -m "feat(#3374): gate user-url launches on explicit isolation requirements"
```

---

### Task 5: 能力矩阵文档与全量验证

**Files:**
- Create: `docs/workspace-isolation-capabilities.md`
- Modify: 无代码

**Interfaces:**
- Consumes: 设计文档 §2.2/§2.4/§4.2/§4.4/§8(等级语义、入口矩阵、reason code 全集、探针取舍、后续路线)。
- Produces: 面向管理员/接入方的部署与验收文档(README 级用户文档,非 dev-note)。

- [ ] **Step 1: 撰写文档**

章节:
1. 能力契约字段与语义(对齐设计 §2.1);
2. 隔离等级定义(none、os_user、sandboxed 及明确边界;linux-only 说明,macOS/Windows 不在多用户隔离支持范围);
3. **reason code 全集对照**:契约 `reasons[]`(部署级 4 个)、门闸 `error_code`(请求级 4 个)、探针原因码(5 个),并说明 `identity_mapping_unverified` vs `identity_mapping_missing` 的区别;
4. 入口覆盖矩阵(设计 §2.4 表格,每行注明依据);
5. 多用户模式部署要求(docker-compose.multi-user.yml、root、`OPENACE_ALLOW_ROOT_MULTI_USER=1`、`WORKSPACE_BASE_DIR=/workspace`、useradd wrapper/sudo、无内核特殊要求——OS 用户由 useradd 创建;部署细节在此文档而非 API message 中);
6. 接入方用法(查询端点 + `required_isolation` 示例与各 error_code 语义、探针记忆化取舍说明);
7. 已知缺口与后续路线(设计 §8 六项)。

- [ ] **Step 2: 全量验证**

Run:
```bash
python -m pytest tests/unit/test_workspace_isolation_contract_3374.py \
  tests/unit/test_workspace_isolation_api_3374.py \
  tests/unit/test_webui_manager_per_user_launch_3374.py \
  tests/unit/test_user_url_isolation_gate_3374.py -v
python -m pytest tests/unit -k "user_url or webui or workspace" -q
python scripts/lint/check_root_docs.py
```
Expected: 全部 PASS;既有 webui/workspace 相关单测无回归;lint 通过(未新增根目录文档)。

- [ ] **Step 3: 提交**

```bash
git add docs/workspace-isolation-capabilities.md docs/superpowers/specs/2026-09-11-issue-3374-workspace-isolation-contract-design.md docs/superpowers/plans/2026-09-11-issue-3374-workspace-isolation-contract.md
git commit -m "docs(#3374): workspace isolation capability matrix and design notes"
```

---

## Self-Review 记录(修订 2)

- 覆盖检查:设计 §2(契约)→ Task 1;§3(端点)→ Task 2;§4.4(manager 方法)→ Task 3;§4.1-4.3(门闸/回显)→ Task 4;§5 文档 + reason code 全集 → Task 5。设计 §8 为显式非目标。
- 评审轮次 1(12 条)全部采纳;评审轮次 2(4 条)全部采纳:HTTPS 哨兵测试改用 Authorization header(避免 werkzeug cookie 域匹配导致 401 假失败)、Task 3 删除死代码 lambda 行、Task 3 Step 4 命令简化(direct 双文件运行)。
- 占位符检查:无 TBD/TODO;所有代码步骤含完整代码。
- 类型一致性:`IsolationReason.public_dict()`、`IsolationCapabilitySnapshot.public_dict()`、`supports_per_user_launch -> tuple[bool, str | None]`、`evaluate_isolation_requirement(...) -> IsolationReason | None` 在各任务间引用一致。
