# Issue #3378: 交互工作区 `sandboxed` 隔离等级（基于 OpenSandbox）— 方案 v5

状态: 设计中（v5。审查轨迹: R1 安全 B×1+M×6、R1 可行性 B×1+M×6、R2 安全 M×2+m×5、R2 可行性 B×1+M×4+m×2、R3 安全 M×1+m×2+q×1、R3 可行性 M×2+m×4+q×3、R4 安全 **APPROVE**（残留 2 实现期 MINOR）、R4 可行性 M×1+m×2（修法已给出，无架构返工））
依赖: PR #3375 已合入（能力契约基础设施）
Worktree: `.worktrees/3378-sandboxed`（branch `feat/3378-sandboxed-isolation-level`，基于 2d3618ce）

v5 变更摘要（相对 v4）:
- **[FEAS-R4-1/M] 孤儿导出以 pod 内标记文件为地面真值门控**: reconcile 对每个孤儿先经 execd `/files/download` 探测 `/workspace/.openace-restore-done`——404 = 降级启动（CP touch 从未成功）= 跳过导出;消除 D5"先导出后销毁"与 D6 导出守卫的字面矛盾（守卫对本进程实例按启动器状态判定，对孤儿按 pod 内标记判定，两者地面真值一致）;
- **[FEAS-R4-2/m] §7.1 计数口径修正**: 翻转 4 items/33（single_user 1 + non_linux 2（parametrize）+ docker_multi_user 1），其余 29 items 不动;验收以具名清单为准;
- **[FEAS-R4-3/m] reconcile 宿主唯一化**: gunicorn worker 启动钩子（env 门控）为唯一生产宿主;server.py dev 路径以独立 greenlet spawn——懒构造选项删除，reconcile 工作绝不内联进用户请求 greenlet。

v4 变更摘要（相对 v3）:
- **[SEC-M1/FEAS-M3] reconcile 改正向触发**: 显式 env（仅 web 服务入口设置），**不放在 create_app**（管理脚本 import create_app 不得触发）;PYTEST/TESTING guard + fail-soft（异常吞没记日志，sandbox-backends.json 损坏不阻塞 web boot）;
- **[FEAS-M1/SEC-D3] kernel 维度翻转清单补第三条**: `test_docker_multi_user_reports_os_user`（os_user 的 unsupported 元组增 kernel——共享宿主内核，诚实申报）;翻转 3 条/33，其余 30 条不动;
- **[FEAS-M2] 导出守卫**: 未确认 restore-done 的实例（本进程从未成功完成恢复）不参与导出——消灭"降级空启动 → 空树 tar 覆盖完好快照"的自毁放大器;
- **[SEC-m2/FEAS-Q3] resources 恒 enforced**: create body 恒携带 resourceLimits（默认 4Gi/2CPU 亦为边界）——删除 `sandbox_resources_unbounded` 死码;
- **[FEAS-M4] entrypoint 草图修正**: 显式目录列表（无 brace expansion，dash 兼容）、计数器超时（300×0.2s=60s）、tar 解包前 mkdir -p;草图标注"示意，规范以文字为准";
- **[FEAS-M5] 放弃"逐块落盘"**: 接受有界 bytes 返回（16MiB 内存上界可接受），client.py 无需改动;
- **[FEAS-Q2] 快照上界可配置**: `OPENACE_WEBUI_STATE_MAX_BYTES`（默认 16MiB）+ 用户语义声明（越限 → 历史冻结在最后一份完好快照）;
- **[FEAS-Q1] metadata 键名独立**: `openace.webui.kind/process_generation/owner`，不与既有 `openace.generation`（workflow 代数）冲突;
- **[FEAS-M6] 单 web 进程部署假设显式声明**（多副本/滚动发布重叠不支持）;
- **[SEC-Q4] 同 form 重启优先复用原端口**。

## 1. 目标与不做的事

**目标**（对齐 #3378 正文）:

1. 交互 WebUI 进程经 OpenSandbox pod 运行;
2. 连通性: LLM 代理回连、浏览器→pod 的 HTTP/SSE/WebSocket、webui 自带 in-pod 文件浏览与会话历史（跨回收周期的快照恢复）;
3. 生命周期: 空闲回收、pod TTL 与 token TTL 协调（renew 钳制）、控制面重启孤儿回收;
4. 契约端点与 `user-url` 门闸放行 `sandboxed`，`POLICY_REVISION` 递增，矩阵与部署文档更新。

