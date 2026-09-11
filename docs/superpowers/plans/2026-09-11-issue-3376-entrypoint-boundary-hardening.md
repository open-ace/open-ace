# Issue #3376 入口边界加固 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 终端/VSCode 凭证与会话所有者绑定、终端 `work_dir` 与 VSCode `project_path` 服务端校验、`/fs/browse` 与 `/fs/check-path` home 子树锁(放行共享项目),消除 #3376 审计的三处水平越权。

**Architecture:** 终端所有权以 `agent_sessions` 行为权威(30 天 GC ≫ 24h store TTL → fail-closed 安全),允许集 = platform admin ∥ 同租户 tenant_admin ∥ 会话所有者;VSCode 在启动端点记录请求者(内存映射,单 web worker)、上报时消费,门闸与 #2183 代理鉴权同口径;`is_valid_path` 迁移到 `app/utils/path_guard.py` 供 remote.py 复用;fs 锁的允许根 = realpath 后的自己 home + 本租户 is_shared 项目路径。

**Tech Stack:** Flask 蓝图内修改,无新依赖;pytest unit lane(app fixture + patch)。

**Spec:** `docs/superpowers/specs/2026-09-11-issue-3376-entrypoint-boundary-hardening-design.md`

## Global Constraints

- 门闸允许集(两入口一致):`is_platform_admin_role`(strict 口径)∥ 所有者 ∥ 同租户 `tenant_admin` ∥(仅 VSCode)机器 admin 权限;跨租户 tenant_admin 一律 403。
- proxy-token 早退路径(status 内 `hmac.compare_digest` 分支)行为原样保留,返回完整 info。
- 标准用户路径的终端 status 响应必须剥离 `original_token`/`original_ws_url`;`token`/`ws_url` 保留。
- 共享项目(本租户 `is_shared` 且 `is_active`)根及其子树必须仍可 browse/check;`get_shared_project_paths(None) -> []`。
- fs 测试鉴权一律 `app.before_request_funcs["fs"] = []` + `@app.before_request` 注入 g.user(先例 test_fs_file_ops.py:142-155);目录一律在 `Path.home()` 下创建(macOS `/tmp` 在黑名单);**不得**改动模块级 `fs_bp` 回调表。
- remote 测试 patch 点:`app.routes.remote._set_user_from_token`、`app.routes.remote.get_remote_agent_manager`、`app.modules.workspace.session_manager.get_session_manager`、`app.routes.remote.get_api_key_proxy_service`、`app.routes.remote._check_legacy_fallback`;仓库名是 `ProjectRepository`(无 `ProjectRepo`)。
- 既有 `tests/unit/test_fs_path_validation.py`、`tests/unit/test_vscode_auth_2183.py`、`tests/unit/test_terminal_ws_handler.py` 必须零改动通过(回归哨兵)。
- 标记:统一 `pytest.mark.issue(3376)`;行为兼容用例追加 `pytest.mark.regression`。
- 分支 `fix/3376-entrypoint-boundary-hardening`(自 main);commit 前缀 `fix(#3376):`。
- 无 DB schema 变更,不触碰 migrations/schema。

---

### Task 1: `is_valid_path` 迁移到 path_guard

**Files:**
- Create: `app/utils/path_guard.py`
- Modify: `app/routes/fs.py`(删除原定义,改为导入;`BLACKLISTED_PATHS`/`_BLACKLISTED_RESOLVED` 若在 fs.py 其他位置被引用则一并导入保留名字)
- Test: `tests/unit/test_path_guard_3376.py`

