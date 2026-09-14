# 多用户隔离验收手册（#3379 / #3374）

本手册描述如何在**真实 Linux 多用户部署**上执行 #3374 的九项验收清单，如何阅读
`scripts/multiuser_acceptance.py` 产出的验收记录，以及哪些边界以**声明性豁免**
的形式覆盖（而非自动化断言）。方案终稿见
`docs/dev-notes/3379-multiuser-acceptance-plan.md`（v2.1，已审查通过）。

## 1. 环境前提

**产品前置（与 PR-A/#3384 同级）**：
- **#3387/#3110**：应用配置解析必须遵循 `OPENACE_CONFIG_DIR`（当前读
  `~/.open-ace`，读不到 config 卷）。修复前脚本在配置校验处快速失败并指名 #3110。
- **#3389 共享命名空间预置**：产品原无任何步骤创建 `<base>/shared`——d 项曾把全新
  部署的 403 记为声明已知缺口。
- **#3390（UID 漂移）**：容器重建后 entrypoint 按在役用户重新 useradd 且不固定
  uid，已停用用户的目录被在役账号数字继承——f 项断言预期 FAIL，如实记录。
- **#3394（已修，PR #3395）**：前端完整性检查期望与 vite 产物不符导致生产镜像
  crash-loop。
- **#3397（声明偏离）**：按 DEPLOYMENT.md 首装的多用户生产部署无法启动（空库被
  拒；仅迁移又拿不到默认 admin）。脚本用一次性容器 `alembic upgrade head &&
  init_db.py` 绕过，**属声明偏离**，见运行备注——产品修复后应还原为纯文档路径。
- **#3396（OS 层共享隔离）**：共享目录为全局 `openace-shared` 组 2775/664——跨租户
  与撤销后的 OS 层读写依然存在。d 项已加 OS 层探针，预期 FAIL，如实记录。

- 真实 Linux 主机（`linux` + `docker` CLI + compose v2；脚本启动时强制检查）。
- 足够拉起 3 个 qwen-code-webui 实例的内存（每实例约数百 MB）。
- **全新栈**：脚本要求默认 admin 处于 must_change_password 状态；若之前跑过，先清理
  **专用项目**（不是默认项目——后者可能是同一 checkout 下的生产部署）：
  `docker compose -p acceptance-multi -f docker-compose.yml -f docker-compose.multi-user.yml down -v --remove-orphans`。
- **主机独占**：多用户栈沿用基础 compose 的全局唯一容器名（`open-ace` 等）并占用
  19888 与 3100–3200 端口——主机上已有产品栈在跑时验收会在 `up` 处干净中止（容器名/
  端口冲突）。如需共存，后续可像单用户尾段一样生成改名+偏移端口的 override（当前以
  声明代替）。
- CI 上由独立 job `multiuser-acceptance` 执行（main-push 触发，观察期），自带
  buildx 构建并以 `IMAGE_NAME=open-ace:$GITHUB_SHA` 驱动 compose——受测代码与
  镜像指纹严格对应，不会误拉 `openace/open-ace:latest`。

## 2. 执行步骤

```bash
# 本地（在仓库根目录；可先 export IMAGE_NAME=open-ace:<tag> 指定待测镜像）
python3 scripts/multiuser_acceptance.py

# 保留多用户栈做人工复核（单用户回归尾段始终自建自清,不受影响）
ACCEPTANCE_KEEP_STACK=1 python3 scripts/multiuser_acceptance.py

# 自定义服务地址 / 记录目录
ACCEPTANCE_BASE_URL=http://host:19888 \
ACCEPTANCE_RECORD_DIR=./my-records \
python3 scripts/multiuser_acceptance.py
```

脚本流程：**拒绝非空的专用项目状态** → bootstrap env（生成三个必填密钥）→
**两段式配置**（首次 `up` 由 entrypoint 生成完整多用户配置 → stop → 用同镜像辅助
容器只合并 `max_instances=3` → 二次 `up`）→ 默认 admin 首登改密 → 建两租户五用户
（tenant-1：alice/bob/dave/erin，tenant-2：carol；`system_account` 触发真实
useradd wrapper）→ 九项断言 → 单用户回归尾段 → `down -v`（KEEP_STACK 除外）。

两套栈都运行在**专用 compose 项目**（`acceptance-multi` / `acceptance-single`）里——
同一 checkout 旁的已有部署永远不会被触碰；脚本开始时若发现专用项目残留容器/卷会拒绝
执行（退出码 `2`，并打印清理命令）。