**明确不做**（follow-up 记录）:

- 控制面 `/fs/*` 路由与沙箱工作区整合;多 web worker;**多 web 副本/滚动发布新旧重叠**（单 web 进程是 generation 判别的前提，§8 部署假设）;真实集群端到端验收（归 #3379，含 N6 验证点）;
- `terminal`/`vscode` 入口沙箱化（词表 `enforced|partial|separate_contract`）;warm pool / pause / resume;webui 历史 quota/GC。

## 2. 关键事实（经四轮审查核实）

| # | 事实 | 证据 |
|---|---|---|
| F1-F29 | （v1-v3 表全部核实;F27 含修正: shutdown_webui_manager 有 dev 调用方） | 见前稿 |
| F30 | `build_create_request` **恒发** `resourceLimits`，`build_resource_limits` 未配置维度填默认（memory 4Gi / cpu 2）——webui pod 恒有资源边界 | policy.py:519-529, 716 |
| F31 | `SCHEDULER_MODE` 未设默认 "web";`create_app` 消费方不止 web/scheduler——`scripts/seed_manage_data.py:21` 直接 import;同文件已有 PYTEST/TESTING guard 先例（"a test process must never start real schedulers"） | app/__init__.py:880-897, scripts/seed_manage_data.py:21 |
| F32 | `_OS_USER_UNSUPPORTED` 是显式二元组常量（resources, network_egress）;`test_docker_multi_user_reports_os_user` 精确断言该元组 | workspace_isolation_contract.py:59, test_…_3374.py:106 |
| F33 | metadata 恒写 `openace.generation`（workflow 代数语义）——webui 进程代不得复用该键 | policy.py:731 |
| F34 | entrypoint 经 `/bin/sh -c` 执行（Ubuntu dash: 无 brace expansion）;现实现用显式目录列表 | policy.py:120-123 |
| F35 | `download_file` 有界模式返回 bytes（16MiB 上界下内存可接受，无需流式 API） | client.py:416-447 |

## 3. 架构决策（v5）

### D1 浏览器通路: 控制面本地 gevent 端口代理（Connection: close + WS 升级后字节桥）

- 端口式而非路径式: 路径式依赖 webui 前端全部相对路径（外部应用行为不可验证）;端口式 origin 根路径无重写依赖。follow-up。
- HTTP 模式 = 每连接单请求（`Connection: close`，上游同样一次性）;SSE 不受影响。**N6 验证点**: 通路依赖"上游对未声明端口应答 `GET /endpoints/3100`"的外部假设——运行期失败以 `sandbox_endpoint_unresolved` 结构化浮出，真实验证归 #3379;备选 CP 侧反代/隧道记 follow-up。
- WS 模式: 客户端 `Upgrade` → 带注入头向上游升级 → 101 后双向 splice。
- 头规约: 注入头强制覆盖客户端同名头;剥离客户端一切 `OpenSandbox-*`/`X-EXECD-*`/`OPENSANDBOX-*`;剥离 hop-by-hop 与 `Expect` 头;CL+TE 共存 → 400;重复注入名头 → 400;上游响应 allowlist 四头之外同前缀头剥离。
- 鉴权: 哑管道，token 由 pod 内 webui 以 per-instance secret 端到端校验;本地端口暴露与今日本地 webui 等价。
- 活跃度回馈: 每次成功转发调 `update_activity`（补单用户无心跳）。
- HTTPS: 沿用 F18 外部反代机制。
- 清理: 停止时关 socket + kill 在飞 greenlet。
- 端口分配: 键 `(user_id, form)`;同用户跨形态 stop-and-restart;**同 form 重启优先复用原端口**（SEC-Q4）;`_wait_for_service_ready` 不用于沙箱形态。
- 单用户 + sandboxed URL: 单用户分支改走代理端口分配（§7.2 断言）。

### D2 WebUI 进程: bootstrap 前缀 entrypoint + per-instance secret + 恢复门