**Interfaces:**
- Produces: `app.utils.path_guard.is_valid_path(path, allowed_prefixes=None) -> bool`、`BLACKLISTED_PATHS: list[str]`、`_BLACKLISTED_RESOLVED: set[str]`(签名与行为与 fs.py 现状逐字一致)。
- Consumes: 无(fs.py 现有定义)。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for app.utils.path_guard (Issue #3376 relocation)."""

import pytest

pytestmark = [pytest.mark.issue(3376)]


def test_is_valid_path_rejects_traversal_and_relative():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice/../../etc") is False
    assert is_valid_path("relative/path") is False
    assert is_valid_path("") is False


def test_is_valid_path_rejects_blacklisted_resolved():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/etc/passwd") is False
    assert is_valid_path("/var/log/x") is False


def test_is_valid_path_accepts_normal_absolute():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice/project") is True


def test_is_valid_path_prefix_boundary():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice", allowed_prefixes=["/home/alice"]) is True
    assert is_valid_path("/home/alice_evil", allowed_prefixes=["/home/alice"]) is False


def test_fs_module_reexports_shared_validator():
    """fs.py 保留模块级名字,既有 patch/引用不断(workspace.py:39 等)。"""
    import app.routes.fs as fs_mod
    import app.utils.path_guard as guard_mod

    assert fs_mod.is_valid_path is guard_mod.is_valid_path
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_path_guard_3376.py -v`
Expected: FAIL(`ModuleNotFoundError: app.utils.path_guard`)

- [ ] **Step 3: 实现**

`app/utils/path_guard.py`(内容自 fs.py:51-286 **逐字迁移** `BLACKLISTED_PATHS`、`_BLACKLISTED_RESOLVED`、`is_valid_path`,含原注释;文件头 docstring):

```python
"""Shared path validation for user-supplied filesystem paths.

Moved from app/routes/fs.py (Issue #3376) so the remote terminal/vscode
start endpoints can reuse the same traversal/blacklist semantics. Behavior
is identical to the previous fs.py definitions.
"""

from __future__ import annotations

import os
import platform

# [fs.py:51-70 BLACKLISTED_PATHS 原文]
BLACKLISTED_PATHS = [...]

# [fs.py:72-80 _BLACKLISTED_RESOLVED 原文]
_BLACKLISTED_RESOLVED = {...}


def is_valid_path(path: str, allowed_prefixes: list[str] | None = None) -> bool:
    """[fs.py:237-286 docstring 与实现原文]"""
    ...
```

fs.py:删除三处定义,头部导入区加 `from app.utils.path_guard import BLACKLISTED_PATHS, is_valid_path`(`_BLACKLISTED_RESOLVED` 若仍被引用则一并导入;`platform` 导入闲置时按 ruff 处理)。

- [ ] **Step 4: 运行确认通过 + 哨兵**

Run: `python -m pytest tests/unit/test_path_guard_3376.py tests/unit/test_fs_path_validation.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/utils/path_guard.py app/routes/fs.py tests/unit/test_path_guard_3376.py
git commit -m "fix(#3376): extract shared path validator into app/utils/path_guard"
```

---

### Task 2: 终端 work_dir / VSCode project_path 服务端校验

**Files:**
- Modify: `app/routes/remote.py`(顶部导入;`start_terminal` :3362 后;`remote_vscode_start` :4699 后)
- Test: `tests/unit/test_terminal_work_dir_validation_3376.py`

**Interfaces:**
- Consumes: Task 1 `is_valid_path`;`machine_access_required`(既有)。
- Produces: 两入口对非法路径返回 `400 {"success": false, "error": ...}` 且命令不下发;合法/空值行为不变。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for terminal/vscode start path validation (Issue #3376)."""

from unittest.mock import patch

import pytest
from flask import g

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-111111111111"
USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}


class _StubAgentMgr:
    def __init__(self):
        self.sent = None

    def get_machine(self, machine_id):
        return {"machine_name": "m", "hostname": "h", "tenant_id": 1, "created_by": 1}

    def is_agent_connected(self, machine_id):
        return True

    def send_command(self, machine_id, cmd):
        self.sent = cmd

    def get_backend_url(self, base):
        return "http://127.0.0.1:19888"

    def check_user_access(self, machine_id, user_id):
        return True

    def get_user_permission(self, machine_id, user_id):
        # machine_access_required 装饰器对非管理员调用(remote.py:957)
        return "user"


def _auth(monkeypatch):
    def _set_user():
        g.user = dict(USER)
        g.user_id = USER["id"]
        return True

    monkeypatch.setattr("app.routes.remote._set_user_from_token", _set_user)


@pytest.mark.parametrize("bad", ["../../etc", "relative/dir", "/etc/x", "/opt/tool"])
def test_terminal_invalid_work_dir_rejected(app, client, monkeypatch, bad):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch(
        "app.routes.remote.get_remote_agent_manager", return_value=agent_mgr
    ), patch(
        "app.modules.workspace.session_manager.get_session_manager"
    ) as sm_cls, patch(
        "app.routes.remote.get_api_key_proxy_service"
    ) as proxy_cls:
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/start",
            json={"machine_id": MACHINE_ID, "work_dir": bad},
        )
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert agent_mgr.sent is None


@pytest.mark.parametrize("bad", ["../../etc", "relative/dir", "/etc/x"])
def test_vscode_invalid_project_path_rejected(app, client, monkeypatch, bad):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch(
        "app.routes.remote.get_remote_agent_manager", return_value=agent_mgr
    ):
        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": bad},
        )
    assert resp.status_code == 400
    assert agent_mgr.sent is None


@pytest.mark.regression
def test_valid_and_empty_paths_pass_through(app, client, monkeypatch):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch(
        "app.routes.remote.get_remote_agent_manager", return_value=agent_mgr
    ), patch(
        "app.modules.workspace.session_manager.get_session_manager"
    ) as sm_cls, patch(
        "app.routes.remote.get_api_key_proxy_service"
    ) as proxy_cls:
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/start",
            json={"machine_id": MACHINE_ID, "work_dir": "/home/alice/proj"},
        )
        assert resp.status_code == 200
        assert agent_mgr.sent["work_dir"] == "/home/alice/proj"

        agent_mgr.sent = None
        resp = client.post(
            "/api/remote/terminal/start", json={"machine_id": MACHINE_ID}
        )
        assert resp.status_code == 200
        assert agent_mgr.sent["work_dir"] == ""

        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": "/home/alice/p"},
        )
        assert resp.status_code == 200
        assert agent_mgr.sent["command"] == "start_vscode"
```

(实现时若 stub 需补方法,以实际可跑为准补充——被测行为契约不变:非法 400 且未下发,合法/空透传。)

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_terminal_work_dir_validation_3376.py -v`
Expected: FAIL(非法路径当前 200)

- [ ] **Step 3: 实现**

remote.py 顶部导入区加 `from app.utils.path_guard import is_valid_path`。

`start_terminal` 在 `work_dir = data.get("work_dir")`(:3362)后:

```python
    # Issue #3376: server-side work_dir validation. Remote-machine paths are
    # not backend-local, so no base_dirs prefix applies here — enforce
    # absoluteness, no "..", and the resolved system-directory blacklist.
    if work_dir and not is_valid_path(work_dir):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid work_dir: must be an absolute path outside "
                    "system directories, without '..'",
                }
            ),
            400,
        )
```

`remote_vscode_start` 在 `if not project_path:` 校验(:4699-4700)后:

```python
    # Issue #3376: same remote-path semantics as terminal work_dir.
    if not is_valid_path(project_path):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid project_path: must be an absolute path "
                    "outside system directories, without '..'",
                }
            ),
            400,
        )
```

(注:project_path 已有非空校验,此处只需 is_valid_path。)

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_terminal_work_dir_validation_3376.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/routes/remote.py tests/unit/test_terminal_work_dir_validation_3376.py
git commit -m "fix(#3376): validate terminal work_dir and vscode project_path server-side"
```

---

### Task 3: 终端会话所有权(attach/status/stop)

**Files:**
- Modify: `app/routes/remote.py`(新增 `_check_terminal_session_access`;`attach_terminal`、`get_terminal_status`、`stop_terminal` 接线;status 剥离 original_*)
- Test: `tests/unit/test_terminal_ownership_3376.py`