退出码：`0` 全部通过；`1` 有失败项或中途终止（两种情况下 compose logs 均落盘进记录
目录——含跑完但有 FAIL 的路径）；`2` 环境不满足或专用项目非空。

在分支上真实首跑（合入前）：workflow 的 `pull_request` 触发器使用 **PR 分支里的**
workflow 文件，所以触碰 `scripts/multiuser_acceptance.py` / 本 workflow / 手册的 PR
会在 CI 上真实执行验收（`workflow_dispatch` 需要默认分支先注册该 workflow，合入前
不可用——404 的原因）。

## 3. 九项清单：攻击 / 期望对照表

| # | 清单项 | 自动化攻击 | 期望（PASS 判据） |
|---|---|---|---|
| a | 并发私有目录/历史/模型配置 | alice/bob 并发取 user-url；容器内扫 /proc | 各自端口、各自 UID（sudo -u 生效）、各自 0700 home；webui env 仅含互异的 `webui:<uid>` 代理 token，`OPENAI_API_KEY`==代理 token，无任何真实/敏感 key |
| b | ID 篡改/穿越/symlink/越权恢复 | `required_isolation=none`；A 的会话 token 打 B 的会话路由；DB 播种 machines/agent_sessions/machine_assignments 后 B 停/连 A 的终端；fs browse B 的 workspace home（`/workspace/<B>`）、`../`、指向 B 的 symlink | floor 不降（仍 os_user）；403/404 矩阵逐项命中；fs 三例全部 400——`/workspace/<B>` 路径穿过 base-dir 门后由 realpath 解析 + #3376 home-lock 拒绝（`/home/*` 只会被 base-dir 前缀门拦下,测不到 home-lock,故攻击面选 workspace 侧） |
| c | 环境无他人凭据；A 不进 B 私有区 | `docker exec -u alice` 读 B 的 webui `/proc/<pid>/environ`、`ls /home/bob` | EPERM/EACCES（真实 UID 语义）；模型配置分离同 a 项口径 |
| d | 共享项目授权与撤销 | alice 建 `<base>/shared/acc-team-proj`；bob（同租户）/carol（异租户）browse；撤销后再 browse；**OS 层探针**（carol/bob 的 shell ls/touch，与 c 项同一通道） | bob 200、carol 400；撤销后 bob 400；OS 层探针预期 FAIL（全局 openace-shared 组，#3396 如实记录） |
| e | 资源上限/取消/异常退出不影响他人 | 预置 `max_instances=3`；第 4 实例；admin 停 alice 实例；`kill -9` bob 的 webui | 第 4 实例 503（body 非结构化——如实记录，本身是验收发现）；他人会话与 /readyz 不受扰；释放的槽位可复用 |
| f | 停用用户/撤销 token/重启 orphan | 停用 bob 后查会话/URL-token/进程/代理 token（代理 token 取自 webui 环境的 `OPENAI_API_KEY`——sudo 启动路径只内联该键集）；`up -d --force-recreate`（重建容器、保留卷——`restart` 不重建可写层，分辨不出 secret 是否真落在卷上）后容器内查进程与端口、alice 旧 token 复验 | 全部 401/进程销毁/代理 token 401；重建后无残留 webui 进程、3100–3200 容器内无监听；token_secret 卷持久化使旧 token 仍有效（#3377 的真实主张）。**依赖 PR-A（3384）** |
| g | backend 不支持时明确拒绝 | 契约端点；user-url 请求 `sandboxed`；无映射用户（erin）请求 `os_user` | 契约 `isolation_level=os_user` 且 reasons **不含任何** SANDBOX_PROBE_REASON_CODES；400 `isolation_level_unsupported`；400 `identity_mapping_missing` |
| h | 单用户模式无退化 | 独立 compose 项目（端口 19889）起基础栈,自带全新卷,不影响多用户栈 | 契约 `none`、单实例 3100（若触发 app 侧单用户启动限制则记声明豁免,见 §5.8）、admin 登录、/readyz 200 |
| i | 发布样例/权限条件/能力矩阵/真实结果 | 记录器 | 记录含 git SHA、镜像 digest、docker/compose 版本、内核、policy_revision；能力矩阵交叉引用 `WORKSPACE_ISOLATION_CAPABILITIES` |

## 4. 记录产物与模板

