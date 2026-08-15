# TESTING Scheduler Guard 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 堵死「测试进程内启动真 scheduler 幽灵操作真实 DB」这一 class-2 事故类：`create_app` 在 TESTING 下硬禁后台服务 + agent 子进程 env 剔除 `SCHEDULER_MODE`。

**Architecture:** 双层防御。层①（主修）在 `app/__init__.py` 的 background-services 分支加 `TESTING` 守卫——无论环境变量从哪来，测试进程都不再起 scheduler。层②（纵深）在 `agent_runner._build_agent_env` 剔除 `SCHEDULER_MODE`，使进程拓扑不随 agent 子进程泄漏（保护非 TESTING 的 create_app 调用）。

**Tech Stack:** Flask `create_app` 工厂、pytest（unittest.mock patch）。

**Spec:** `docs/superpowers/specs/2026-08-15-testing-scheduler-guard-design.md`

---

### Task 1: create_app TESTING 守卫（层①）

**Files:**
- Test: `tests/unit/test_app_testing_scheduler_guard.py`（新建）
- Modify: `app/__init__.py:808-813`

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_app_testing_scheduler_guard.py`：

```python
"""TESTING 模式必须禁启后台服务（class-2 幽灵暂停事故，2026-08-15）。

背景：server 以 SCHEDULER_MODE=scheduler 启动时，agent 子进程继承该变量；
agent 在 worktree 内跑 pytest 时，fixture 的 create_app({"TESTING": True})
会在测试进程内启动真实 AutonomousScheduler，对真实 DB 执行孤儿清理/推进，
把 server 正在驱动的工作流 kill+paused。守卫：TESTING 下硬禁。
"""

from unittest.mock import patch

from app import create_app


def test_testing_mode_skips_background_services():
    with (
        patch.dict("os.environ", {"SCHEDULER_MODE": "scheduler"}),
        patch("app.start_background_services") as mock_sbs,
    ):
        create_app({"TESTING": True})

    mock_sbs.assert_not_called()


def test_scheduler_mode_still_starts_services_when_not_testing():
    with (
        patch.dict("os.environ", {"SCHEDULER_MODE": "scheduler"}),
        patch("app.start_background_services") as mock_sbs,
    ):
        create_app({"TESTING": False})

    mock_sbs.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败（红）**

Run: `python3 -m pytest tests/unit/test_app_testing_scheduler_guard.py -v`
Expected: `test_testing_mode_skips_background_services` FAIL
（`AssertionError: Expected 'start_background_services' to not have been called`）；
`test_scheduler_mode_still_starts_services_when_not_testing` PASS（对照，守卫不误伤正路径）。

- [ ] **Step 3: 最小实现**

`app/__init__.py:806-813` 当前：

```python
    scheduler_mode = os.environ.get("SCHEDULER_MODE", "web")
    if scheduler_mode == "scheduler":
        start_background_services()
        logger.info("Background services started (SCHEDULER_MODE=scheduler)")
    else:
        logger.info("Background services NOT started (SCHEDULER_MODE=%s)", scheduler_mode)
```

改为：

```python
    scheduler_mode = os.environ.get("SCHEDULER_MODE", "web")
    # TESTING guard (class-2, 2026-08-15): a test process must never start
    # real schedulers — they operate on the real DB (orphan-kill advancing
    # workflows, ghost-pausing rows). Tests that need a scheduler call
    # init_autonomous_scheduler() directly.
    if scheduler_mode == "scheduler" and app.config.get("TESTING"):
        logger.info("Background services skipped (TESTING mode)")
    elif scheduler_mode == "scheduler":
        start_background_services()
        logger.info("Background services started (SCHEDULER_MODE=scheduler)")
    else:
        logger.info("Background services NOT started (SCHEDULER_MODE=%s)", scheduler_mode)
```

- [ ] **Step 4: 跑测试确认通过（绿）**

Run: `python3 -m pytest tests/unit/test_app_testing_scheduler_guard.py -v`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_app_testing_scheduler_guard.py app/__init__.py
git commit -m "fix: create_app hard-skips background services under TESTING"
```

---

### Task 2: agent env 剔除 SCHEDULER_MODE（层②）

**Files:**
- Test: `tests/unit/test_autonomous_ci_guardrails.py`（扩展，模式复用同文件 `test_agent_environment_binds_python_and_git_guards`）
- Modify: `app/modules/workspace/autonomous/agent_runner.py:1448`（`env.pop("SKIP", None)` 旁）

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_autonomous_ci_guardrails.py` 的
`test_agent_environment_binds_python_and_git_guards` 之后新增：

