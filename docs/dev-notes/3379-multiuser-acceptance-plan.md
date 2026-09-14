> 历史方案记录（v2.1，已按 #3383 后的惯例从 docs/superpowers/ 迁至 docs/dev-notes/）。
> 落地差异：脚本定名 `scripts/multiuser_acceptance.py`（按 scripts/ 功能命名惯例，去掉 issue 编号）；
> PR 评审 round 3 起改为专用 compose 项目 + 两段式 config 合并（entrypoint 生成后再改
> max_instances，不再首启前预置 3 键 JSON），并以 #3110（app 读取 ~/.open-ace 而非
> OPENACE_CONFIG_DIR）为声明前置。执行细节以双语手册为准：
> docs/{cn,en}/MULTIUSER_ISOLATION_ACCEPTANCE.md。

# Issue #3379: 多用户模式真实 Linux 端到端隔离验收 — 方案 v2.1（终稿）

状态: **审查通过（APPROVE）**,进入实现（审查轨迹: 首轮 1B+4M+4m+2Q → v2 复核 2M+5m+1Q → v2.1 终审 APPROVE 含 3 处编辑级残留,已随手清零）
依赖: #3375/#3376/#3377/#3378 全部合入 main（9af57bde）;**前置产品修复 PR-A**（见 §0）

