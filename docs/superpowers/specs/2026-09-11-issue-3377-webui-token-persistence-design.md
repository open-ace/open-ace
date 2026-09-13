# Issue #3377 WebUI token_secret 未持久化 设计文档

日期:2026-09-11(修订 1:采纳独立评审 8 条意见)
Issue: open-ace/open-ace#3377(拆分自 #3374,缺口清单第 4 项/审计风险点 F)
状态:待独立评审(修订版)

## 1. 问题与影响(调研结论)

`WebUIManager.__init__` 在 `workspace.token_secret` 为空时现场生成
`secrets.token_hex(32)`(webui_manager.py:217-219)。该 secret 签发/校验所有
WebUI token(v2 TTL 24h,v1 无 TTL),并经 `--token-secret` 传给 qwen-code-webui
子进程。影响比 issue 描述更严重:

1. **重启静默失效**(issue 原文):config.json 缺 secret 时,服务每次重启后所有
   已发 token 验签失败,iframe 工作区会话静默失效。
2. **同进程内互不认可(调研新发现)**:`WebUIManager()` 并非只在单例
   `get_webui_manager()`(webui_manager.py:1618)构造——`session_access.py:131`
   与 `routes/workspace.py:305` 在**每次 URL-token 回退校验时新建实例**。无 secret
   场景下每个实例各生成随机 secret,签发方(单例)与校验方(按请求实例)的
   secret 不同 → URL-token 回退认证在该场景下本就基本失效。
3. **多 worker 不一致**(issue 原文):自定义多 gunicorn worker 部署互不认可。
4. **无告警**:生成行为静默;Docker entrypoint 仅在首次生成 config.json 时写入
   `token_secret`(docker-entrypoint.sh:792-797,879),config 卷丢失/非 Docker
   部署无兜底。

关键事实:

- config.json 的应用侧写入**仅限 system_settings 段**(app/utils/config.py:212-250
  `set_system_setting`、300-356 `set_branding_settings`,均为无锁读-改-写全量
  JSON,由 admin 路由运行时触发);**workspace 段无任何应用侧写入者**,唯一写入
  者是 entrypoint 首启脚本。这反向支持独立文件决策:再增加一个全量读改写者会
  与现有两个无锁写入者互相丢段。
- `workspace` 段无 `get_config_value` 消费者(全仓 grep 零命中),写入该段不会
  造成 utils 缓存一致性问题。
- `WebUIManager(config)` 被大量单测以注入 config 方式构造
  (test_webui_manager_42 等);另有一批单测以 `@patch("WebUIManager._load_config")`
  后无参 `WebUIManager()` 构造(test_webui_manager.py 五处、test_auth_decorators.py
  三处)——若无条件持久化,这些测试会读写宿主 `~/.open-ace`,必须一并处理(§4)。
- `CONFIG_DIR` 定义于 `app/repositories/database.py:17`
  (`os.path.expanduser("~/.open-ace")`),webui_manager 在 `_load_config` 内
  **调用时导入**,是现成的测试 patch 缝。

## 2. 方案

### 2.1 secret 解析顺序(每次 `__init__`)

```
1. config.json workspace.token_secret(既有,entrypoint 首启写入)→ 直接使用
2. <CONFIG_DIR>/webui_token_secret 文件存在 → 读取使用(0600)
3. 生成 secrets.token_hex(32):
   a. **非空但非法**的既有文件(损坏)先 `os.unlink` 再以 O_CREAT|O_EXCL|O_WRONLY
      (mode 0o600) 原子重建——否则损坏文件会把每个实例推回"各自随机内存态",
      恰好复现本 issue;
   b. **空文件**视为"胜者在途写入/写入者崩溃残留":仅告警 + 内存态,**不 unlink**
      (拆掉在途胜者的文件会造成双 secret,比留下空文件更糟);运维按告警清理;
   c. FileExistsError(并发竞争失败)→ 读取胜者内容使用;
   d. 写入失败(目录只读/权限,如只读 fs)→ 内存态继续 + logger.warning
      (行为回退为现状,但从静默变为可见)。
```

新 secret 生成时(无论是否持久化成功)记 `logger.warning`(可见性,issue 诉求)。

### 2.2 关键设计决策

1. **独立文件而非写回 config.json**:O_EXCL 原子创建天然并发安全(两 worker 同时
   首启只有一个胜者,败者读取胜者值);写回 config.json 需读-改-写全量 JSON,
   竞争窗口内互相覆盖且可能丢失其他段。config.json 既有值(顺序 1)继续优先生效,
   已有部署零变化。
2. **仅对"自加载配置"持久化**:`__init__(config=None)`(走 `_load_config`,即
   部署路径,含 session_access/workspace.py 的按请求构造)才执行解析与持久化;
   显式注入 config(测试/编程构造)不触发任何磁盘写。判据:`config is None`。
3. **不派生自 SECRET_KEY**:WebUI token 域与会话凭据域解耦,避免轮换语义耦合。
4. **不新增配置项**:无 env/config 开关;顺序 1 保证想显式管理 secret 的部署仍可
   经 config.json 控制。
