# Issue #3377 WebUI token_secret 持久化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WebUI token secret 在缺失时生成一次并持久化到 `<CONFIG_DIR>/webui_token_secret`(O_EXCL 原子、0600),消除重启失效、同进程按请求实例互不认可与多 worker 不一致;仅对自加载配置路径持久化,注入路径(测试)零磁盘副作用。

**Architecture:** `WebUIManager.__init__` 的 secret 块替换为模块级助手 `_resolve_token_secret(config_source)` 的调用;解析顺序 config.json → secret 文件 → 生成并持久化(败者读胜者,失败降级内存 + warning)。

**Tech Stack:** 纯标准库(os/secrets/logging),无新依赖;pytest unit lane。

**Spec:** `docs/superpowers/specs/2026-09-11-issue-3377-webui-token-persistence-design.md`

## Global Constraints

- config.json 既有 `workspace.token_secret` 永远优先(entrypoint 路径零变化)。
- 注入 config(`WebUIManager(config)` 且 config 非 None)不触发任何磁盘读写副作用。
- secret 文件权限 0o600,原子创建(O_CREAT|O_EXCL),内容单行 hex;读取三态语义(设计 §2.1-3a/3b):合法(≥64 hex)→ 使用;非空损坏 → unlink 重建;空 → 保留 + 内存态。
- 生成路径必记 `logger.warning`;持久化失败不抛异常(降级内存态)。
- 测试全部 patch `app.repositories.database.CONFIG_DIR` 至 tmp_path,不触碰宿主 `~/.open-ace`;不依赖宿主 config.json 内容。
- 既有 `tests/unit/test_webui_manager_42.py`、`test_webui_token_instance_check.py`、`test_webui_env_isolation.py` 零改动通过(回归哨兵)。
- 标记:统一 `pytest.mark.issue(3377)`,兼容用例(注入路径、既有回归)加 `pytest.mark.regression`。
- 分支 `fix/3377-webui-token-secret-persistence`(自 main);commit 前缀 `fix(#3377):`。
- 无 DB schema 变更。

---

### Task 1: secret 解析与持久化助手

**Files:**
- Modify: `app/services/webui_manager.py`
- Test: `tests/unit/test_webui_token_persistence_3377.py`

**Interfaces:**
- Produces: 模块级 `_resolve_token_secret(config: WorkspaceConfig) -> str`(webui_manager.py 内,供 `__init__` 调用;config 为已加载的 WorkspaceConfig,函数按 §2.1 顺序返回最终 secret;`_TOKEN_SECRET_FILENAME = "webui_token_secret"` 模块常量)。
- Consumes: `WorkspaceConfig.token_secret`;`CONFIG_DIR`(调用时导入,patch 缝);`secrets`/`os`(已有导入)。

- [ ] **Step 1: 写失败测试**