**Interfaces:**
- Consumes: `get_session_manager().get_session(terminal_id) -> AgentSession | None`(`.user_id`/`.tenant_id`);`is_platform_admin_role`(app.auth.permissions)。
- Produces: `_check_terminal_session_access(terminal_id) -> tuple[Response, int] | None`;允许集 = platform admin ∥ 同租户 tenant_admin ∥ 所有者。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for terminal session ownership (Issue #3376)."""

from unittest.mock import patch

import pytest
from flask import g

from app.modules.workspace.terminal_store import terminal_info_store

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-222222222222"
TERM_ID = "0b6a2b6e-8f0b-4f5c-9c1e-333333333333"

OWNER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
OTHER = {"id": 9, "user_id": 9, "username": "mallory", "role": "user", "tenant_id": 1}
TENANT_ADMIN = {"id": 5, "user_id": 5, "username": "ta", "role": "tenant_admin", "tenant_id": 1}
CROSS_TA = {"id": 6, "user_id": 6, "username": "xta", "role": "tenant_admin", "tenant_id": 2}
PLATFORM_ADMIN = {"id": 1, "user_id": 1, "username": "root", "role": "platform_admin", "tenant_id": 1}


class _StubAgentMgr:
    def get_machine(self, machine_id):
        return {"tenant_id": 1, "created_by": 1}

    def check_user_access(self, machine_id, user_id):
        return True

    def get_user_permission(self, machine_id, user_id):
        # machine_access_required 装饰器对非管理员调用(remote.py:957)
        return "user"

    def send_command(self, machine_id, cmd):
        pass

    def get_backend_url(self, base):
        return "http://127.0.0.1:19888"


class _StubSession:
    def __init__(self, user_id, tenant_id=1):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.context = {}


def _auth(monkeypatch, user):
    def _set_user():
        g.user = dict(user)
        g.user_id = user["id"]
        return True

    monkeypatch.setattr("app.routes.remote._set_user_from_token", _set_user)


def _with_session(user_id, tenant_id=1):
    sm = type("SM", (), {})()
    sm.get_session = (
        lambda sid, **kw: _StubSession(user_id, tenant_id) if user_id is not None else None
    )
    return patch(
        "app.modules.workspace.session_manager.get_session_manager", return_value=sm
    )


@pytest.fixture
def stored_terminal():
    terminal_info_store.put(
        MACHINE_ID,
        TERM_ID,
        {
            "status": "running",
            "ws_url": f"/api/remote/terminal/{TERM_ID}/ws",
            "token": "browser-token",
            "original_ws_url": "ws://10.0.0.1:42000/ws",
            "original_token": "agent-side-token",
        },
    )
    yield
    terminal_info_store.pop(MACHINE_ID, TERM_ID)


def _mgr(recorder):
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: recorder.append(cmd)
    return mgr


def _status(client):
    return client.get(f"/api/remote/terminal/{TERM_ID}/status?machine_id={MACHINE_ID}")


def _attach(client):
    return client.post(
        f"/api/remote/terminal/{TERM_ID}/attach", json={"machine_id": MACHINE_ID}
    )


def _stop(client):
    # 实际路由:POST /api/remote/terminal/stop,body 含 terminal_id(remote.py:3646-3658)
    return client.post(
        "/api/remote/terminal/stop",
        json={"terminal_id": TERM_ID, "machine_id": MACHINE_ID},
    )


def test_status_owner_gets_info_without_agent_credentials(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OWNER)
    with _with_session(7), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
    ):
        resp = _status(client)
    assert resp.status_code == 200
    term = resp.get_json()["terminal"]
    assert term["token"] == "browser-token"
    assert "original_token" not in term and "original_ws_url" not in term


def test_status_non_owner_machine_user_is_403(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    with _with_session(7), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
    ):
        resp = _status(client)
    assert resp.status_code == 403


def test_status_cross_tenant_tenant_admin_is_403(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, CROSS_TA)
    with _with_session(7, tenant_id=1), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
    ):
        resp = _status(client)
    assert resp.status_code == 403


def test_status_same_tenant_tenant_admin_and_platform_admin_allowed(
    app, client, monkeypatch, stored_terminal
):
    for user in (TENANT_ADMIN, PLATFORM_ADMIN):
        _auth(monkeypatch, user)
        with _with_session(7), patch(
            "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
        ):
            resp = _status(client)
        assert resp.status_code == 200, user["username"]


def test_status_missing_session_fail_closed(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    with _with_session(None), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
    ):
        resp = _status(client)
    assert resp.status_code == 404


@pytest.mark.regression
def test_status_proxy_token_path_returns_full_info(app, client, monkeypatch, stored_terminal):
    # 内部 WS 代理(多 Pod 重定向)凭 browser token 本身认证,响应保留 original_*
    _auth(monkeypatch, OTHER)
    with _with_session(7), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
    ):
        client.set_cookie("session_token", "browser-token")
        resp = _status(client)
    assert resp.status_code == 200
    term = resp.get_json()["terminal"]
    assert term["original_token"] == "agent-side-token"


def test_attach_non_owner_403_and_no_command(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    sent = []
    with _with_session(7), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_mgr(sent)
    ), patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls:
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        resp = _attach(client)
    assert resp.status_code == 403
    assert sent == []


def test_attach_owner_succeeds(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OWNER)
    sent = []
    with _with_session(7), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_mgr(sent)
    ), patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls:
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        resp = _attach(client)
    assert resp.status_code == 200
    assert sent and sent[0]["command"] == "attach_terminal"


