# Design: #2680 跟进项（AUTH_TOKEN 剥离 / executor 拓扑剔除 / pytest 进程守卫）

日期：2026-08-15 · 类别：class-2 后续 · 状态：批准（用户授权免问推进）

## 背景

#2680 主修（PR #2681）后遗留三个跟进项，本 spec 一并处理：

1. **`ANTHROPIC_AUTH_TOKEN` 剥离缺口**：`remote-agent/constants.py`
   `LLM_PROVIDER_ENV_KEYS` 含 `ANTHROPIC_API_KEY/BASE_URL/TOKEN` 但缺
   `ANTHROPIC_AUTH_TOKEN`（Claude Code CLI 实际使用的认证变量名）。实测
   本机 dev 场景该变量原样进入 agent env，违反 #2019"agent 只携带 proxy
   token"的意图。本地（agent_runner）与远程（executor）共用此清单。
2. **remote executor 拓扑泄漏**：`remote-agent/executor.py::_build_env`
   `dict(os.environ)` 未剔除 `SCHEDULER_MODE`——与 agent_runner 同款
   问题（#2681 只修了本地路径）。远程侧当前实际风险低（远端 daemon 非
   open-ace server 启动），按纵深防御补齐 parity。
3. **TESTING 后置绕过**：`create_app()` 之后才设 `app.config["TESTING"]`
   的用法（10+ 测试文件，如 test_remote_session_state_check.py:199）在
   工厂时刻守卫之后，若 pytest 进程 env 带 `SCHEDULER_MODE=scheduler`
   仍会起真 scheduler。逐个改测试是大范围机械清扫；改为在守卫上补
   **pytest 进程检测**：`"PYTEST_VERSION" in os.environ`（pytest 启动即
   注入，实测存在于测试进程 env；pytest 之外永不存在）。关闭整类"测试
   进程起真 scheduler"，不管 TESTING 何时设置。

## 改动

1. `remote-agent/constants.py`：`LLM_PROVIDER_ENV_KEYS` 增加
   `"ANTHROPIC_AUTH_TOKEN"`（归入 LLM provider 键 → dev 逃生门
   `OPENACE_ALLOW_RAW_KEY_FALLBACK=1` 可保留，与既有语义一致）。
2. `remote-agent/executor.py::_build_env`：`env = dict(os.environ)` 后
   `env.pop("SCHEDULER_MODE", None)`（注释同 agent_runner）。
3. `app/__init__.py` 守卫扩展：
   ```python
   if scheduler_mode == "scheduler" and (
       app.config.get("TESTING") or "PYTEST_VERSION" in os.environ
   ):
   ```
   日志文案改为 `Background services skipped (test process)`。

## 明确不做

- **不剔除 `DATABASE_URL`**：agent（开发 open-ace 仓库自身）跑的测试套件
  需要"某个"数据库；盲剔除只会让它静默回落到 config.json 指向的库（本机
  同一个 PG），既不能隔离又改变语义。测试库隔离需要独立设计（如 per-task
  scratch DB），超出本跟进范围——在 #2680 留言说明。
- 不迁移 10+ 测试文件的 TESTING 后置写法（守卫扩展已覆盖其实际风险）。
- 不动 `scheduler_worker.py`（显式 SCHEDULER_MODE=scheduler 的专用进程，
  pytest 不可能运行它）。

## 隔离约束核对

`remote-agent/executor.py` 属远程会话共享面（[[autonomous-isolation-scope-constraint]]）：
本次为 **additive**（env 剔除一个 CLI 工具不消费的 open-ace 拓扑变量），
不改变普通远程会话语义。`constants.py` 增加剥离键与 #2019 既有方向一致。

## 测试（TDD）

1. 失败测试 ①：`_build_agent_env` 在 env 带 `ANTHROPIC_AUTH_TOKEN` 时产物
   无该键（现测试文件加用例）。
2. 失败测试 ②：executor `_build_env` 产物无 `SCHEDULER_MODE`（executor
   可导入性待验证；若重依赖则以 constants 清单单测 + agent_runner 路径
   覆盖，executor 侧最小 mock 单测）。
3. 失败测试 ③：`patch.dict(os.environ, {"SCHEDULER_MODE": "scheduler",
   "PYTEST_VERSION": "x"})` + `create_app()`（不带 TESTING）→ 不启动
   background services。
4. 对照：非 pytest、非 TESTING 正路径仍启动（已有对照测试沿用）。