```python
"""Unit tests for WebUI token secret persistence (Issue #3377)."""

import logging
import os
import stat

import pytest

from app.services.webui_manager import WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.issue(3377)]

SECRET_FILENAME = "webui_token_secret"


def _config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.repositories.database.CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_config_json(config_dir, token_secret=None, workspace=None):
    import json

    ws = workspace if workspace is not None else {"enabled": True}
    if token_secret is not None:
        ws["token_secret"] = token_secret
    (config_dir / "config.json").write_text(json.dumps({"workspace": ws}))


@pytest.mark.regression
def test_config_json_secret_wins_and_no_file_written(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd, token_secret="a" * 64)
    mgr = WebUIManager()
    assert mgr.config.token_secret == "a" * 64
    assert not (cd / SECRET_FILENAME).exists()
    # 注入路径同口径:显式 secret 直接使用,不落盘
    injected = WebUIManager(WorkspaceConfig(enabled=True, token_secret="d" * 64))
    assert injected.config.token_secret == "d" * 64
    assert not (cd / SECRET_FILENAME).exists()


def test_generated_secret_is_persisted_with_0600(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)  # enabled but no token_secret
    mgr = WebUIManager()
    secret = mgr.config.token_secret
    assert len(secret) >= 64
    secret_file = cd / SECRET_FILENAME
    assert secret_file.exists()
    assert secret_file.read_text().strip() == secret
    assert stat.S_IMODE(secret_file.stat().st_mode) & 0o777 == 0o600


@pytest.mark.regression
def test_second_manager_reads_same_secret(tmp_path, monkeypatch):
    # 重启/按请求构造实例一致性(session_access.py:131 每次新建)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    first = WebUIManager().config.token_secret
    second = WebUIManager().config.token_secret
    assert first == second


def test_existing_secret_file_is_used_not_overwritten(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_text("b" * 64)
    mgr = WebUIManager()
    assert mgr.config.token_secret == "b" * 64


def test_corrupt_secret_file_is_regenerated(tmp_path, monkeypatch):
    # 非空损坏文件必须被重建(而非停留在内存态,否则每个实例各自随机,
    # 复现 #3377 原始 bug)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_text("not-hex!!")
    mgr = WebUIManager()
    assert mgr.config.token_secret != "not-hex!!"
    assert len(mgr.config.token_secret) >= 64
    # 文件被重写为合法值,后续实例收敛到同一 secret
    assert (cd / SECRET_FILENAME).read_text().strip() == mgr.config.token_secret
    assert WebUIManager().config.token_secret == mgr.config.token_secret


def test_empty_secret_file_degrades_to_memory_without_deleting(tmp_path, monkeypatch, caplog):
    # 空文件 = 胜者在途写入/崩溃残留:保守处理,不 unlink(拆在途胜者会造成双
    # secret),内存态 + 告警,文件保留待运维清理
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_text("")
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    assert (cd / SECRET_FILENAME).read_text() == ""
    assert any("token secret" in r.message.lower() for r in caplog.records)


@pytest.mark.regression
def test_injected_config_never_touches_disk(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    mgr = WebUIManager(WorkspaceConfig(enabled=True))
    assert len(mgr.config.token_secret) >= 64
    assert not (cd / SECRET_FILENAME).exists()
    assert not (cd / "config.json").exists()


def test_persist_failure_degrades_to_memory_with_warning(tmp_path, monkeypatch, caplog):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)

    real_open = open

    def _failing_open(path, *a, **kw):
        if str(path).endswith(SECRET_FILENAME):
            raise OSError("read-only filesystem")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", _failing_open)
    # os.open(O_EXCL) 路径也要挡住
    real_os_open = os.open

    def _failing_os_open(path, flags, *a, **kw):
        if str(path).endswith(SECRET_FILENAME):
            raise OSError("read-only filesystem")
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr(os, "open", _failing_os_open)
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    assert any("token secret" in r.message.lower() for r in caplog.records)


def test_generation_logs_warning(tmp_path, monkeypatch, caplog):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        WebUIManager()
    assert any("token secret" in r.message.lower() for r in caplog.records)


def test_race_loser_reads_winner_secret(tmp_path, monkeypatch):
    # 真实执行 FileExistsError → 重读胜者分支:不预置文件,在拦截器内动态制造
    # 胜者(先写入胜者内容再抛 FileExistsError)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    winner = "c" * 64
    real_os_open = os.open

    def _race_open(path, flags, *a, **kw):
        if str(path).endswith(SECRET_FILENAME) and flags & os.O_EXCL:
            fd = real_os_open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, winner.encode())
            os.close(fd)
            raise FileExistsError(str(path))
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr(os, "open", _race_open)
    mgr = WebUIManager()
    assert mgr.config.token_secret == winner
```

(注:`test_race_loser_reads_winner_secret` 在拦截器内动态写入胜者后抛
FileExistsError,保证 O_EXCL 失败→重读分支被真实执行(评审 B2);若实现读取
路径不经 `os.open` 而用 `open()`,monkeypatch 目标以实际实现为准调整为对应
函数,断言契约不变。)

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_webui_token_persistence_3377.py -v`
Expected: FAIL(当前无持久化:文件不存在断言失败)

- [ ] **Step 2b: 隔离既有单测的无参构造(评审 B3,必须先于实现合入)**

既有 8 处 `WebUIManager()` 无参构造(自加载路径)在新逻辑下会读写宿主
`~/.open-ace`,违反本计划 Global Constraint,先改为隔离形态(零断言变化):

- `tests/unit/test_webui_manager.py`:五处 `@patch("WebUIManager._load_config")`
  用例(约 :52、:100、:140、:169、:193),在各用例(或其共用 fixture)补
  `patch("app.repositories.database.CONFIG_DIR", str(tmp_path))` ——实现时读该
  文件确认五处的具体形态后逐处加 patch;
- `tests/unit/test_auth_decorators.py`:三处无参构造(约 :604、:618、:636)改为
  `WebUIManager(WorkspaceConfig())`(随后本就覆写 `manager.config.token_secret`,
  零语义变化;按需补 WorkspaceConfig 导入)。

Run: `python -m pytest tests/unit/test_webui_manager.py tests/unit/test_auth_decorators.py -q`
Expected: 全部 PASS(调整前后行为不变)

- [ ] **Step 3: 实现**

webui_manager.py 模块级(WorkspaceConfig 之后):

```python
_TOKEN_SECRET_FILENAME = "webui_token_secret"


