> 历史方案记录（v2.1，已按 #3383 后的惯例从 docs/superpowers/ 迁至 docs/dev-notes/）。
> 落地差异：脚本定名 `scripts/multiuser_acceptance.py`（按 scripts/ 功能命名惯例，去掉 issue 编号）；
> PR 评审 round 3 起改为专用 compose 项目 + 两段式 config 合并（entrypoint 生成后再改
> max_instances，不再首启前预置 3 键 JSON），并以 #3110（app 读取 ~/.open-ace 而非
> OPENACE_CONFIG_DIR）为声明前置。执行细节以本文 §7 执行手册为准
> （原双语手册 docs/{cn,en}/MULTIUSER_ISOLATION_ACCEPTANCE.md 已全文并入本文件）。

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

---

## 7. 执行手册（落地终版；原双语手册 docs/{en,cn}/MULTIUSER_ISOLATION_ACCEPTANCE.md 全文并入）

> 以下为验收手册终版（英文原文，其标题层级已降一级）。它吸收了实现期的全部变更：
> 专用 compose 项目（acceptance-multi / acceptance-single）、两段式 config、两租户五用户
> 场景、#3387–#3397 的修复/偏离记录、退出码语义与 8 项声明性豁免。与上文方案 v2.1
> 不一致处（如场景用户数、config 预置方式），以本节为准。

This handbook describes how to execute the #3374 nine-item acceptance checklist
against a **real multi-user Linux deployment**, how to read the acceptance
record produced by `scripts/multiuser_acceptance.py`, and which
boundaries are covered by **declared exemptions** rather than automated
assertions. The approved plan of record is
`docs/dev-notes/3379-multiuser-acceptance-plan.md` (v2.1).

### 1. Environment prerequisites