def test_stop_non_owner_403_and_no_command(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    sent = []
    with _with_session(7), patch(
        "app.routes.remote.get_remote_agent_manager", return_value=_mgr(sent)
    ), patch(
        "app.modules.workspace.session_manager.get_session_manager"
    ) as sm_cls:
        sm_cls.return_value.get_session.return_value = _StubSession(7)
        sm_cls.return_value.complete_session.return_value = True
        resp = _stop(client)
    assert resp.status_code == 403
    assert sent == []
```

(stop_terminal 带 `@machine_access_required`,stub 的 `get_user_permission` 已就位;实现时读 remote.py:3646-3678 核对 body 字段与 complete_session 调用形态,被测契约不变:非所有者 403 且 stop 命令未下发、会话未被 complete。)

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_terminal_ownership_3376.py -v`
Expected: FAIL(非所有者/跨租户当前 200)

- [ ] **Step 3: 实现**

remote.py 在 Terminal Management 区块前新增:

```python
def _check_terminal_session_access(terminal_id: str):
    """Issue #3376: terminal endpoints require session ownership.

    Allowed: platform admin (strict-role check), same-tenant tenant admin,
    the session owner. The agent_sessions row is created by start_terminal
    and outlives the in-memory terminal info (30-day session GC vs 24h
    store TTL), so a missing row fails closed with 404. Returns an error
    response tuple or None when access is allowed.
    """
    from app.auth.permissions import is_platform_admin_role

    if is_platform_admin_role(g.user.get("role")):
        return None
    from app.modules.workspace.session_manager import get_session_manager

    session = get_session_manager().get_session(terminal_id)
    if session is None:
        logger.warning(
            "Terminal ownership denied: no session record for %s (user_id=%s)",
            terminal_id[:8],
            g.user.get("id"),
        )
        return jsonify({"error": "Terminal session not found"}), 404
    if g.user.get("role") == "tenant_admin" and (
        session.tenant_id is None or session.tenant_id == g.user.get("tenant_id")
    ):
        return None
    if session.user_id != g.user.get("id"):
        logger.warning(
            "Terminal ownership denied: user_id=%s is not owner %s of %s",
            g.user.get("id"),
            session.user_id,
            terminal_id[:8],
        )
        return jsonify({"error": "Access denied"}), 403
    return None
```

接线(三处,**函数级缩进 4 空格**,位于 `if not User.is_admin_role(...)` 机器检查整块之后):
- `attach_terminal`:块结束后插入 `ownership_error = _check_terminal_session_access(terminal_id); if ownership_error is not None: return ownership_error`;
- `stop_terminal`(3646-3678,实现时先读原函数):同上,插在其访问检查之后、`send_command`/`complete_session` 之前;
- `get_terminal_status`:标准路径末尾(:3888-3890)替换为:

```python
    # Issue #3376: session ownership; machine access alone is not enough.
    ownership_error = _check_terminal_session_access(terminal_id)
    if ownership_error is not None:
        return ownership_error

    if info:
        # Issue #3376: agent-side bridge credentials never go to browsers.
        public_info = {
            k: v for k, v in info.items() if k not in ("original_token", "original_ws_url")
        }
        return jsonify({"success": True, "terminal": public_info})
    return jsonify({"success": True, "terminal": {"status": "unknown"}})
```

(proxy-token 早退块 :3853-3860 原样不动。)

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_terminal_ownership_3376.py tests/unit/test_terminal_ws_handler.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/routes/remote.py tests/unit/test_terminal_ownership_3376.py
git commit -m "fix(#3376): bind terminal status/attach/stop to session owner, strip bridge credentials"
```

---

### Task 4: VSCode 所有权 = 请求者

**Files:**
- Modify: `app/modules/workspace/vscode_store.py`(新增 `VSCodeOwnerStore` + 单例 `vscode_owner_store`;该文件已顶层导入 threading/time)
- Modify: `app/routes/remote.py`(`remote_vscode_start` 记录;`vscode_status` 上报经 `_resolve_vscode_reported_owner`;`_check_vscode_session_access` 门闸接 status/stop/attach)
- Test: `tests/unit/test_vscode_ownership_3376.py`

**Interfaces:**
- Produces: `vscode_owner_store.record(vscode_id, machine_id, user_id, tenant_id)` / `.pop(vscode_id) -> tuple[str, int, int | None] | None`(消费制;缺失/过期/机器不匹配 → None);`_resolve_vscode_reported_owner(agent_mgr, machine_id, vscode_id) -> int | None`;`_check_vscode_session_access(machine_id, vscode_id, info) -> tuple | None`。
- Consumes: `vscode_info_store.find_by_vscode_id / mark_stopped`、`VSCODE_SESSION_TTL`;`is_platform_admin_role`;`get_user_permission(machine_id, user_id)`;`_check_legacy_fallback`(仅测试 patch)。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for VSCode session ownership (Issue #3376)."""

import time
from unittest.mock import patch

import pytest
from flask import g

from app.modules.workspace.vscode_store import vscode_info_store, vscode_owner_store

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-444444444444"
VS_ID = "0b6a2b6e-8f0b-4f5c-9c1e-555555555555"

OWNER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
OTHER = {"id": 9, "user_id": 9, "username": "mallory", "role": "user", "tenant_id": 1}
TENANT_ADMIN = {"id": 5, "user_id": 5, "username": "ta", "role": "tenant_admin", "tenant_id": 1}
CROSS_TA = {"id": 6, "user_id": 6, "username": "xta", "role": "tenant_admin", "tenant_id": 2}
PLATFORM_ADMIN = {"id": 1, "user_id": 1, "username": "root", "role": "platform_admin", "tenant_id": 1}


class _StubAgentMgr:
    def __init__(self, created_by=1):
        self._created_by = created_by

    def get_machine(self, machine_id):
        return {"tenant_id": 1, "created_by": self._created_by}

    def is_agent_connected(self, machine_id):
        return True

    def send_command(self, machine_id, cmd):
        pass

    def get_user_permission(self, machine_id, user_id):
        return "user"


def _running_info(owner_user_id=7, tenant_id=1):
    return {
        "status": "running",
        "original_http_url": "http://10.0.0.1:8080",
        "original_token": "ot",
        "cs_password": "pw",
        "token": "browser-token",
        "machine_id": MACHINE_ID,
        "project_path": "/p",
        "owner_user_id": owner_user_id,
        "tenant_id": tenant_id,
        "created_at": time.time(),
        "expires_at": time.time() + 3600,
    }


def _auth(monkeypatch, user):
    def _set_user():
        g.user = dict(user)
        g.user_id = user["id"]
        return True

    monkeypatch.setattr("app.routes.remote._set_user_from_token", _set_user)


@pytest.fixture
def running_session():
    vscode_info_store.put(MACHINE_ID, VS_ID, _running_info())
    yield
    vscode_info_store.mark_stopped(MACHINE_ID, VS_ID)


# --- owner store ---


def test_owner_store_record_pop_roundtrip():
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    assert vscode_owner_store.pop(VS_ID) == (MACHINE_ID, 7, 1)
    assert vscode_owner_store.pop(VS_ID) is None  # consumed


def test_owner_store_rejects_machine_mismatch():
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    assert (
        vscode_owner_store.pop("00000000-0000-0000-0000-999999999999") is None
    )
    assert vscode_owner_store.pop(VS_ID) is None  # mismatch consumed defensively


# --- reported owner resolution ---


def test_resolve_owner_prefers_recorded_requester():
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    from app.routes.remote import _resolve_vscode_reported_owner

    assert (
        _resolve_vscode_reported_owner(_StubAgentMgr(created_by=1), MACHINE_ID, VS_ID) == 7
    )


def test_resolve_owner_falls_back_to_machine_creator():
    from app.routes.remote import _resolve_vscode_reported_owner

    assert (
        _resolve_vscode_reported_owner(_StubAgentMgr(created_by=3), MACHINE_ID, VS_ID) == 3
    )


# --- report wiring via agent_message ---


def test_agent_message_running_report_uses_requester_as_owner(app, client, monkeypatch):
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    mgr = _StubAgentMgr()
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr), patch(
        "app.routes.remote._check_legacy_fallback", return_value=(True, None)
    ):
        resp = client.post(
            "/api/remote/agent/message",
            json={
                "type": "vscode_status",
                "machine_id": MACHINE_ID,
                "vscode_id": VS_ID,
                "status": "running",
                "http_url": "http://10.0.0.1:8080",
                "token": "ot",
                "cs_password": "pw",
                "project_path": "/p",
            },
        )
    assert resp.status_code == 200
    found = vscode_info_store.find_by_vscode_id(VS_ID)
    assert found is not None
    assert found[1]["owner_user_id"] == 7
    vscode_info_store.mark_stopped(MACHINE_ID, VS_ID)


# --- endpoint gates ---


def _status(client):
    return client.get(f"/api/remote/vscode/{VS_ID}/status")


def test_status_owner_gets_url_with_token(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OWNER)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 200
    assert "token=" in resp.get_json().get("url", "")


def test_status_non_owner_403_without_url(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OTHER)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 403
    assert "url" not in resp.get_json()


def test_status_tenant_admin_and_platform_admin_allowed(app, client, monkeypatch, running_session):
    for user in (TENANT_ADMIN, PLATFORM_ADMIN):
        _auth(monkeypatch, user)
        with patch(
            "app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()
        ):
            resp = _status(client)
        assert resp.status_code == 200, user["username"]


def test_status_cross_tenant_tenant_admin_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, CROSS_TA)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 403


def test_stop_non_owner_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OTHER)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(
            "/api/remote/vscode/stop",
            json={"machine_id": MACHINE_ID, "vscode_id": VS_ID},
        )
    assert resp.status_code == 403
    assert sent == []


def test_attach_non_owner_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OTHER)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(
            f"/api/remote/vscode/{VS_ID}/attach", json={"machine_id": MACHINE_ID}
        )
    assert resp.status_code == 403
    assert sent == []


def test_attach_cross_tenant_tenant_admin_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, CROSS_TA)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(
            f"/api/remote/vscode/{VS_ID}/attach", json={"machine_id": MACHINE_ID}
        )
    assert resp.status_code == 403
    assert sent == []


@pytest.mark.regression
def test_start_records_requester_as_owner(app, client, monkeypatch):
    _auth(monkeypatch, OWNER)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": "/home/alice/p"},
        )
    assert resp.status_code == 200
    vscode_id = resp.get_json()["vscode_id"]
    assert vscode_owner_store.pop(vscode_id) == (MACHINE_ID, 7, 1)
```

(agent_message 的 body 字段名以 remote.py:2865+ 实际读取为准,实现时先核对再定 payload;被测契约:上报后 store 的 `owner_user_id == 记录的请求者`。)

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_vscode_ownership_3376.py -v`
Expected: FAIL(store/助手/门闸不存在)

- [ ] **Step 3: 实现**

(a) `app/modules/workspace/vscode_store.py` 末尾(threading/time 已顶层导入):

```python
class VSCodeOwnerStore:
    """Issue #3376: remember which user requested a VSCode session.

    /vscode/start knows the requester; the agent later reports 'running'
    via /api/remote/agent/message, which only has machine context. This
    in-memory bridge links the two (single web worker: gunicorn
    --workers 1 with the gevent worker class). Absent entries fall back
    to machine.created_by at report time.
    """

    def __init__(self, ttl: float = VSCODE_SESSION_TTL):
        self._lock = threading.Lock()
        self._ttl = ttl
        # vscode_id -> (machine_id, user_id, tenant_id, recorded_at)
        self._owners: dict[str, tuple[str, int, int | None, float]] = {}

    def record(self, vscode_id: str, machine_id: str, user_id: int, tenant_id: int | None) -> None:
        with self._lock:
            self._owners[vscode_id] = (machine_id, user_id, tenant_id, time.time())

    def pop(self, vscode_id: str) -> tuple[str, int, int | None] | None:
        """Consume the recorded owner; None when absent/expired.

        A lookup for a different machine consumes the entry defensively
        (never returns a cross-machine owner).
        """
        with self._lock:
            entry = self._owners.pop(vscode_id, None)
            if not entry:
                return None
            machine_id, user_id, tenant_id, recorded_at = entry
            if time.time() - recorded_at > self._ttl:
                return None
            return machine_id, user_id, tenant_id

    def cleanup_stale(self) -> int:
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._owners.items() if now - v[3] > self._ttl]
            for k in stale:
                del self._owners[k]
            return len(stale)