v2 变更摘要:
- **[BLOCKER→§0 前置 PR-A]** 停用用户不断实例/不吊销 URL-token 是真实产品缺口（与 #3374 清单原文矛盾）——新增前置修复: 停用即停实例 + token 校验查用户活跃状态;验收断言从"预期暴露缺口"改为"修复后预期 PASS";
- **[MAJOR→§3 sandboxed]** 契约豁免断言改为确定性: compose（无 sandbox-backends.json）下 \`isolation_level=os_user\` 且 reasons **不含任何** SANDBOX_PROBE_REASON_CODES 码（backend-unconfigured 被刻意静默,multi_process 不可达）;gate 层 \`sandboxed → 400 isolation_level_unsupported\`（reasons 空落回通用码）;
- **[MAJOR→§3-b]** terminal/vscode 断言改为**会话归属 403/404 矩阵**（remote 路径校验是纯结构性的,/home/bob 不会 400）;terminal 经 DB 预置 agent_sessions 行 + machine_assignments 驱动（无 live agent 时门闸先于 send_command）;vscode 的 in-memory store 无法外部播种 → 单测覆盖引用 + 手册豁免声明（compose 无 remote agent）;
- **[MAJOR→§5]** CI 显式 \`IMAGE_NAME=open-ace:$GITHUB_SHA\`（否则 compose 拉 openace/open-ace:latest,受测代码与指纹脱钩）;
- **[MAJOR→§3-e]** max_instances 经**预置 config.json**（首启前写入 config 卷,workspace.max_instances=3）调小——不真实拉 30 个进程;503 body 如实记录并标注"非结构化"（本身是验收发现）;
- **[MAJOR→§3-f]** restart 断言全部**容器内**执行（compose exec ps/ss——宿主侧 docker-proxy 对发布端口恒监听,恒假）;容器级 restart 与"控制面重启而容器不死"形态的差异入手册声明;
- **[MINOR]** \`up -d --wait\`;模型配置分离断言（env 不含任何动态/敏感 envKeys 真实值,仅含互异 proxy token）;取消任务近似+声明;临时材料=容器生命周期声明;VSCode 豁免;记录含通过项的截断 req/resp、git SHA、\`docker inspect\` 镜像 digest、docker/compose 版本、policy_revision,终版记录贴 #3374 或入库;手册 docs/cn+docs/en 双语;
- **[QUESTION]** CI 新建独立 job（不并入 docker job,观察期）+ timeout-minutes + stdlib urllib（免 pip install）;\`sudo -u alice ls /home/bob\` 的终端等价性写入手册论证。

## 0. 前置产品修复（PR-A,独立小 PR 先行合入）

**缺口**: \`DELETE /api/admin/users/<id>\`（admin.py:414-483）只撤 session + 软删;webui token 校验（webui_manager validate_token v1/v2）只验签名与 TTL,不查用户状态;URL-token 认证路径的 \`get_user_by_id\` 无 is_active/deleted_at 过滤——停用用户的实例、URL token、proxy token 全部继续可用,与 #3374 清单"停用用户/撤销 token 后无法恢复继续执行"矛盾。

**修复**: (1) 停用路径增加: 停止该用户的 WebUI 实例（multi-user stop_user_webui / single-user 若 owner 相符）——实例停止顺带撤销 \`webui:<uid>\` proxy token;(2) webui token 校验在解析出 user_id 后查用户活跃状态（user_repo 显式 is_active + 未软删检查）,不活跃 → 拒绝（fail-closed;该路径是 webui iframe 的常规流量——fs/quota/projects 每请求带 ?token= 走此回退——但单条主键查询成本低,DB 异常时 fail-closed 是正确方向）。测试: 停用后 session/URL-token 401、实例销毁、proxy token 撤销;活跃用户不受影响。
Worktree: `.worktrees/3379-acceptance`（branch `feat/3379-multiuser-acceptance`）

## 1. 目标与交付物

在**真实 Linux 多用户部署**（docker-compose.multi-user.yml，生产镜像、真实 useradd/sudo wrapper、真实进程）上执行 #3374 的 9 项验收清单，产出**可复核的验收记录**，作为关闭 #3374 的依据。API mock 或仅改目录名不替代运行隔离验证（#3374 原文）。

交付物：

1. **`scripts/multiuser_acceptance.py`** — 独立验收脚本（不进 pytest 收集，#2457 先例）：bootstrap env → 预置 config → compose overlay `up -d --wait` → 建 two-tenant three-user 场景 → 按 9 项清单执行硬断言 → 产出带时间戳的 JSON+MD 验收记录;
2. **`docs/MULTIUSER_ISOLATION_ACCEPTANCE.md`** — 验收手册：执行步骤、环境前提、9 项清单的攻击/期望对照表（3376 design §7 式）、记录模板、sandboxed 声明性豁免与集群扩展清单;
3. **CI 接线** — ci.yml 新增**独立 job** `multiuser-acceptance`（main-push 触发、观察期独立不并入 docker job）：**自带镜像构建步骤**（与 docker job 同款 build-push-action local load，产出 `open-ace:$GITHUB_SHA`——job 间不共享本地镜像，不自建则 IMAGE_NAME 落空）、`IMAGE_NAME=open-ace:$GITHUB_SHA` 驱动 compose、显式 `timeout-minutes`、验收记录与失败时 `compose logs` 上传 artifact。

## 2. 载体决策（调研结论）

**独立脚本为主 + 手册留档为凭 + CI main-push 挂载为可复现证明，不做 PR-gated pytest lane。** 理由：
- 先例最强：`scripts/multiuser_smoke.py`（CI docker job 内调用、硬断言、非零退出）与 `scripts/manual_e2e_quota_enforcement.py`（BASE_URL env 驱动、刻意不进 pytest，#2457 教训写进其 docstring）;
- e2e governance 登记成本（ci/e2e-inventory.json 271 条）、DinD root 依赖、lane 预算装不下完整清单——pytest lane 三项全占;
- `multiuser_smoke.py` 是镜像内单容器冒烟（不起 compose、不打 HTTP）——本脚本补的正是缺的那层。

## 3. 九项清单的验收设计（脚本断言 × 人工复核点）

场景搭建：`bootstrap_compose_env.py` 生成 env → **预置 config.json 进 config 卷**（机制：helper 容器写入——**按 label 过滤定位卷**（com.docker.compose.project + com.docker.compose.volume,先例 bootstrap_compose_env.py:89-98）,不手拼卷名（项目名规范化推导有脚枪,拼错会静默新建孤儿卷）;首个 `up` 前完成,首启生成只在无 config 时运行）→ `docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait` → 默认 admin 首登改密（must_change_password 流程）→ 建 tenant-1{alice,bob} + tenant-2{carol}（均带 system_account，POST /api/admin/users 触发真实 useradd wrapper）。

| # | 清单项 | 脚本断言（自动化） | 人工复核点（记录表） |
|---|---|---|---|
| a | 并发用户私有目录/历史/**模型配置** | alice/bob 并发各取 user-url → 各自端口、各自进程 UID（容器内 `ps` 断言 sudo -u 生效）、各自 700 home;webui 历史目录按账户分离;**模型配置分离**: 两实例 env **不含任何动态/敏感 envKeys 的真实值**（动态键在注入前被清洗,api_key_proxy.py:100/1822）,仅含互异的 `webui:<uid>` proxy token——与 c 项 env dump 断言同一口径 | 浏览器双开截图 |
| b | ID 篡改/cwd 替换/symlink/穿越/历史恢复越权 | user-url 带 `required_isolation=none` 不能降 floor;A 的 session token 打 B 的 workspace 路由 → 403/404 矩阵;**terminal 会话归属**: DB 播种三件套——machines 表机器 M 行、alice 的 agent_sessions 行（挂 M）、bob 的 machine_assignments 行（挂 M;attach 的 machine_id 从 body 读）→ bob 调 stop/attach 带 alice 会话 → 403,伪造不存在会话 → 404（门闸先于 agent 交互,无需 live agent）;fs browse/check-path 打 B 的 home、`../` 与 A home 下指向 B 的 symlink（root 侧预置）→ 400;restore_session 跨用户 → 403/404 | vscode 归属门闸（in-memory store 无法外部播种）: 单测覆盖引用（test_vscode_ownership_3376.py,25 个测试函数）+ 手册豁免声明（compose 无 remote agent）;remote 路径结构性校验（is_valid_remote_path）为纯形状校验、不拒他人 home 路径——手册如实说明 #3376 的语义边界 |
| c | 环境/进程信息无他人 key/token;A 终端/WebUI/VSCode 不进 B 私有区 | `/proc/<B-webui-pid>/environ` 以 A 的 shell 读 → EPERM（或内容无 B 凭据）;A 的 webui env 全量 dump 断言无真实 API key（仅 proxy token）;**终端等价断言** `docker exec … sudo -u alice ls /home/bob` → EACCES（真实 UID 语义;与"用户 A 的终端"的等价性入手册论证——终端命令以 alice UID 执行,OS 层无差别） | VSCode: 同 b 项豁免 |
| d | 共享项目授权与撤销 | alice 建 `<base>/shared/team-proj` → bob（同租户）browse 可达、carol（异租户）不可达;删共享行后 bob browse → 400 | — |
| e | 资源上限/取消/异常退出不影响他人 | **预置 config.json（首启前写 config 卷）调 max_instances=3** → 第 4 实例 503（body 如实记录并标注非结构化——本身是验收发现）;停 alice 实例 → bob 会话与 /readyz 不受扰;kill -9 bob 的 webui 进程 → bob 实例健康检查不受扰、治理面存活;"取消任务"以实例停止+会话终止近似,手册声明覆盖边界 | — |
| f | 停用用户/撤销 token/重启 orphan（**依赖 PR-A**） | 停用 bob → session 401、**URL-token 401（PR-A 后）**、实例销毁、proxy token 撤销、prestart 不再发生;`docker compose restart open-ace` → **容器内**（compose exec）断言: 无残留 sudo webui 进程、3100-3200 无监听（宿主侧 docker-proxy 恒监听,不作断言）、token_secret 持久化使 token 重启后仍有效（#3377 验证点） | "临时材料按策略清理"= 容器生命周期语义,手册声明;控制面重启而容器不死的形态在 compose 不存在,手册声明 |
| g | backend 不支持时明确拒绝 | 契约端点断言 `isolation_level=os_user` + enforced/unsupported 维度、**reasons 不含任何 SANDBOX_PROBE_REASON_CODES 码**（backend-unconfigured 被契约静默,multi_process 在无 backend 时不可达——确定性断言）;user-url 请求 `sandboxed` → 400 `isolation_level_unsupported`（reasons 空落回通用码）;映射缺失用户默认路径 → 400 `identity_mapping_missing`;无共享 UID 回退（进程 UID 断言） | — |
| h | 单用户模式无退化 | 验收末段 `compose down` → 基础 compose（单用户）up → 契约 `none`/单实例 3100、admin 登录、/readyz 冒烟 | 默认 UI 人工点检 |
| i | 发布样例/权限条件/能力矩阵/真实结果 | 脚本产出记录文件含环境指纹（镜像 digest、内核版本、runtime）;文档交叉引用既有 §5/§6.1 与 DEPLOYMENT 文档 | 记录归档 |

**sandboxed 声明性豁免（确定性断言，与 g 行一致）**：compose 形态（不挂载 sandbox-backends.json）下，契约端点 `isolation_level=os_user` 且 `reasons` **不含任何** `SANDBOX_PROBE_REASON_CODES` 码——backend-unconfigured 被契约刻意静默、multi_process 在无 backend 时不可达（contract.py :346-367/:613-618/:444 的语义链）;gate 层请求 `sandboxed` → 400 `isolation_level_unsupported`（reasons 空落回通用码）。**集群可用时的扩展清单**（写入手册，不在本脚本自动化）：6 条外部假设逐项（3100 端点可达性、entrypoint 上游约束、renew 格式、网关凭据自捕获边界、单 web 进程假设、boot probe 升级与 memo TTL）——留待真实集群执行时追加记录。

## 4. 记录产物

- `acceptance-records/`（脚本输出目录，CI 上传 artifact;本地运行写 `test-results/` 下）: `multiuser-acceptance-<utc-ts>.json`（逐项 PASS/FAIL + **通过项也留截断 req/resp 对**）+ 同名 `.md`（人读对照表）;
- 环境指纹: git SHA、镜像 digest（`docker inspect --format` 输出）、docker/compose 版本、内核版本、契约 `policy_revision`;
- 终版（关闭 #3374 时）记录贴入 #3374 或提交入库——CI artifact 90 天保留不作长期归档;
- 手册含空模板供运维留档;docs/cn + docs/en 双语（对齐 DEPLOYMENT/NGINX 惯例）。

## 5. 工程约束

- 脚本遵循 `multiuser_smoke.py` 风格：`main()` 强制 linux + docker 可用（`shutil.which("docker")`），非零退出码 = 验收失败;env 驱动（`ACCEPTANCE_BASE_URL` 缺省 http://localhost:19888;`ACCEPTANCE_KEEP_STACK=1` 保留现场便于人工复核）;**HTTP 用 stdlib urllib**（runner 免 pip install）;
- 不进 pytest 收集（文件名不含 test_ 前缀、无 pytest 依赖）;
- compose 以 `up -d --wait` 起（attached 会挂前台日志）;
- 场景搭建顺序: bootstrap env（三个 `:?` 密钥）→ **预置 config.json 进 config 卷**（workspace.enabled/multi_user_mode/max_instances=3,首启生成只在无 config 时运行）→ up -d --wait → 先建租户（POST /api/tenants）再建用户（强制 tenant_id;POST 触发真实 useradd wrapper）;
- CI: 独立 job `multiuser-acceptance`（main-push、观察期;**自带 buildx 构建+load 步骤**产出 `open-ace:$GITHUB_SHA`——job 间不共享本地镜像）、`IMAGE_NAME=open-ace:$GITHUB_SHA`（否则 compose 拉 openace/open-ace:latest,受测代码与指纹脱钩）、`timeout-minutes` 显式、`docker compose` 二进制可用性先行验证、失败 dump `compose logs` 进 artifact;
- 清理：验收结束 `compose down -v`（KEEP_STACK 例外）。

## 6. 任务顺序

0. **PR-A 前置产品修复**（停用停实例 + token 校验查活跃状态;独立分支先行）;
1. 脚本骨架（env/bootstrap/up -d --wait/预置 config/场景搭建/记录器）;
2. 9 项断言逐项实现（a→i）+ sandboxed 豁免断言;
3. 单用户回归段（h）;
4. 手册文档 + 记录模板;
5. CI 独立 job 接线（自带镜像构建）+ artifact;
6. 独立 agent 审查至零意见 → PR。