def _read_secret_file(secret_path: str) -> tuple[str | None, bool]:
    """Read the persisted token secret.

    Returns (secret, was_nonempty_invalid): secret is None when absent,
    empty, or malformed. was_nonempty_invalid is True only for non-empty
    content that failed validation (caller may unlink + regenerate);
    empty content is treated as an in-flight/crashed writer and is left
    alone (unstaking a live winner would cause diverging secrets).
    """
    try:
        with open(secret_path) as f:
            value = f.read().strip()
    except OSError:
        return None, False
    if len(value) >= 64 and all(c in "0123456789abcdef" for c in value):
        return value, False
    if value:
        logger.warning(
            "Malformed WebUI token secret file %s; regenerating", secret_path
        )
        return None, True
    logger.warning(
        "Empty WebUI token secret file %s (interrupted writer?); using an "
        "in-memory secret — delete the file to restore persistence",
        secret_path,
    )
    return None, False


def _persist_secret_file(secret_path: str, value: str) -> bool:
    """Atomically create the secret file (0600). False when unavailable.

    O_CREAT|O_EXCL makes concurrent first-boot races safe: exactly one
    writer wins; losers re-read the winner's value.
    """
    import os as _os

    try:
        _os.makedirs(_os.path.dirname(secret_path), exist_ok=True)
        fd = _os.open(secret_path, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o600)
        try:
            _os.write(fd, value.encode())
        finally:
            _os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError as e:
        logger.warning(
            "Cannot persist WebUI token secret to %s (%s); tokens will "
            "not survive restarts",
            secret_path,
            e,
        )
        return False


def _resolve_token_secret(config: WorkspaceConfig) -> str:
    """Issue #3377: resolve the WebUI token secret.

    Order: config.json value (entrypoint-managed) → persisted secret file →
    generate once and persist (0600, O_EXCL race-safe; non-empty malformed
    files are unlinked and rebuilt, empty ones are conservatively kept).
    Only called on the self-loaded-config path; injected configs stay
    in-memory (tests must not touch the host config dir).
    """
    import os as _os

    if config.token_secret:
        return config.token_secret
    from app.repositories.database import CONFIG_DIR

    secret_path = _os.path.join(CONFIG_DIR, _TOKEN_SECRET_FILENAME)
    existing, nonempty_invalid = _read_secret_file(secret_path)
    if existing:
        return existing
    if nonempty_invalid:
        try:
            _os.unlink(secret_path)
        except OSError as e:
            logger.warning(
                "Cannot remove malformed WebUI token secret file %s (%s)",
                secret_path,
                e,
            )
    generated = secrets.token_hex(32)
    persisted_ok = _persist_secret_file(secret_path, generated)
    persisted = _read_secret_file(secret_path)[0]
    if persisted:
        if persisted_ok:
            logger.warning(
                "WebUI token secret not configured; generated and persisted to %s",
                secret_path,
            )
        else:
            # O_EXCL lost a concurrent first-boot race: adopt the winner.
            logger.warning(
                "WebUI token secret not configured; adopted the secret "
                "persisted by a concurrent worker at %s",
                secret_path,
            )
        return persisted
    logger.warning(
        "WebUI token secret not configured; generated an in-memory secret "
        "(tokens will not survive restarts)"
    )
    return generated
```

`WebUIManager.__init__` 的 secret 块(217-219)替换为:

```python
        # Issue #3377: resolve the token secret (config.json → persisted
        # file → generate once). Only the self-loaded-config path persists;
        # injected configs stay in-memory so tests never touch the host.
        if config is None:
            self.config.token_secret = _resolve_token_secret(self.config)
        elif not self.config.token_secret:
            self.config.token_secret = secrets.token_hex(32)