vscode_owner_store = VSCodeOwnerStore()
```

(b) remote.py 模块级新增:

```python
def _resolve_vscode_reported_owner(agent_mgr, machine_id: str, vscode_id: str) -> int | None:
    """Issue #3376: prefer the recorded requester over machine.created_by."""
    from app.modules.workspace.vscode_store import vscode_owner_store

    pending = vscode_owner_store.pop(vscode_id)
    if pending and pending[0] == machine_id:
        return pending[1]
    if pending:
        logger.info(
            "VSCode %s owner record machine mismatch; falling back to creator",
            vscode_id[:8],
        )
    machine = agent_mgr.get_machine(machine_id) or {}
    return machine.get("created_by")


def _check_vscode_session_access(machine_id: str, vscode_id: str, info: dict):
    """Issue #3376: VSCode endpoints follow the #2183 proxy-auth role set.

    Allowed: platform admin, session owner, same-tenant tenant admin,
    machine-level admin permission. Everyone else (including cross-tenant
    tenant admins) gets 403. Returns an error response tuple or None.
    """
    from app.auth.permissions import is_platform_admin_role

    if is_platform_admin_role(g.user.get("role")):
        return None
    session_tenant = info.get("tenant_id")
    user_tenant = g.user.get("tenant_id")
    if session_tenant is not None and user_tenant != session_tenant:
        audit_logger.log(
            action="CROSS_TENANT_VSCODE_ACCESS_ATTEMPT",
            severity="warning",
            user_id=g.user.get("id"),
            details={
                "user_tenant_id": user_tenant,
                "target_tenant_id": session_tenant,
                "vscode_id": vscode_id[:8],
            },
        )
        logger.warning(
            "VSCode ownership denied (cross-tenant): user_id=%s, session tenant=%s",
            g.user.get("id"),
            session_tenant,
        )
        return jsonify({"error": "Access denied"}), 403
    if g.user.get("id") == info.get("owner_user_id"):
        return None
    if g.user.get("role") == "tenant_admin" and (
        session_tenant is None or user_tenant == session_tenant
    ):
        return None
    if (
        get_remote_agent_manager().get_user_permission(machine_id, g.user.get("id"))
        == "admin"
    ):
        return None
    logger.warning(
        "VSCode ownership denied: user_id=%s is not owner %s",
        g.user.get("id"),
        info.get("owner_user_id"),
    )
    return jsonify({"error": "Access denied"}), 403