- 位置：`ACCEPTANCE_RECORD_DIR`（默认 `test-results/acceptance-records/`；CI 上传
  artifact，90 天保留，**不作长期归档**——终版记录应贴入 #3374 或提交入库）。
- `multiuser-acceptance-<utc-ts>.json`：逐项 PASS/FAIL + **通过项也保留截断的
  请求/响应对**（`items[].evidence`，400 字符截断）+ 环境指纹 + 运行备注。
- 同名 `.md`：人读对照表（上表的实际执行结果版）。
- 留档空模板（运维复制填写）：

```markdown
# 多用户隔离验收记录（人工执行版）
- 执行时间（UTC）：
- 执行人：
- git SHA / 镜像 digest：
- docker/compose 版本 / 内核：
- policy_revision：
- 结果总览：__ passed / __ failed
- 逐项结果与证据：（按 §3 表格逐行填写）
- 人工复核项（浏览器双开截图、默认 UI 点检）：
- 偏差与说明：
```

## 5. 声明性豁免与边界（如实声明，非遗漏）

1. **sandboxed 等级**：compose 形态不挂载 `sandbox-backends.json`，契约确定性断言
   `isolation_level=os_user` 且 reasons 不含任何沙箱探针码（backend-unconfigured 被
   契约刻意静默，multi_process 无 backend 时不可达）。真实集群可用时的**扩展清单**
   （六条外部假设，逐项追加记录）：3100 端点可达性、entrypoint 上游约束、renew 格式、
   网关凭据自捕获边界、单 web 进程假设、boot probe 升级与 memo TTL。
2. **VSCode 归属门闸**：其 store 为进程内存态，无法从外部播种——以单测
   （`tests/unit/test_vscode_ownership_3376.py`）覆盖引用 + 本声明豁免（compose 无
   remote agent）。
3. **remote 路径校验**：`is_valid_remote_path` 是纯结构性校验，不拒绝"他人 home
   形状"的路径——#3376 的语义边界，如实记录，不作为本验收失败项。
4. **终端等价性论证**：脚本以 `docker exec -u alice ls /home/bob` 断言 EACCES。
   终端中的命令同样以 alice 的真实 UID 执行（终端进程本身运行在该 UID 下），
   OS 层面无差别——`docker exec -u` 与"用户 A 的终端"在同一 UID 语义上等价。
5. **"临时材料按策略清理"**：compose 形态下即容器/卷生命周期语义（`down -v`）。
   "控制面重启而容器不死"的形态在 compose 不存在（restart 即整容器重启），该
   形态留待 K8s 部署验收。
6. **"取消任务"**：以实例停止 + 会话终止近似（停 alice 实例后 bob 不受扰即为
   期望行为），覆盖边界如本条声明。
7. **max_instances 503 body 非结构化**：如实记录——这本身是一条验收发现，不是
   断言失败。
8. **默认 admin 的 3100 启动（能力分离制豁免）**：单用户容器以 uid 1000 运行且不会
   为默认 admin 预置 OS 账号，sudo 启动路径在该形态下无法为 admin 拉起 3100 实例——
   app 侧已知映射限制。脚本先做**真实能力断言**：创建 `system_account=open-ace`（容器
   自身账户）的用户走免 sudo 直启分支，其 user-url 必须 200/:3100——该断言失败则 admin
   的 502/503 也一律 FAIL（回归不被吞）；能力断言通过时，admin 的 502/503 才记 EXEMPT
   （声明限制）。待 app 侧为 admin 提供映射后豁免自动收敛。另注：单用户尾段
   在独立 compose 项目中运行（web 端口 19889、工作区端口段偏移到 13100–13200，避免
   与多用户栈的 3100–3200 冲突）——该形态下单用户 webui 不在其广播的主机 URL 上可达，
   h 项断言全部为 API 层，不依赖直连它。

## 6. 部署注记（承接 #3384）

`update_user(is_active=false)` 自 #3384 起同条 UPDATE 写 `tokens_valid_after` 列。
**开发库拉取该变更后需先 `alembic upgrade head` 再触发停用**，否则停用报 500
（组织同步路径则静默不生效）。生产 PG 启动时有 REQUIRE_HEAD 迁移检查，缺迁移
起不来，不受影响。

## 7. 人工复核点（自动化之外）

- a 项：浏览器双开（alice/bob 两个身份）截图，目视各自的私有目录与历史。
- h 项：单用户模式默认 UI 人工点检（工作区打开、文件浏览）。
- 截图与点检结论附在记录模板的"人工复核项"一节。