- **entrypoint = `_build_entrypoint` bootstrap（逐字保留，显式目录列表，F34）+ 恢复门（计数器超时）+ `exec qwen-code-webui`**。示意草图（**规范以本节文字为准，草图不可照抄**）:
  ```sh
  # bootstrap 部分 = policy.py _build_entrypoint 原文（mkdir 显式列表/chown/git safe.directory）
  i=0
  until [ -f /workspace/.openace-restore-done ] || [ "$i" -ge 300 ]; do sleep 0.2; i=$((i+1)); done
  [ -f /workspace/.openace-restore-done ] || echo "restore marker missing; degrading" >&2
  exec qwen-code-webui --host 0.0.0.0 --port 3100 <shlex.quote 后的参数>
  ```
  `build_create_request` 扩展为 entrypoint 后缀追加（不整体替换）;60s（300×0.2s）超时后降级启动 + CP 侧 WARNING。
- **恢复定序（N3）**: CP 在 create 返回后: 上传快照 tar（或空标记文件，无快照时）→ `/command background:true tar -xf`（**先 `mkdir -p` 目标目录**，FEAS-M4c）→ `touch /workspace/.openace-restore-done`。启动器记录"本实例恢复是否确认完成"（D6 导出守卫依赖此状态）。
- **env**: 全走 create body `env`: `OPENAI_BASE_URL`、`OPENAI_API_KEY`（proxy token）、`OPENACE_PROXY_URL/TOKEN`、`PATH/HOME`;遵守 `_ENV_NEVER`。禁止 export 串。
- **per-instance secret（B1）**: 每实例 `secrets.token_hex(32)` 经 `--token-secret` 注入并存 `WebUIInstance`;token 签发/校验/刷新 per-instance 化;本地实例全局 secret 行为不变。**N8 语义声明**: 沙箱实例停止后其 token 在 CP URL-token 路径 401（§8）。
- **proxy token TTL 对齐**: 生效 TTL 复用 `_get_default_proxy_token_ttl_minutes("webui")`（经 APIKeyProxyService 实例获取，实现细节）≥ pod TTL，否则探测码 `sandbox_proxy_token_ttl_too_short`。
- **备选启动路径**: `/command background:true`（fake 须先建模，§7.8）。

### D3 契约探测: 零 pod 静态 fail-closed + 六维+kernel 映射

1. `parse_backend_config` → `sandbox_backend_unconfigured`;
2. tier（`workspace.sandbox_tier` 或 `default_tier`）→ `sandbox_tier_missing`;
3. `webui_image` 配置/pinned/allowlisted → 三个码;
4. `assert_proxy_reachable` 语义 → `sandbox_proxy_unreachable`;
5. 生效 proxy token TTL ≥ pod TTL → `sandbox_proxy_token_ttl_too_short`。

**早退重构**: 沙箱探测不查平台与 `multi_user_mode`;os_user 链不变;等级取最高通过者。

**维度映射（含 kernel 新维度，SEC-m2/FEAS-Q3 修正）**:

| 维度 | sandboxed 静态探测通过后 | sandboxed 首 pod probe 后 | os_user（对照） |
|---|---|---|---|
| identity | enforced | enforced | enforced（不变） |
| filesystem | enforced | enforced | enforced（不变） |
| environment | enforced | enforced | enforced（不变） |
| process | enforced | enforced | enforced（不变） |
| resources | **enforced（恒——create body 恒携带 resourceLimits，默认 4Gi/2CPU 亦为边界，F30）** | enforced | unsupported（不变） |
| kernel | unsupported（`sandbox_runtime_unverified`） | enforced（gVisor 正向）;Kata 仅负向 → 保持 unverified（`sandbox_runtime_kata_negative_only`） | **unsupported（新增——共享宿主内核，模块 docstring 既述）** |
| network_egress | unsupported（同 kernel reason） | enforced（sidecar /policy 实读） | unsupported（不变） |

`kernel` 入词表随 `POLICY_REVISION = "2026-09-12.1"`;os_user 的 `_OS_USER_UNSUPPORTED` 扩为三元组（F32 的精确断言测试随之修订，见 §7.1）;probe 升级 in-process memoize，重启回退。

### D4 门闸、身份与 prestart

- `required == sandboxed`: 跳过 `identity_mapping_missing`/`per_user_launch_unavailable`，改查 D3 memoized 探测。floor 语义不变;`webui_image` 配置即翻转默认 floor（文档声明）。
- `prestart_user_instance_async` 早退移到门闸后/分支内。
- 沙箱形态不做: OS 账户、host 目录供给、sudo wrapper 链。