```

(audit 写法对齐 remote.py:4946-4955 的既有 CROSS_TENANT_VSCODE_ACCESS_ATTEMPT 事件。)

(c) `remote_vscode_start`:`vscode_id = str(uuid.uuid4())` 后:

```python
    # Issue #3376: remember the requester; the agent's async 'running'
    # report only carries machine context.
    vscode_owner_store.record(vscode_id, machine_id, g.user["id"], g.user.get("tenant_id"))
```

(d) `vscode_status` 上报分支:`owner_user_id = machine.get("created_by")`(:2905)替换为:

```python
            owner_user_id = _resolve_vscode_reported_owner(agent_mgr, machine_id_for_vs, vscode_id)
```

(machine 查询保留供 tenant_id;`_resolve...` 内部再查一次 machine 可接受,或把已查的 machine 传入——以最小 diff 实现并保持助手签名与本计划 (b) 一致。)

(e) `remote_vscode_status`:在 `if not User.is_admin_role(...)` 机器检查**整块之后、函数级缩进(4 空格)**插入:

```python
    gate = _check_vscode_session_access(machine_id, vscode_id, info)
    if gate is not None:
        return gate
```

(f) `remote_vscode_stop`:`vscode_id`/`machine_id` 校验后、send_command 前(函数级缩进):

```python
    from app.modules.workspace.vscode_store import vscode_info_store

    found = vscode_info_store.find_by_vscode_id(vscode_id)
    if found:
        found_machine_id, found_info = found
        gate = _check_vscode_session_access(found_machine_id, vscode_id, found_info)
        if gate is not None:
            return gate