**Product prerequisites (same tier as PR-A/#3384)**:
- **#3387/#3110 (merged)**: the app's config resolution honors
  `OPENACE_CONFIG_DIR` (it used to read `~/.open-ace` and never see the
  config volume).
- **#3389 shared-namespace provisioning**: nothing in the product created
  `<base>/shared` — item (d) used to record the fresh-deployment 403 as a
  declared known gap; the entrypoint now provisions it (openace-shared
  group, **3770**: sticky + setgid, NO others bits — the earlier 3775's
  others r-x let any non-member process on the host list shared project
  NAMES; declared residual, inherent to the namespace design: every active
  account joins the global group for namespace creation, so cross-tenant
  members can still list the root via the group r-x — content access stays
  fenced by the per-tenant groups).
- **#3393 (fixed; app-side on-demand provisioning)**: on a shape the
  entrypoint never reaches (package installs, or a lost/missing root), the
  FIRST shared-project creation (`create_dir: true` at `<base>/shared/<name>`)
  still mkdir'd against the missing root. POST /api/projects now provisions
  the namespace root on demand BEFORE the user-side mkdir (root-owned,
  `openace-shared` group, **3770** = sticky+setgid, no others bits — the
  entrypoint's semantics; an existing root is left untouched, failures
  degrade to a clean 5xx); the multi-user path of
  `scripts/install-central/package-method/install.sh` provisions the
  equivalent at install time.- **#3390 (UID drift)**: on container recreation the entrypoint re-useradds
  active users without uid pinning — a deactivated user's directories are
  numerically inherited by an active account. Item (f) asserts this by
  owner name and is expected to FAIL until the fix lands (recorded
  honestly).
- **#3394 (fixed, PR #3395)**: the frontend integrity check expected a
  `main.*.js` that vite never produced — production images crash-looped.
- **#3396 (fixed, tenant-scoped groups)**: shared dirs used to be group
  openace-shared (GLOBAL — every tenant's account joins) 2775/664, leaving
  cross-tenant and post-revocation OS-level access open. Content is now
  group-owned by per-tenant `openace-shared-<tenant_id>` with 2770/660 (the
  global group keeps only the namespace-root creation right), and revocation
  reclaims the directory to the creator (chown -R + 0700/0600). Item (d)'s
  OS-layer probes (denials for bob/carol plus positive controls for alice)
  are expected to PASS.
  - **The `openace-shared-0` pseudo-tenant (#3396 semantics)**: shared
    projects of NULL-tenant (platform admin) users use group
    `openace-shared-0`. It is a real OS-level pseudo-tenant, not a
    placeholder: platform admins share shared-project content among
    themselves through that group. The read side
    (`_allowed_roots_for_user` in `fs.py`) hides NULL-tenant shared roots
    from tenant users, so this sharing is OS-group-only among platform
    admins. Real tenant ids start at 1, so the pseudo-id 0 never collides.
- **#3397 (declared deviation)**: a fresh multi-user production deployment
  following DEPLOYMENT.md cannot start (empty DB refused; a bare migration
  leaves no default admin). The script works around it with a one-shot
  `alembic upgrade head && init_db.py` container — a DECLARED deviation
  recorded in every run's notes; revert to the documented path once fixed.

- A real Linux host (`linux` + `docker` CLI + compose v2; enforced at startup).
- Enough memory for three qwen-code-webui instances (a few hundred MB each).
- **A fresh stack**: the script requires the default admin to be in the
  must_change_password state. After any previous run, first clean the
  DEDICATED project (not the default one — that may be the production
  deployment in the same checkout):
  `docker compose -p acceptance-multi -f docker-compose.yml -f docker-compose.multi-user.yml down -v --remove-orphans`.
- **Host exclusivity**: the multi-user stack inherits the base compose's
  daemon-global container names (`open-ace`, …) and holds host ports 19888
  and 3100–3200 — with a product stack already running, the acceptance
  aborts cleanly at `up` (name/port conflict). Coexistence would need a
  rename+offset override like the single-user tail's (declared, not built).
- On CI, a dedicated `multiuser-acceptance` job (main-push, observation
  period) builds its own image and drives compose with
  `IMAGE_NAME=open-ace:$GITHUB_SHA` — the tested code and the image
  fingerprint stay strictly coupled; the public `ghcr.io/open-ace/open-ace:latest` is
  never pulled by accident.

### 2. How to run

```bash
# Locally (repo root; optionally export IMAGE_NAME=open-ace:<tag> first)
python3 scripts/multiuser_acceptance.py

# Keep the MULTI-USER stack up for manual inspection (the single-user tail
# always builds and cleans its own isolated project)
ACCEPTANCE_KEEP_STACK=1 python3 scripts/multiuser_acceptance.py

# Custom service URL / record directory
ACCEPTANCE_BASE_URL=http://host:19888 \
ACCEPTANCE_RECORD_DIR=./my-records \
python3 scripts/multiuser_acceptance.py
```

Flow: **refuse if the dedicated project has state** → bootstrap env (the
three mandatory secrets) → **two-phase config** (first `up` lets the
entrypoint generate the full multi-user config → stop → a same-image helper
container merges ONLY `max_instances=3` → second `up`) → default-admin first
login with password change → two-tenant, five-user scenario (tenant-1:
alice/bob/dave/erin, tenant-2: carol; `system_account` triggers the real
useradd wrapper) → nine-item assertions → single-user regression tail →
`down -v` (unless KEEP_STACK).

Both stacks run in DEDICATED compose projects (`acceptance-multi` /
`acceptance-single`) — an existing deployment beside this checkout is never
touched; if the dedicated project already holds containers/volumes the script
refuses to run (exit `2`, printing the cleanup command).

Exit codes: `0` all passed; `1` failures or an aborted run (compose logs are
dumped into the record directory on BOTH paths — including a completed run
with failures); `2` environment unsuitable or non-empty dedicated project.

First REAL run on a branch (before merge): the workflow's `pull_request`
trigger uses the workflow file FROM THE PR BRANCH, so any PR touching
`scripts/multiuser_acceptance.py`, the workflow, or the handbooks runs the
acceptance for real in CI. (`workflow_dispatch` needs the workflow
registered on the default branch first — that is why pre-merge dispatch
404s.)

### 3. Nine-item checklist: attack / expectation matrix

| # | Checklist item | Automated attack | Expected (PASS criteria) |
|---|---|---|---|
| a | Concurrent private dirs/history/model config | alice/bob concurrent user-url; in-container /proc scan | Distinct ports, distinct UIDs (sudo -u effective), 0700 homes each; webui env carries only distinct `webui:<uid>` proxy tokens, `OPENAI_API_KEY` == proxy token, no real/sensitive keys |
| b | ID tampering / traversal / symlink / cross-user restore | `required_isolation=none`; A's session token against B's session routes; DB-seeded machines/agent_sessions/machine_assignments then B stops/attaches A's terminal; fs browse of B's workspace home (`/workspace/<B>`), `../`, symlink into B's home | Floor not lowered (stays os_user); the 403/404 matrix hits each cell; all three fs cases 400 — the `/workspace/<B>` path passes the base-dir gate and is then refused by realpath resolution + the #3376 home-lock (`/home/*` would only hit the base-dir prefix gate, never reaching the home-lock, hence the workspace-side targets) |
| c | No other user's credentials in env; A's tools stay out of B's area | `docker exec -u alice` reading B's webui `/proc/<pid>/environ`, `ls /home/bob` | EPERM/EACCES (real-UID semantics); model-config separation shares item a's evidence channel |
| d | Shared-project grant and revocation | alice creates `<base>/shared/acc-team-proj`; bob (same tenant) / carol (other tenant) browse; browse again after revocation; **OS-layer probes** (carol/bob shell ls/touch — the same channel item c argues terminal equivalence with) | bob 200, carol 400; after revocation bob 400; OS probes expected to FAIL (global openace-shared group, #3396, recorded honestly) |
| e | Resource ceilings / cancellation / crash isolation | Pre-seeded `max_instances=3`; a 4th instance; admin stops alice's instance; `kill -9` bob's webui | 4th instance 503 (body unstructured — recorded verbatim, itself an acceptance finding); others' sessions and /readyz undisturbed; the freed slot is reusable |
| f | Deactivation / token revocation / restart orphans | After deactivating bob: session, URL-token, process, proxy token (read from the webui env's `OPENAI_API_KEY` — the sudo-launch path inlines only that known key set); after `up -d --force-recreate` (container recreated, volumes kept — `restart` keeps the writable layer and cannot distinguish a secret on the volume from one left in the layer): in-container process and port checks; alice's old token re-verified | All 401 / instance destroyed / proxy token 401; no leftover webui processes after recreation, nothing listening on 3100–3200 in-container; token_secret volume persistence keeps the old token valid (#3377's actual claim). **Depends on PR-A (#3384)** |
| g | Explicit refusal when a backend/level is unavailable | Contract endpoint; user-url requesting `sandboxed`; unmapped user (erin) requesting `os_user` | Contract `isolation_level=os_user` with reasons containing **no** SANDBOX_PROBE_REASON_CODES; 400 `isolation_level_unsupported`; 400 `identity_mapping_missing` |
| h | No regression in single-user mode | Base compose in its OWN project (port 19889, fresh volumes), leaving the multi-user stack untouched | Contract `none`, single instance on the configured range's first free port (default 3100; the offset tail shape gets 13100 — the hardcoded-3100 leftover is fixed, §5.8) (recorded as a declared exemption if the app-side single-user launch limitation fires — §5.8), admin login, /readyz 200 |
| i | Publishable sample / permission conditions / capability matrix / real results | Recorder | Record carries git SHA, image digest, docker/compose versions, kernel, policy_revision; capability matrix cross-references `WORKSPACE_ISOLATION_CAPABILITIES` |

### 4. Records and template

- Location: `ACCEPTANCE_RECORD_DIR` (default
  `test-results/acceptance-records/`; uploaded as a CI artifact with 90-day
  retention — **not a long-term archive**; the final record should be posted
  into #3374 or committed to the repo).
- `multiuser-acceptance-<utc-ts>.json`: per-item PASS/FAIL plus **truncated
  request/response pairs even for passing items** (`items[].evidence`,
  truncated at 400 chars) + environment fingerprint + run notes.
- A same-named `.md`: the human-readable matrix (the executed version of the
  table above).
- Blank template for operators:

```markdown
# Multi-user isolation acceptance record (manual run)
- Executed at (UTC):
- Operator:
- git SHA / image digest:
- docker/compose versions / kernel:
- policy_revision:
- Summary: __ passed / __ failed
- Per-item results and evidence: (fill per the §3 matrix)
- Manual review items (dual-browser screenshots, default-UI walkthrough):
- Deviations and notes:
```

### 5. Declared exemptions and boundaries (deliberate, not oversights)

1. **sandboxed level**: the compose shape mounts no `sandbox-backends.json`;
   the contract deterministically reports `isolation_level=os_user` with no
   sandbox probe codes in reasons (backend-unconfigured is deliberately
   silenced by the contract; multi_process is unreachable without a backend).
   When a real cluster is available, the **extension checklist** (six external
   assumptions, one record row each): 3100 endpoint reachability, entrypoint
   upstream constraints, renew format, gateway-credential self-capture
   boundary, single-web-process assumption, boot-probe upgrade and memo TTL.
2. **VSCode ownership gate**: its store is in-process memory and cannot be
   seeded externally — covered by unit tests
   (`tests/unit/test_vscode_ownership_3376.py`) plus this declared exemption
   (compose has no remote agent).
3. **Remote path validation**: `is_valid_remote_path` is a purely structural
   check and does not reject "someone else's home-shaped" paths — the #3376
   semantic boundary, recorded as-is, not a failure of this acceptance.
4. **Terminal-equivalence argument**: the script asserts EACCES via
   `docker exec -u alice ls /home/bob`. Commands inside a terminal run under
   alice's real UID (the terminal process itself runs as that UID); there is
   no OS-level difference — `docker exec -u` and "user A's terminal" are
   equivalent under the same UID semantics.
5. **"Ephemeral materials cleaned per policy"**: under compose this is
   container/volume lifecycle semantics (`down -v`). The
   "control plane restarts while the container survives" shape does not exist
   under compose (restart restarts the whole container); that shape is left
   for a Kubernetes-deployment acceptance.
6. **Task cancellation**: approximated by instance stop + session
   termination (bob being undisturbed after alice's stop is the expected
   behavior), with this boundary declared.
7. **Unstructured max_instances 503 body**: recorded verbatim — it is itself
   an acceptance finding, not an assertion failure.
8. **Default-admin 3100 launch (capability-separated exemption)**:
   single-user containers run as uid 1000 and never provision an OS account
   for the default admin, so the sudo path cannot launch the 3100 instance
   FOR THE ADMIN — a known app-side mapping limitation. The script first
   makes a REAL capability assertion: a user with
   `system_account=open-ace` (the container's own account) takes the
   sudo-free direct-launch branch and its user-url must return 200/:3100 —
   if that fails, the admin's 502/503 FAILs too (no regression can hide).
   Only with the capability proven does the admin's 502/503 record as
   EXEMPT (the declared limitation). The exemption collapses once the app
   side maps an account for the default admin. Note: the single-user tail runs in its own
   compose project (web port 19889, workspace port range offset to
   13100-13200 to avoid colliding with the multi-user stack's 3100-3200).
   The single-user instance now honors the configured range (first free
   port, default 3100 — previously hardcoded 3100, which bound an
   unpublished port and advertised an unreachable `:3100` URL in this
   offset shape), so it binds 13100 and advertises `:13100`; the item-h
   assertions remain API-level and do not depend on connecting to it
   directly.

### 6. Deployment note (carried from #3384)

Since #3384, `update_user(is_active=false)` writes the `tokens_valid_after`
column in the same UPDATE. **On development databases, run
`alembic upgrade head` after pulling that change before triggering any
deactivation**, otherwise deactivations fail with a 500 (org-sync paths fail
silently instead). Production PostgreSQL checks migration head at startup
(REQUIRE_HEAD) and refuses to boot without it — unaffected.

### 7. Manual review points (beyond automation)

- Item a: dual-browser screenshots (alice/bob identities side by side),
  visually confirming the private directories and histories.
- Item h: manual walkthrough of the single-user default UI (workspace open,
  file browsing).
- Attach screenshots and walkthrough conclusions under the "Manual review
  items" section of the record template.