```

- [ ] **Step 4: 运行确认通过 + 哨兵**

Run: `python -m pytest tests/unit/test_webui_token_persistence_3377.py tests/unit/test_webui_manager_42.py tests/unit/test_webui_token_instance_check.py tests/unit/test_webui_env_isolation.py tests/unit/test_webui_manager.py tests/unit/test_auth_decorators.py -v`
Expected: 全部 PASS(后两个文件除 Step 2b 所列 patch/注入调整外零改动)

- [ ] **Step 5: 提交**

```bash
git add app/services/webui_manager.py tests/unit/test_webui_token_persistence_3377.py tests/unit/test_webui_manager.py tests/unit/test_auth_decorators.py
git commit -m "fix(#3377): persist generated WebUI token secret across restarts and instances"
```

---

### Task 2: CHANGELOG 与全量验证

**Files:**
- Modify: `CHANGELOG.md`([Unreleased] ### Fixed 追加)
- Modify: 设计/计划文档随 PR 提交

- [ ] **Step 1: CHANGELOG 条目**

```markdown
- Fixed WebUI workspace tokens silently invalidating when `workspace.token_secret` was absent from config.json (Issue #3377): `WebUIManager` used to generate an ephemeral random secret per process, so tokens broke on every restart — and per-request token validation (which constructs a fresh manager) could not validate tokens at all in that state. The secret is now resolved as config.json value → `<config dir>/webui_token_secret` (0600, atomically created on first use) → generated once and persisted; concurrent first-boot races converge on one winner, unwritable config dirs degrade to the old in-memory behavior with a warning instead of silent breakage. Deployments that set the secret via the Docker entrypoint are unaffected.
```

- [ ] **Step 2: 全量验证**

Run:
```bash
python -m pytest tests/unit/test_webui_token_persistence_3377.py tests/unit/test_webui_manager_42.py \
  tests/unit/test_webui_token_instance_check.py tests/unit/test_webui_env_isolation.py \
  tests/unit/test_webui_manager.py tests/unit/test_auth_decorators.py -v
python -m pytest tests/unit -k "webui or token or workspace" -q
python scripts/lint/check_root_docs.py
```
Expected: 全部 PASS、无回归、lint 通过。

**宿主污染排查口径(评审 B3)**:实现合入前,确认运行过上述测试后宿主
`~/.open-ace/webui_token_secret` **不存在**(`ls ~/.open-ace/webui_token_secret`);
若存在,说明仍有未隔离的自加载构造点,必须定位补 patch 后重跑。路由级测试触达
`get_webui_manager()` 的(app/routes/*.py 12 处调用点均为惰性请求路径)同属此
排查口径,以文件存在性为最终判据。

- [ ] **Step 3: 提交(含方案文档)**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-09-11-issue-3377-webui-token-persistence-design.md docs/superpowers/plans/2026-09-11-issue-3377-webui-token-persistence.md
git commit -m "docs(#3377): changelog and design notes for token secret persistence"
```

---

## Self-Review 记录(修订 1)

- 覆盖检查:设计 §2.1 顺序(1→用例1 双路径/2→0600 用例/3a 损坏重建+收敛用例/3b 空文件用例/3c 竞争动态胜者用例/3d 失败降级用例)、§2.2-2 注入无副作用、§2.2-5 文件卫生、warning 可见性、§3 既有测试隔离(test_webui_manager.py / test_auth_decorators.py)→ Task 1 用例与 Step 2b 一一对应;CHANGELOG → Task 2。
- 评审轮次 1(3 BLOCKING + 5 NON-BLOCKING)全部采纳:B1(非空损坏 unlink 重建 + 文件重写/二次收敛断言)、B2(竞争用例拦截器内动态制造胜者,真实执行 FileExistsError→重读分支)、B3(8 处既有无参构造隔离 + 纳入验证 + 宿主污染判据)、N4(设计"关键事实"更正为 system_settings 段无锁写入)、N5(设计签名去 Optional)、N6(删目录 0700 承诺)、N7(空文件保守不 unlink,与损坏文件区分)、N8(用例 1 补注入断言,marker 清单对齐 1/3/5/9)。
- 占位符检查:Step 2b 的"读文件确认五处形态后逐处加 patch"为核对性动作,断言与行为契约固定;无 TBD。
- 类型一致性:`_resolve_token_secret(config: WorkspaceConfig) -> str`、`_read_secret_file(str) -> tuple[str | None, bool]`、`_persist_secret_file(str, str) -> bool`、`_TOKEN_SECRET_FILENAME` 在任务与设计间一致。
- 风险预检:测试对 `os.open`/`open` 的 monkeypatch 仅拦截 SECRET_FILENAME 后缀路径;`_persist_secret_file` 的 makedirs 在 CONFIG_DIR 已存在时为 no-op;损坏文件 unlink 失败仅告警(不抛);空文件路径不落盘、不删除。