```python
def test_agent_env_scrubs_scheduler_mode(monkeypatch, tmp_path):
    """进程拓扑不得泄漏进 agent 子进程（class-2 幽灵暂停，2026-08-15）。

    agent 继承 SCHEDULER_MODE=scheduler 后，其在 worktree 内运行的
    pytest 会在测试进程内启动真 scheduler（TESTING 守卫之外的路径）。"""
    from app.modules.workspace.autonomous import agent_runner

    guard_dir = tmp_path / "agent-bin"
    guard_dir.mkdir()
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o755)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(guard_dir))
    monkeypatch.setenv("SCHEDULER_MODE", "scheduler")

    adapter = MagicMock()
    adapter.get_env_vars.return_value = {}
    env = agent_runner.AutonomousAgentRunner._build_agent_env(
        adapter,
        "claude-code",
        None,
        "session",
        "",
        [sys.executable],
    )

    assert "SCHEDULER_MODE" not in env
```

（文件顶部已 import `MagicMock`/`sys`——若无则补。）

- [ ] **Step 2: 跑测试确认失败（红）**

Run: `python3 -m pytest tests/unit/test_autonomous_ci_guardrails.py::test_agent_env_scrubs_scheduler_mode -v`
Expected: FAIL（`AssertionError: assert 'SCHEDULER_MODE' not in {...}`）。

- [ ] **Step 3: 最小实现**

`app/modules/workspace/autonomous/agent_runner.py`，在
`env.pop("SKIP", None)`（约 :1448，注释块 "Never let a service-level
SKIP leak..." 之后）紧接着加：

```python
    env.pop("SKIP", None)
    # Process topology must not leak into agent subprocesses: the agent's
    # pytest run would start real schedulers inside the test process
    # (TESTING guard covers create_app; this keeps topology out entirely so
    # non-TESTING create_app calls are safe too). (class-2, 2026-08-15)
    env.pop("SCHEDULER_MODE", None)
```

- [ ] **Step 4: 跑测试确认通过（绿）**

Run: `python3 -m pytest tests/unit/test_autonomous_ci_guardrails.py::test_agent_env_scrubs_scheduler_mode tests/unit/test_autonomous_ci_guardrails.py::test_agent_environment_binds_python_and_git_guards -v`
Expected: 2 passed（新测试绿 + 既有相邻测试不回归）。

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_autonomous_ci_guardrails.py app/modules/workspace/autonomous/agent_runner.py
git commit -m "fix: scrub SCHEDULER_MODE from agent subprocess env"
```

---

### Task 3: 端到端复验（scratch 复现脚本转阴性）

**Files:** 无代码改动；复用 `ace_repro` scratch 库与双 create_app 场景。

- [ ] **Step 1: 重置假工作流**

```bash
psql -d ace_repro -qc "update autonomous_workflows set status='developing', paused_at=NULL, sandbox_state=NULL, agent_pid=NULL where workflow_id like '11111111%'"
```

- [ ] **Step 2: 跑修复后的双 create_app 场景**

```bash
DATABASE_URL=postgresql://rhuang@localhost/ace_repro SCHEDULER_MODE=scheduler \
OPENACE_SECURITY_MODE=development SECRET_KEY=$(python3 -c "print('a'*48)") \
OPENACE_ENCRYPTION_KEY=$(python3 -c "print('b'*64)") python3 - <<'EOF'
import time
from app import create_app
app1 = create_app({'TESTING': True})
time.sleep(15)
app2 = create_app({'TESTING': True})
time.sleep(10)
print('DONE')
EOF
psql -d ace_repro -tc "select status, paused_at from autonomous_workflows"
```

Expected: `developing | `（未被暂停）——修复前同场景必 paused。

- [ ] **Step 3: 本地相关单测回归**

```bash
python3 -m pytest tests/unit/test_app_testing_scheduler_guard.py tests/unit/test_autonomous_ci_guardrails.py tests/unit/test_scheduler_guard.py -q
```

Expected: 全部 passed（含 scheduler_guard 既有 20+ 用例）。

- [ ] **Step 4: 清理 scratch 库**

```bash
dropdb ace_repro
```

---

### Task 4: PR 与合并

- [ ] 建 GitHub issue（ghost-pause 事故记录，含调用栈证据）
- [ ] push 分支、开 PR（正文含事故摘要；**不用** `Closes #N` 关键词以外的自动关闭陷阱措辞——如需关 issue 用显式 `Fixes #N`）
- [ ] 独立 agent 审查 `gh pr diff`（requesting-code-review 流程）至 CLEAN
- [ ] 等 5 个 required checks 全绿（lint / test(3.10/3.11/3.12) / build）
- [ ] `gh pr merge <N> --merge --delete-branch`

### Task 5: 部署与重置（本机）

- [ ] 合并后重启本机 server（`SCHEDULER_MODE=scheduler` + `generated-secrets.env`，从 main 最新代码）
- [ ] 恢复 #2667（paused）与 reset #2491（failed，第一类测试失败 → pickup 状态 + 清 retry 计数 + 恢复 worktree_path）
- [ ] 观察 agent 跑 pytest 后工作流不再被幽灵暂停（监控 cron 持续跟踪）
