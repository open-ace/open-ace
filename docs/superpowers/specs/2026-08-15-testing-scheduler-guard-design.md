# Design: TESTING 模式禁启后台服务 + agent 子进程剔除 SCHEDULER_MODE

日期：2026-08-15 · 类别：class-2（自主系统自身 bug）· 状态：已批准

## 事故背景

本地 dev server 以 `SCHEDULER_MODE=scheduler` 启动后，自主工作流的 agent
在 worktree 内运行 pytest。agent 子进程继承了该环境变量，测试 fixture 调
`create_app({"TESTING": True})` 时 `app/__init__.py` 的 scheduler 启动逻辑
只看 `SCHEDULER_MODE`、不看 `TESTING`，导致测试进程内启动了真实的
AutonomousScheduler，直接操作真实 dev DB：

- `_cleanup_orphan_processes()` 把 server 进程正在使用的 agent_pid 视为
  孤儿（本进程不认识），`killpg` 杀掉真实 agent，并将工作流重置为
  `paused`（写入签名：`status=paused` + `paused_at` + `agent_pid=None` +
  空 `error_message`，无任何 workflow 事件）；
- 测试进程的 scheduler 还会推进真实工作流、自行 spawn agent。

两次实锤（均 +1 秒时间耦合于 agent 的 pytest 启动）：

1. #2491（05ad9f56）：16:44:06Z agent 运行
   `pytest tests/unit/test_acceptance_override_2658.py` → 16:44:07 工作流被暂停；
2. #2667（29f6df76）：01:49:51Z agent 运行
   `pytest tests/unit/test_autonomous_models_route_2667.py` → 01:49:52 被暂停。

完整调用栈（scratch 库复现 + repo 层仪表捕获）：

```
create_app({'TESTING': True})
 └→ start_background_services()            # app/__init__.py:997
     └→ init_autonomous_scheduler()        # autonomous_scheduler.py:1432
         └→ _cleanup_orphan_processes()    # autonomous_scheduler.py:1142
             └→ repo.update_workflow({'agent_pid': None, 'agent_session_id': '',
                                      'status': 'paused', 'paused_at': now})
```

既有旁证：三个 e2e 文件顶部各自 `os.environ.setdefault("SCHEDULER_MODE",
"web")` 打过同款补丁——坑已知但从未在源头堵住。

## 修复设计（双层防御）

### 改动 1（主修）：`app/__init__.py` TESTING 硬禁

`create_app` 的 background-services 启动分支增加 `TESTING` 守卫：

- `SCHEDULER_MODE=scheduler` 且 `app.config["TESTING"]` 为真 → **跳过**
  `start_background_services()`，打 INFO 日志
  `Background services skipped (TESTING mode)`；
- 非 TESTING 行为完全不变。

**硬禁无例外**（用户拍板）：需要测 scheduler 的单测直接调
`init_autonomous_scheduler()`（现有 test_scheduler_guard 等即如此），
不提供逃生门环境变量。

### 改动 2（纵深）：`agent_runner.py::_build_agent_env` 剔除 SCHEDULER_MODE

在现有 `env.pop("SKIP", None)`（同样是"服务级 env 不进 agent"的先例）
旁增加 `env.pop("SCHEDULER_MODE", None)`，注释说明：进程拓扑不得泄漏进
agent 子进程——agent 运行 pytest 时会在测试进程内 create_app；层①只保护
TESTING 路径，本层保证非 TESTING 的 create_app 调用（脚本/CLI）同样安全。

只剔 `SCHEDULER_MODE` 一个变量。`OPENACE_SECURITY_MODE` 等 agent 运行时
需要的语义不扩大剔除范围（YAGNI）。

## 测试计划（TDD）

1. **失败测试 ①**：`patch.dict(os.environ, {"SCHEDULER_MODE": "scheduler"})`
   下 `create_app({"TESTING": True})` → 断言 `start_background_services`
   未被调用（mock 断言）；
2. **失败测试 ②**：agent env 构建（`_build_env` 等价路径）→ 断言产物中
   无 `SCHEDULER_MODE` 键；
3. **对照断言**：非 TESTING + `SCHEDULER_MODE=scheduler` 仍正常启动
   （守卫不误伤正路径）；
4. 回归：`tests/unit/test_scheduler_guard.py` 等现有单测全绿。

## 验收

- 上述测试转绿；
- scratch-DB 复现脚本（双 create_app 场景）在修复后**不再**暂停假工作流；
- 本地 server 重启部署后，恢复/重置 #2491、#2667，agent 再跑 pytest 不再
  出现幽灵暂停。

## 明确不做

- 不删除 e2e 文件顶部的 `SCHEDULER_MODE=web` setdefault（它们管的是
  子进程 server，与本守卫职责不同）；
- 不引入逃生门 env；
- 不重构 env 构建结构。