### D5 生命周期、TTL 链与孤儿回收

- create `timeout` = min(WEBUI_TOKEN_TTL 24h, 生效 proxy token TTL);
- **token 随访问重铸** + 健康检查用现行 token;
- **续期钳制**: renew 目标 = min(now+24h, proxy token 过期时刻)——pod 最多活到 token 失效，此后正常回收 → 下次 `/user-url` 重建 + 快照恢复;
- **空闲回收（30min）**: 导出快照 → `client.delete_sandbox`（幂等 404=成功）→ 撤销代理 → 注销实例;
- **孤儿回收**:
  - **正向触发（SEC-M1）**: reconcile 仅在设置了显式 env `OPENACE_WEBUI_ORPHAN_RECONCILE=1` 的进程执行——该 env **只**由 web 服务入口设置（docker-entrypoint.sh 的 web gunicorn 命令行 + server.py dev `__main__` 路径）;**不放在 create_app 内**（管理脚本 `seed_manage_data.py` 等 import create_app 不得触发，F31）;**唯一生产宿主: gunicorn worker 启动钩子（app/gunicorn_worker.py，env 门控）**;server.py dev 路径以**独立 greenlet** spawn——reconcile 工作（list + 逐孤儿导出 + destroy + 终态轮询）绝不内联进用户请求 greenlet（FEAS-R4-3）;
  - **TESTING guard（FEAS-M3）**: `PYTEST_VERSION`/`TESTING` 下绝不 reconcile（对齐 app/__init__.py:883-890 先例）;
  - **fail-soft（FEAS-M3）**: reconcile 自身异常吞没并记日志（含 sandbox-backends.json 损坏）——web boot 不得失败;
  - **generation 判别**: 每进程 `uuid4().hex` 写入 metadata **独立键** `openace.webui.process_generation`（F33，不与 `openace.generation` 冲突）;metadata 键集: `openace.webui.kind="webui"`、`openace.webui.process_generation`、`openace.webui.owner=<user_id>`;reconcile 只销毁 kind=webui 且 process_generation != 本进程的沙箱（list 等值过滤 + 客户端侧不等过滤，belt-and-braces）;
  - **worker 重生**: 代理/secret 随 worker 死亡 → pod 功能性孤儿化 → 重生进程销毁为正确清理;
  - **先导出后销毁（FEAS-R4-1 地面真值门控）**: 每个孤儿导出前先经 execd `/files/download` 探测 pod 内 `/workspace/.openace-restore-done`——**存在才导出，404（降级启动，CP touch 从未成功）跳过导出**;与本进程实例的 D6 导出守卫共享同一地面真值（pod 内标记文件），两种判定路径永不冲突;导出尽力而为，失败不阻塞销毁;
  - **单 web 进程假设（FEAS-M6）**: generation 判别前提 = 每 installation 恰一个存活 web 进程（compose 单 app 容器成立）;多副本/滚动重叠会互毁，§8 声明不支持;
  - **reconcile_orphans 排斥契约（N7）**: 休眠的 `provider.reconcile_orphans` live-set 必须排除 kind=webui——测试 + docs/sandbox-backends.md 声明;
- **周期性导出**: cleanup 循环每 5min 对存活沙箱实例导出;
- **shutdown 扩展（S7）**: 存活沙箱实例导出 + destroy + 撤销代理（server.py SIGTERM 路径）;gunicorn 无 exit hook 由启动 reconcile 兜底。

### D6 会话历史: 整目录 tar 快照 + 上界 + 导出守卫 + 独立存储根

- **导出守卫（FEAS-M2）**: **未确认恢复完成的实例不参与任何导出**——判定地面真值为 pod 内 `/workspace/.openace-restore-done` 标记文件: 本进程实例按启动器记录的恢复状态（该状态仅在 CP 成功 touch 后置位），孤儿（reconcile 路径）按 execd 对该文件的探测结果——降级空启动（touch 从未成功 → pod 内无标记）**永远不会**覆盖完好快照;
- **导出**: `/command background:true tar -cf`（status 轮询）→ `files/download`（有界 bytes 返回，F35——**不做流式扩展，client.py 无改动**）→ 大小 ≤ 上界 → 写 `webui-agent-state/webui-<user_id>.tar`（独立根，reaper 不扫）;
- **上界可配置（FEAS-Q2）**: `OPENACE_WEBUI_STATE_MAX_BYTES` 默认 16MiB;超限 WARNING + 跳过（保留最后一份完好快照）——**用户语义声明**: 越限用户的历史冻结在旧快照，直到其手工清理（§8）;
- **导入**: upload tar → `mkdir -p` + `tar -xf` → touch restore-done（无快照上传空标记）;
- **承诺范围**: 整目录覆盖快照;crash 丢 ≤5min 增量;**降级启动实例不导出**;无自动 GC。