```

(g) `remote_vscode_attach`:同 (e)——机器检查整块之后、函数级缩进插入同一门闸(info 经 `find_by_vscode_id`;未找到时维持现状放行)。

**插入点纪律(评审 B4)**:(e)(g) 的门闸必须在 `if not User.is_admin_role(...):` 块**外**。若误入块内(8 空格),tenant_admin 会整块跳过门闸——`test_attach_cross_tenant_tenant_admin_403` 与 `test_status_cross_tenant_tenant_admin_403` 就是防这个的,不得删除。

- [ ] **Step 4: 运行确认通过 + 既有回归**

Run: `python -m pytest tests/unit/test_vscode_ownership_3376.py tests/unit/test_vscode_auth_2183.py -v`
Expected: 全部 PASS(#2183 用例若依赖 created_by=owner 语义,回退路径保底)

- [ ] **Step 5: 提交**

```bash
git add app/modules/workspace/vscode_store.py app/routes/remote.py tests/unit/test_vscode_ownership_3376.py
git commit -m "fix(#3376): bind vscode sessions to the requesting user"
```

---

### Task 5: fs browse/check-path home 子树锁

**Files:**
- Modify: `app/repositories/project_repo.py`(新增 `ProjectRepository.get_shared_project_paths`)
- Modify: `app/routes/fs.py`(`_allowed_roots_for_user`、`_is_within_any_root`;browse/check-path 接线)
- Test: `tests/unit/test_fs_home_lock_3376.py`

**Interfaces:**
- Consumes: `get_home_directory(user)`、`get_workspace_base_dirs()`、`is_valid_path`(Task 1);`self._normalize_tenant_id`(repo 既有)。
- Produces: `ProjectRepository.get_shared_project_paths(tenant_id=None) -> list[str]`(**None → []**;realpath 去重)。

**测试基建纪律(评审 B1/R8)**:
1. 目录一律在 `Path.home()` 下创建(macOS `/tmp` realpath 落黑名单,`/tmp` 作 base_dirs 会让**旧门**先拒——新门的红/绿信号失真;先例 test_fs_file_ops.py:118-129);**CI 不得以 root 运行本文件**(root 的 `Path.home()=/root` 在黑名单,同理信号失真);
2. fs 鉴权用独立 Flask app + `app.before_request_funcs["fs"] = []` + `@app.before_request` 注入 `g.user`(先例 test_fs_file_ops.py:132-155;蓝图是模块级单例,不得改 `fs_bp` 回调表);
3. `get_directory_info` stub 必须含 `is_readable` 键(fs.py:647 消费)。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for fs browse/check-path home subtree lock (Issue #3376)."""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.issue(3376)]

USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
_NO_TENANT_USER = {"id": 8, "user_id": 8, "username": "bob", "role": "user", "tenant_id": None}
_CURRENT_USER: dict = {}


def _switch_user(user):
    _CURRENT_USER.clear()
    _CURRENT_USER.update(user)


@pytest.fixture
def workspace(tmp_path_factory):
    """Throwaway dirs under the real home (non-blacklisted)."""
    ws = Path.home() / ".ace_fs_lock_test_3376"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    home = ws / "home"
    shared = ws / "shared-proj"
    other = ws / "someone-else"
    home.mkdir(parents=True)
    shared.mkdir()
    other.mkdir()
    yield ws, home, shared, other
    shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def fs_app(workspace):
    from app.routes.fs import fs_bp

    ws, home, shared, other = workspace
    _switch_user(USER)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(fs_bp, url_prefix="/api")

    # 先例 test_fs_file_ops.py:142-155:app 级清空蓝图回调,注入 g.user
    app.before_request_funcs["fs"] = []

    @app.before_request
    def _set_user():
        g.user = dict(_CURRENT_USER)

    with (
        patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws)]),
        patch("app.routes.fs.get_home_directory", return_value=str(home)),
        patch(
            "app.repositories.project_repo.ProjectRepository.get_shared_project_paths",
            lambda self, tenant_id=None: [str(shared)] if tenant_id == 1 else [],
        ),
        patch(
            "app.routes.fs.get_directory_info",
            lambda path, sa: {
                "exists": os.path.isdir(path),
                "is_dir": True,
                "is_readable": True,
                "is_writable": True,
            },
        ),
        patch(
            "app.routes.fs.list_subdirectories",
            lambda path, sa, include_files=False: {"directories": [], "files": []},
        ),
    ):
        yield app


def _browse(client, path):
    return client.get(f"/api/fs/browse?path={path}")


def test_browse_outside_home_and_shared_rejected(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = _browse(client, str(other))
    assert resp.status_code == 400


@pytest.mark.regression
def test_browse_home_and_shared_allowed(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    assert _browse(client, str(home)).status_code == 200
    assert _browse(client, str(shared)).status_code == 200
    assert _browse(client, "home").status_code == 200


def test_browse_no_tenant_user_home_only(fs_app, workspace):
    # tenant_id 为 None 的用户不放大到全部共享(评审 N10:None -> [])
    ws, home, shared, other = workspace
    _switch_user(_NO_TENANT_USER)
    client = fs_app.test_client()
    assert _browse(client, str(home)).status_code == 200
    resp = _browse(client, str(shared))
    # shared 由 repo stub 按 tenant_id==1 过滤,None 拿不到
    assert resp.status_code == 400


def test_check_path_outside_home_and_shared_rejected(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(other)})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] is False


@pytest.mark.regression
def test_check_path_shared_root_allowed(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(shared)})
    assert resp.status_code == 200


def test_browse_symlinked_home_allowed(fs_app, workspace):
    # 评审 B2:home 根必须 realpath,符号链接 home 不能误拒
    ws, home, shared, other = workspace
    link = ws / "home-link"
    if not link.exists():
        link.symlink_to(home)
    with patch("app.routes.fs.get_home_directory", return_value=str(link)):
        client = fs_app.test_client()
        assert _browse(client, str(home)).status_code == 200
```

(注:`fs_app` fixture 注册单一 `before_request`,经 `_switch_user` 切换身份;其余用例默认 alice,`test_browse_no_tenant_user_home_only` 切到 bob 后如影响后续用例,在用例末尾切回或加 autouse 恢复——实现时保持单一注册点,不再叠加第二个 hook。)

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_fs_home_lock_3376.py -v`
Expected: FAIL(越界路径 `other` 在旧门下合法,当前 200——注意:必须以 `other` 位于 `ws` 内且不在黑名单来保证旧门放行,这是红信号的来源)

- [ ] **Step 3: 实现**

`project_repo.py` 在 `get_project_by_path` 后新增:

```python
    def get_shared_project_paths(self, tenant_id: int | None = None) -> list[str]:
        """Get realpath'd paths of active shared projects (Issue #3376).

        Used by the fs browse/check-path home lock to keep explicitly
        shared projects reachable for tenant members. A None tenant_id
        yields no shared roots (home only) rather than every tenant's.
        """
        import os

        normalized_tenant_id = self._normalize_tenant_id(tenant_id)
        if normalized_tenant_id is None:
            return []
        query = (
            "SELECT path FROM projects "
            "WHERE is_active IS TRUE AND is_shared IS TRUE AND tenant_id = ?"
        )
        rows = self.db.fetch_all(query, (normalized_tenant_id,)) or []
        paths: list[str] = []
        for row in rows:
            raw = (row.get("path") if isinstance(row, dict) else row) or ""
            if raw:
                resolved = os.path.realpath(raw)
                if resolved not in paths:
                    paths.append(resolved)
        return paths
```

`fs.py` 在 `_resolve_user_owned_path` 附近新增:

```python
def _is_within_any_root(resolved: str, roots: list[str]) -> bool:
    """Boundary-safe containment: equal or beneath (path-separator aware)."""
    for root in roots:
        if root and (resolved == root or resolved.startswith(root + os.sep)):
            return True
    return False