5. **文件卫生**:`os.makedirs(CONFIG_DIR, exist_ok=True)`(database.py:206 同款,
   默认权限,与 entrypoint `mkdir -p`/`ensure_db_dir` 现状一致,不单独收紧目录
   权限);内容单行 hex,读取 strip;文件 0600(os.open 的 mode 只被 umask 收窄,
   不会变宽)。

### 2.3 修复后的行为对照

| 场景 | 现状 | 修复后 |
|---|---|---|
| config.json 有 secret(entrypoint 首启) | 正常 | 不变(顺序 1) |
| config.json 无 secret,目录可写 | 每实例随机 → 重启失效/同进程互不认可 | 首个构造者生成并持久化,后续实例(含按请求构造、重启后、其他 worker)读同一 secret |
| config.json 无 secret,目录不可写 | 每实例随机,静默 | 内存态随机(与现状同)+ warning 日志(可见) |
| 并发首启(多 worker) | 各自随机 | O_EXCL 单胜者,败者读胜者 |

## 3. 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/services/webui_manager.py` | 修改 | `__init__` 的 secret 块改为调用新私有助手;新增 `_resolve_token_secret(config: WorkspaceConfig) -> str` 模块级助手(实现 §2.1 顺序,含损坏重建、空文件保守处理、持久化与告警) |
| `tests/unit/test_webui_manager.py` | 修改 | 五处 `@patch("WebUIManager._load_config")` + 无参构造的用例补 `CONFIG_DIR` patch(否则新逻辑会读写宿主 `~/.open-ace`) |
| `tests/unit/test_auth_decorators.py` | 修改 | 三处无参 `WebUIManager()` 构造改为注入 `WorkspaceConfig()`(随后本就覆写 token_secret,零语义变化) |
| `tests/unit/test_webui_token_persistence_3377.py` | 新建 | 见 §4 |
| `CHANGELOG.md` | 修改 | [Unreleased] Fixed 条目 |

不触碰:decorators 的 v1/v2 校验、entrypoint、config.json 结构、子进程传参。

## 4. 测试设计(全部 unit lane,patch `app.repositories.database.CONFIG_DIR` 到 tmp_path)

1. config.json 有 secret → 使用之,不生成文件(**注入 config 与自加载两路径各断言**:
   注入 `WorkspaceConfig(token_secret=...)` 同样直接使用、不落盘);
2. 无 secret、目录可写 → 生成 + 文件存在 + 权限 0600 + 内容与 `config.token_secret` 一致;
3. 二次构造(自加载)→ 读到同一 secret(重启/按请求实例一致性,回归核心);
4. 预置合法 secret 文件 + config.json 无 secret → 读文件值,不覆盖;
4b. 预置**非空损坏**文件 → 告警 + unlink 重建为合法值,且第二个实例收敛到同一
    secret(不能停留在内存态);
4c. 预置**空文件** → 告警 + 内存态,**不删除文件**(保守:不拆在途胜者);
5. 注入 config(非 None)且无 secret → 仅内存生成,不写任何文件(测试无宿主副作用);
6. 写入失败(monkeypatch open/os.open 抛 OSError)→ 内存态 + 不抛异常;
7. 生成时输出 warning 日志(caplog);
8. O_EXCL 竞争败者 → 读到胜者 secret(**拦截器内动态制造胜者**,不预置文件,
   确保 FileExistsError→重读分支被真实执行);
9. 既有回归:`test_webui_manager_42.py`、`test_webui_token_instance_check.py`、
   `test_webui_env_isolation.py`、**以及 §3 中补丁过的 `test_webui_manager.py`、
   `test_auth_decorators.py`** 零改动(指除 §3 所列 patch/注入调整外)通过。

标记:`pytest.mark.issue(3377)`;1/3/5 及 9 中原三个哨兵文件(42/instance_check/
env_isolation)的既有标记保持,`regression` 加于 1/3/5;9 号新纳入的
test_webui_manager.py、test_auth_decorators.py 仅做 §3 所列隔离调整,不强求
补标记(注脚)。

## 5. 非目标

- secret 轮换/管理生命周期(新增轮换机制、过期、re-kick);
- 把 v1 legacy token 迁移或废止;
- config.json 的应用侧通用写入框架;
- 多 Pod/多机共享 secret(单机 CONFIG_DIR 语义不变;k8s 多副本本就依赖共享卷)。

## 6. 风险

| 风险 | 缓解 |
|---|---|
| 持久化文件被误提交/泄露 | 文件在运行时 CONFIG_DIR(非仓库);0600;设计文档注明不入备份策略由部署方决定 |
| 损坏/空文件驻留 | 非空非法 → unlink 重建(§2.1-3a);空文件 → 告警 + 内存态,运维清理(§2.1-3b,刻意不自动删除) |
| O_EXCL 败者读到空/损坏内容 | 读取后校验非空且为合法 hex(≥64 字符);空/损坏按 §2.1-3 处理,不会把非法值当 secret 用 |
| 与 entrypoint 首启写入并存 | 顺序 1 优先,两机制不冲突;entrypoint 路径完全不变 |
| 既有单测无参构造污染宿主 | §3 所列 test_webui_manager.py(补 CONFIG_DIR patch)与 test_auth_decorators.py(改注入)一并调整,并纳入 §4-9 回归验证 |