### D7 镜像与部署

- `endpoints.<tier>.webui_image`（digest-pinned、allowlist）;`config.py` fail-closed 解析;
- `scripts/docker/webui-sandbox.Dockerfile` 参考构建;
- 文档: 镜像/allowlist、`OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES ≥ 1440`（生效值语义）、egress 前提、HTTPS 外部反代端口段、`webui_image` 即翻转 floor、webui 入口与 3100 端点"未经集群端到端验证"（#3379）、**单 web 进程部署假设**。

## 4. 代码落点（v5）

| 文件 | 动作 |
|---|---|
| `app/services/workspace_isolation_contract.py` | D3 全部（探测/早退重构/等级/维度映射含 os_user kernel 申报/kernel 词表/POLICY_REVISION/矩阵） |
| `app/services/webui_sandbox.py`（新，~900-1000 行） | `SandboxedWebuiLauncher`（create/entrypoint 构造/恢复门与状态/健康/renew 钳制/快照导出导入含守卫/generation reconcile 正向触发/env 门 + TESTING guard + fail-soft）+ `SandboxWebuiProxy`（D1） |
| `app/services/webui_manager.py` | 实例模型（form/per-instance secret/is_alive 分叉/token 重铸/端口复用）、启动/停止/空闲回收/prestart 重排/allocator 形态感知/shutdown 扩展 |
| `app/modules/workspace/autonomous/sandbox/opensandbox/policy.py` | 扩展 `build_create_request`: entrypoint 后缀追加（bootstrap 保留）、timeout/metadata 覆盖参数 |
| `app/modules/workspace/autonomous/sandbox/opensandbox/config.py` | `webui_image` 解析校验 |
| `app/modules/workspace/autonomous/sandbox/opensandbox/fake_server.py` | 建模 `background:true`（注明上游依据）;metadata 过滤 list 已存在勿重复 |
| `docker-entrypoint.sh` | web 服务 gunicorn 命令注入 `OPENACE_WEBUI_ORPHAN_RECONCILE=1`（scheduler 服务不注） |
| `app/gunicorn_worker.py` | worker 启动钩子: env 门控 + TESTING guard + fail-soft 地触发 reconcile（唯一生产宿主，FEAS-R4-3） |
| `server.py` | dev `__main__` SIGTERM 路径已有 shutdown;补 env 设置（与 entrypoint 对齐） |
| `app/auth/decorators.py` + webui_manager token 路径 | per-instance secret 校验分叉 |
| `docs/workspace-isolation-capabilities.md`、`docs/sandbox-backends.md` | §7/§8 全部声明 + 排斥契约 + 单进程假设 |
| `scripts/docker/webui-sandbox.Dockerfile`（新） | 参考镜像 |

不新增 DB 表/迁移;client.py 无改动。

## 5. 错误码（新）

`sandbox_backend_unconfigured` / `sandbox_tier_missing` / `webui_image_missing` / `webui_image_not_pinned` / `webui_image_not_allowed` / `sandbox_proxy_unreachable` / `sandbox_proxy_token_ttl_too_short` / `sandbox_runtime_unverified` / `sandbox_runtime_kata_negative_only`（探测级 reason）/ `sandbox_create_failed` / `sandbox_endpoint_unresolved`（运行期）。
（`sandbox_resources_unbounded` 已删除——resources 恒 enforced，F30。）

## 6. 入口矩阵

词表沿用 `enforced|partial|separate_contract`: `webui` → `enforced`（sandboxed 形态）;`terminal`/`vscode`/`fs` → 沿用现值，reasons 标注 `sandboxed_entry_not_wired`。

## 7. 测试矩阵（v5）

全部 `pytest.mark.issue(3378)`，回归向加 `regression`。代理真实 socket 测试放 `tests/integration/`，其余 unit。