def _allowed_roots_for_user(user) -> list[str]:
    """Issue #3376: roots a user may browse/check — own home plus the
    tenant's explicitly shared project paths. Both realpath'd so they
    compare equal against already-resolved request paths."""
    roots = [os.path.realpath(get_home_directory(user))]
    try:
        from app.repositories.project_repo import ProjectRepository

        tenant_id = (user or {}).get("tenant_id")
        if tenant_id is not None:
            roots.extend(ProjectRepository().get_shared_project_paths(tenant_id))
    except Exception as e:
        logger.warning("Failed to load shared project roots: %s", e)
    return roots
```

`api_browse_directory`:在 `path = os.path.realpath(path)`(:618)后:

```python
        # Issue #3376: home subtree lock; explicitly shared project roots
        # stay reachable (read-side parity with the #1813 write lock).
        if not _is_within_any_root(path, _allowed_roots_for_user(user)):
            return (
                jsonify(
                    {"error": "Path must be inside your home directory or a shared project"}
                ),
                400,
            )
```

`api_check_path`:在 `path = os.path.realpath(path)`(:907)后:

```python
    if not _is_within_any_root(path, _allowed_roots_for_user(user)):
        return (
            jsonify(
                {
                    "valid": False,
                    "error": "Path must be inside your home directory or a shared project. "
                    f"Provided path: {path}",
                }
            ),
            400,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_fs_home_lock_3376.py tests/unit/test_fs_path_validation.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/repositories/project_repo.py app/routes/fs.py tests/unit/test_fs_home_lock_3376.py
git commit -m "fix(#3376): home subtree lock for fs browse and check-path with shared roots"
```

---

### Task 6: CHANGELOG 与全量验证

**Files:**
- Modify: `CHANGELOG.md`([Unreleased] ### Fixed 追加)
- Modify: 设计/计划文档随 PR 提交

- [ ] **Step 1: CHANGELOG 条目**

```markdown
- Hardened workspace entry-point boundaries for multi-user deployments (Issue #3376): terminal `status`/`attach`/`stop` and VSCode `status`/`stop`/`attach` now require session ownership instead of machine-level access alone — terminal endpoints allow platform admins, same-tenant tenant admins, and the session owner; VSCode endpoints follow the code-server proxy's existing role set (platform admins, same-tenant tenant admins, same-tenant machine-level admins, and the owner, who is now the requesting user rather than the machine creator). Terminal `status` no longer returns the agent-side bridge credentials to browsers; terminal `work_dir` and VSCode `project_path` are validated server-side (absolute, no traversal, outside system directories — note this rejects remote paths under `/opt`, `/var`, `/root` etc.); `/fs/browse` and `/fs/check-path` enforce the same home-subtree lock as write operations while keeping explicitly shared project paths reachable.
```

- [ ] **Step 2: 全量验证**

Run:
```bash
python -m pytest tests/unit/test_path_guard_3376.py tests/unit/test_terminal_work_dir_validation_3376.py \
  tests/unit/test_terminal_ownership_3376.py tests/unit/test_vscode_ownership_3376.py \
  tests/unit/test_fs_home_lock_3376.py -v
python -m pytest tests/unit -k "terminal or vscode or fs or remote or session" -q
python -m pytest tests/unit/test_fs_path_validation.py tests/unit/test_vscode_auth_2183.py tests/unit/test_terminal_ws_handler.py -q
python scripts/lint/check_root_docs.py
```
Expected: 全部 PASS、无回归、lint 通过。

- [ ] **Step 3: 提交(含方案文档)**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-09-11-issue-3376-entrypoint-boundary-hardening-design.md docs/superpowers/plans/2026-09-11-issue-3376-entrypoint-boundary-hardening.md
git commit -m "docs(#3376): changelog and design notes for entry-point hardening"
```

---

## Self-Review 记录(修订 2)

- 覆盖检查:设计 §3.1(终端所有权,含 stop)→ Task 3;§3.2(VSCode,含审计事件)→ Task 4;§3.3(work_dir + project_path)→ Task 2;§3.4(path_guard)→ Task 1;§3.5(fs 锁,home realpath、None→[])→ Task 5;§8 兼容清单 → CHANGELOG 措辞。设计 §4 为显式非目标。
- 评审轮次 1(4 BLOCKING + 10 NON-BLOCKING)全部采纳(明细见修订 1 记录:fs 测试重写、home realpath、ProjectRepository 更名、插入点纪律、终端门闸重设计、stop_terminal、project_path、兼容清单、审计事件、接线测试、内联 dict、矩阵随 #3375、影响面列举、None→[])。
- 评审轮次 2(1 BLOCKING + 7 NON-BLOCKING)全部采纳:R1(Task 2/3 stub 补 `get_user_permission`、`_stop` 改 `POST /api/remote/terminal/stop` + body 含 terminal_id)、R2(门闸三元签名在设计 §3.2-3 与 Interfaces 统一)、R3(终端门闸无机器 admin 分支为刻意不对称,设计 §3.1/§8 与 CHANGELOG 三处口径统一)、R4(§8 跨租户机器 admin 行改为"同租户放行、跨租户一律 403")、R5(门闸代码块直接写对三元签名与审计 details)、R6(删除 `if False` 死句,fixture 改为 `_switch_user` 单一注册点)、R7(§8 补"同租户 tenant_admin + 会话行缺失 → 404"行)、R8(Task 5 纪律补"CI 不得以 root 运行")。
- 占位符检查:Task 1 Step 3 迁移体标注"逐字迁移"且来源行号明确;各"实现时先核对"注记仅限核对性动作(读原函数、核对 body 字段),断言契约均固定;无死代码残句。
- 类型一致性:`is_valid_path(path, allowed_prefixes=None) -> bool`、`_check_terminal_session_access -> tuple | None`、`vscode_owner_store.pop -> tuple[str, int, int | None] | None`、`_resolve_vscode_reported_owner(agent_mgr, machine_id, vscode_id) -> int | None`、`_check_vscode_session_access(machine_id, vscode_id, info) -> tuple | None`、`ProjectRepository.get_shared_project_paths(tenant_id=None) -> list[str]` 各任务与设计引用一致。