1. **契约**: 探测各失败码 × 配置矩阵（proxy TTL 生效值）;维度映射静态/探针后两态（Kata 负向保持 unverified;os_user 的 kernel 落 unsupported）;早退重构;POLICY_REVISION;公开 dict;**#3375 翻转清单（具名 4 items/33 collected: `test_single_user_mode_reports_unsupported`、`test_non_linux_platform_reports_unsupported`（parametrize ×2 platforms）、`test_docker_multi_user_reports_os_user`（unsupported 三元组，F32）——其余 29 items 不动全绿;验收以具名清单为准）**。
2. **门闸/user-url**: sandboxed 接受/各码拒绝;不要求 system_account;os_user 不变;floor/param;命中重铸 token;HTTPS 改写;单用户沙箱 URL 走代理端口断言。
3. **启动器（fake provider）**: create body（bootstrap 前缀/恢复门/env/timeout/metadata 独立键/无 host volume）;恢复定序（标记先于 exec;空标记;超时降级 + **降级实例标记为未确认恢复**）;健康经代理;renew 钳制;停止幂等;is_alive 分叉。
4. **代理（integration）**: HTTP 透传+头注入覆盖、客户端前缀头剥离、CL/TE 400、`Expect` 剥离、Connection: close、SSE、WS splice、上游不可达、stop 终止 greenlet、update_activity。
5. **生命周期**: idle 回收导出+销毁;周期导出;**reconcile: env 正向触发（无 env 不跑——管理脚本形态）、TESTING guard、generation 判别（本进程免疫/异进程销毁）、孤儿导出按 pod 内 restore-done 标记门控（有标记导出/404 跳过，FEAS-R4-1）、先导出后销毁、fail-soft（损坏配置不抛）、幂等重入、宿主为 worker 启动钩子/dev 独立 greenlet（不内联用户请求 greenlet，FEAS-R4-3）**;`reconcile_orphans` 排斥契约;同 form 重启端口复用;跨形态 stop-and-restart;shutdown 导出+销毁。
6. **历史**: tar 往返、上界可配置与超限跳过、`webui-<user_id>` 键、独立根不被 reaper、**未确认恢复实例不导出（本进程状态与孤儿 pod 内标记探测两条路径都不覆盖空树，FEAS-R4-1）**、导出失败不阻塞销毁。
7. **prestart**: sandboxed 分支;os_user 不变。
8. **fake 治理**: `background:true` 建模（立即 complete、无 stdout 事件，注明上游依据）。

## 8. 文档声明要点

- enforced/等级诚实边界（静态 = 配置面就绪;kernel/egress per-pod 验证;Kata 仅负向;重启回退）;
- TTL 链数值表 + renew 钳制（pod ≤ proxy token 寿命）+ token 重铸;
- 丢失窗口: crash ≤5min;**降级启动实例不导出**（宁可保旧快照）;快照上界可配置、越限冻结语义;无自动 GC;
- 沙箱实例停止后 token 401 语义（N8）;
- `terminal/vscode/fs` 未接线;`/fs` host 树不可用;
- 网关凭据 in-pod 自捕获包含假设;3100 端点可达性为外部假设（#3379）;
- **单 web 进程部署假设**（多副本/滚动重叠不支持，会互毁）;
- **reconcile 正向触发 env 的运维语义**（仅 web 入口;管理脚本安全）;
- pause/resume/warm pool 不适用;gunicorn 形态 SIGTERM 无 exit hook（reconcile 兜底）;
- `webui_image` 即翻转默认 floor;`reconcile_orphans` 排斥契约。

## 9. 任务顺序

1. 契约（探测/早退重构/等级/维度映射/kernel 词表 + os_user kernel 申报）+ §7.1 测试 + #3375 回归基线（具名 3 测试/4 items 翻转，清单见 §7.1）;
2. policy.py `build_create_request` 扩展 + fake_server `background:true` 建模;
3. 启动器（entrypoint/恢复门与状态/env/per-instance secret/健康/renew 钳制）;
4. 代理（integration 先行）;
5. manager 分叉（实例模型/prestart/停止/空闲回收/allocator/shutdown）+ user-url + docker-entrypoint env;
6. 快照（守卫/上界/独立根）+ generation reconcile（env 门 + TESTING guard + fail-soft）;
7. Dockerfile + 文档;
8. 全量回归（unit + integration/routes + #3375 全套）。
