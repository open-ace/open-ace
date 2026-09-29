# Open ACE Documentation — Open ACE 文档

[English](#english) | [中文](#中文)

---

## English

Every curated document under `docs/` is a **single bilingual file**: an
`## English` section followed by a `## 中文` section. There are no per-language
directories; the published docs site splits the two sections apart at build
time (see [Docs site sync](#docs-site-sync)).

### Directory structure

```
docs/
├── README.md            ← you are here (index + placement rules)
├── guide/               # Usage & operations — for people who do not change code
├── dev/                 # Contributor docs — for people who change code
├── contracts/           # Versioned contracts parsed or pinned by guards/APIs
├── security/            # Security baselines, boundaries, exception registry
├── images/              # Documentation images
└── dev-notes/           # English-only process archive (fix retros, runbooks)
```

### Placement rules

1. **Who reads it?** Deployers, operators, integrators → `guide/`.
   Contributors and maintainers → `dev/`.
2. **Special form?** A versioned contract (`policy_revision`, guarded by tests
   or referenced from API fields) → `contracts/`. A security baseline or
   exception registry → `security/`. A generated matrix → `dev/` (regenerate,
   never hand-edit).
3. **Process write-up** (fix retrospective, incident analysis, per-issue
   design draft) → `dev-notes/` — English only, never the repo root.

New documents must follow the bilingual template (one `## English` and one
`## 中文` section); `scripts/lint/check_docs_bilingual.py` enforces it
together with index consistency and endpoint/env coverage.

### guide/ — usage & operations

| Document | Description |
|----------|-------------|
| [INTRO](guide/INTRO.md) | Product introduction, core capabilities, quick start |
| [DEPLOYMENT](guide/DEPLOYMENT.md) | Docker deployment (production-first) and local trial |
| [MULTI_USER_WORKSPACE](guide/MULTI_USER_WORKSPACE.md) | Multi-user workspace: launch modes, sudoers, port ranges |
| [WORKSPACE_ISOLATION](guide/WORKSPACE_ISOLATION.md) | Admin guide: choose, configure and verify workspace-user isolation per install method |
| [UPGRADING](guide/UPGRADING.md) | Upgrade & rollback runbook, `baseline_2026_06_23` minimum |
| [KUBERNETES](guide/KUBERNETES.md) | Kubernetes deployment (3 replicas, sticky routing) |
| [NGINX](guide/NGINX.md) | Nginx reverse proxy for HTTPS and WebSocket |
| [REMOTE_WORKSPACE](guide/REMOTE_WORKSPACE.md) | Remote workspace from the server perspective |
| [REMOTE_AGENT](guide/REMOTE_AGENT.md) | Remote agent client — install, config, CLI adapters |
| [SSO_CONFIG](guide/SSO_CONFIG.md) | SSO: SAML 2.0, OIDC, OAuth2, SLO, redirect allowlist |
| [EXTERNAL_IDENTITY](guide/EXTERNAL_IDENTITY.md) | Server-to-server external identity: HMAC probes and token exchange |
| [FEISHU_CONFIG](guide/FEISHU_CONFIG.md) | Feishu/Lark integration |
| [DINGTALK_CONFIG](guide/DINGTALK_CONFIG.md) | DingTalk integration |
| [KEY_MANAGEMENT](guide/KEY_MANAGEMENT.md) | Secret matrix, Fernet stores, atomic rotation |
| [DATABASE_BACKUP](guide/DATABASE_BACKUP.md) | Backup & recovery (Kubernetes and Docker Compose) |
| [CONFIG_REFERENCE](guide/CONFIG_REFERENCE.md) | `config.json` field reference |
| [ENV_REFERENCE](guide/ENV_REFERENCE.md) | Authoritative environment-variable table |
| [OPERATIONS](guide/OPERATIONS.md) | Health endpoints, scheduler, collectors, TLS, retention |
| [TROUBLESHOOTING](guide/TROUBLESHOOTING.md) | Cross-component troubleshooting index |

### dev/ — contributors

| Document | Description |
|----------|-------------|
| [DEVELOPMENT](dev/DEVELOPMENT.md) | Dev environment setup and first run |
| [ARCHITECTURE](dev/ARCHITECTURE.md) | System architecture — backend, frontend, agent layers |
| [MODULES](dev/MODULES.md) | The six `app/modules/` packages: duties and invariants |
| [API](dev/API.md) | REST reference — all 426 endpoints, generated inventory |
| [API_PERMISSION_MATRIX](dev/API_PERMISSION_MATRIX.md) | Generated elevated-permission matrix (do not hand-edit) |
| [PERMISSION_MODEL](dev/PERMISSION_MODEL.md) | Six-role RBAC, ten auth decorators, strict mode |
| [TOKEN_ACCOUNTING](dev/TOKEN_ACCOUNTING.md) | Token collection pipeline + Request/Message/Session concepts |
| [AUTONOMOUS_DEVELOPMENT](dev/AUTONOMOUS_DEVELOPMENT.md) | Autonomous development lifecycle and three-session design |
| [MODEL_GATEWAY](dev/MODEL_GATEWAY.md) | LiteLLM-compatible model gateway |
| [SANDBOX_BACKENDS](dev/SANDBOX_BACKENDS.md) | Where autonomous agents execute — backends and trade-offs |
| [DATABASE_SCHEMA](dev/DATABASE_SCHEMA.md) | Domain map of all 103 tables (column authority: schema SQL) |
| [DATABASE_CONVENTIONS](dev/DATABASE_CONVENTIONS.md) | Field naming conventions, `adapt_boolean` helpers |
| [SCHEMA_MIGRATION_GUIDE](dev/SCHEMA_MIGRATION_GUIDE.md) | Alembic strategy + authoring rules MIG001–MIG003 |
| [FRONTEND_GUIDE](dev/FRONTEND_GUIDE.md) | React/TypeScript frontend guide |
| [TEST_LAYERS](dev/TEST_LAYERS.md) | Test taxonomy, placement, CI execution semantics |
| [CLI_REFERENCE](dev/CLI_REFERENCE.md) | The 9 `cli.py` subcommands |
| [REPOSITORY_SETUP](dev/REPOSITORY_SETUP.md) | GitHub topics, labels, release checklist |

### contracts/ — versioned contracts

| Document | Description |
|----------|-------------|
| [AUTONOMOUS_PHASE_CONTRACTS](contracts/AUTONOMOUS_PHASE_CONTRACTS.md) | Eight-field contract per orchestrator phase (guard-tested) |
| [WORKSPACE_SESSION_DATA_CONTRACT](contracts/WORKSPACE_SESSION_DATA_CONTRACT.md) | Data boundary of the workspace session tables |
| [WORKSPACE_ISOLATION_CAPABILITIES](contracts/WORKSPACE_ISOLATION_CAPABILITIES.md) | Versioned multi-user isolation capability contract |
| [TRANSCRIPT_CONTRACT](contracts/TRANSCRIPT_CONTRACT.md) | Pinned contract for remote session transcripts and replay |

### security/

| Document | Description |
|----------|-------------|
| [API_EXCEPTIONS](security/API_EXCEPTIONS.md) | API security baseline exception registry and lifecycle |
| [BANDIT_BASELINE](security/BANDIT_BASELINE.md) | Bandit baseline maintenance guide |
| [SSH_KEY_BOUNDARY](security/SSH_KEY_BOUNDARY.md) | SSH key sync security boundary |
| [SSH_SYNC_CONFIGURATION](security/SSH_SYNC_CONFIGURATION.md) | SSH sync allowlist and review process |

### Reading paths by role

| Role | Path |
|------|------|
| Evaluating Open ACE | INTRO → ARCHITECTURE → DEPLOYMENT |
| Deployer / operator | DEPLOYMENT → WORKSPACE_ISOLATION → ENV_REFERENCE → OPERATIONS → UPGRADING |
| Something is broken | TROUBLESHOOTING → the linked component guide |
| API integrator | API → PERMISSION_MODEL → SSO_CONFIG |
| Frontend developer | FRONTEND_GUIDE → DEVELOPMENT |
| Autonomous-dev maintainer | AUTONOMOUS_DEVELOPMENT → SANDBOX_BACKENDS → contracts/ |
| Managing remote machines | REMOTE_WORKSPACE → REMOTE_AGENT |
| Maintainer | REPOSITORY_SETUP → TEST_LAYERS |

### Generated documents

- `dev/API_PERMISSION_MATRIX.md` — regenerate with
  `python3 scripts/generate_permission_matrix.py` after changing permission
  decorators.
- `dev/API.md` tables were generated from a route inventory; when you add or
  move an endpoint, `check_docs_bilingual.py` fails until the doc catches up.

### Docs site sync

The published Docusaurus site lives in the separate repository
`open-ace/open-ace-docs`. Its `scripts/sync-docs.js` runs at build time with
`OPEN_ACE_SOURCE_DIR` pointing at this repository and **splits each bilingual
file by its `## English` / `## 中文` anchors**: the English half is published
under `docs/<section>/`, the Chinese half under
`i18n/zh-Hans/docusaurus-plugin-content-docs/current/<section>/`, preserving
the site's language switcher. `dev-notes/` is not published. Because the
anchors are the split points, they are reserved: a curated document must
contain each anchor exactly once. The splitter also requires the combined
H1 (`# EN title — CN title` — the em-dash separator is where it cuts the
page title) and the nav line; `check_docs_bilingual.py` enforces all three.
At write time it rewrites `../images/` links to `/img/`, and links that
leave `docs/` (dev-notes, repo files) to GitHub blob URLs.

---

## 中文

`docs/` 下所有正式文档都是**单文件双语**：`## English` 节在前，`## 中文`
节在后。没有按语言划分的目录；文档站在构建时把两节拆开（见
[文档站同步](#文档站同步)）。

### 目录结构

```
docs/
├── README.md            ← 你在这里（索引 + 归位规则）
├── guide/               # 使用与运维 —— 不改代码的人读
├── dev/                 # 开发者文档 —— 改代码的人读
├── contracts/           # 版本化契约 —— 被守护测试或 API 字段钉住
├── security/            # 安全基线、边界、例外登记
├── images/              # 文档图片
└── dev-notes/           # 英语-only 过程归档（修复复盘、runbook）
```

### 归位规则

1. **谁读？** 部署、运维、集成方 → `guide/`；贡献者与维护者 → `dev/`。
2. **特殊形态？** 带版本号的契约（`policy_revision`、被测试解析或被 API
   字段引用）→ `contracts/`；安全基线/例外登记 → `security/`；生成矩阵 →
   `dev/`（重新生成，不要手改）。
3. **过程文档**（修复复盘、事故分析、按 issue 的方案稿）→ `dev-notes/`，
   只用英语，绝不放仓库根目录。

新文档必须遵循双语模板（`## English` 与 `## 中文` 各一节）；
`scripts/lint/check_docs_bilingual.py` 会强制检查双语完整性、索引一致性
以及端点/环境变量覆盖率。

### guide/ —— 使用与运维

| 文档 | 说明 |
|------|------|
| [INTRO](guide/INTRO.md) | 产品介绍、核心能力、快速上手 |
| [DEPLOYMENT](guide/DEPLOYMENT.md) | Docker 部署（生产路径优先）与本地试用 |
| [MULTI_USER_WORKSPACE](guide/MULTI_USER_WORKSPACE.md) | 多用户工作区：启动方式、sudoers、端口段 |
| [WORKSPACE_ISOLATION](guide/WORKSPACE_ISOLATION.md) | 管理员指南：按安装方式选择、配置并验证工作区用户隔离 |
| [UPGRADING](guide/UPGRADING.md) | 升级与回滚 runbook、`baseline_2026_06_23` 最低基线 |
| [KUBERNETES](guide/KUBERNETES.md) | Kubernetes 部署（3 副本、粘性路由） |
| [NGINX](guide/NGINX.md) | Nginx 反向代理（HTTPS 与 WebSocket） |
| [REMOTE_WORKSPACE](guide/REMOTE_WORKSPACE.md) | 服务端视角的远程工作区 |
| [REMOTE_AGENT](guide/REMOTE_AGENT.md) | 远程 Agent 客户端——安装、配置、CLI 适配器 |
| [SSO_CONFIG](guide/SSO_CONFIG.md) | SSO：SAML 2.0、OIDC、OAuth2、SLO、重定向白名单 |
| [EXTERNAL_IDENTITY](guide/EXTERNAL_IDENTITY.md) | 服务器间外部身份：HMAC 探测与 token 交换 |
| [FEISHU_CONFIG](guide/FEISHU_CONFIG.md) | 飞书集成 |
| [DINGTALK_CONFIG](guide/DINGTALK_CONFIG.md) | 钉钉集成 |
| [KEY_MANAGEMENT](guide/KEY_MANAGEMENT.md) | 密钥矩阵、Fernet store、原子轮换 |
| [DATABASE_BACKUP](guide/DATABASE_BACKUP.md) | 备份与恢复（Kubernetes 与 Docker Compose） |
| [CONFIG_REFERENCE](guide/CONFIG_REFERENCE.md) | `config.json` 字段参考 |
| [ENV_REFERENCE](guide/ENV_REFERENCE.md) | 权威环境变量总表 |
| [OPERATIONS](guide/OPERATIONS.md) | 健康端点、调度器、采集、TLS、数据保留 |
| [TROUBLESHOOTING](guide/TROUBLESHOOTING.md) | 跨组件排障索引 |

### dev/ —— 开发者

| 文档 | 说明 |
|------|------|
| [DEVELOPMENT](dev/DEVELOPMENT.md) | 开发环境搭建与首次运行 |
| [ARCHITECTURE](dev/ARCHITECTURE.md) | 系统架构——后端、前端、Agent 层 |
| [MODULES](dev/MODULES.md) | `app/modules/` 六个模块：职责与不变量 |
| [API](dev/API.md) | REST 参考——全部 426 个端点（清单生成） |
| [API_PERMISSION_MATRIX](dev/API_PERMISSION_MATRIX.md) | 生成的提权端点矩阵（勿手改） |
| [PERMISSION_MODEL](dev/PERMISSION_MODEL.md) | 六角色 RBAC、十个认证装饰器、严格模式 |
| [TOKEN_ACCOUNTING](dev/TOKEN_ACCOUNTING.md) | Token 采集链路 + Request/Message/Session 概念 |
| [AUTONOMOUS_DEVELOPMENT](dev/AUTONOMOUS_DEVELOPMENT.md) | 自主开发生命周期与三会话设计 |
| [MODEL_GATEWAY](dev/MODEL_GATEWAY.md) | LiteLLM 兼容模型网关 |
| [SANDBOX_BACKENDS](dev/SANDBOX_BACKENDS.md) | 自主 Agent 的执行地——后端与权衡 |
| [DATABASE_SCHEMA](dev/DATABASE_SCHEMA.md) | 103 张表领域地图（逐列权威：schema SQL） |
| [DATABASE_CONVENTIONS](dev/DATABASE_CONVENTIONS.md) | 字段命名约定、`adapt_boolean` 助手 |
| [SCHEMA_MIGRATION_GUIDE](dev/SCHEMA_MIGRATION_GUIDE.md) | Alembic 策略 + 编写铁律 MIG001–MIG003 |
| [FRONTEND_GUIDE](dev/FRONTEND_GUIDE.md) | React/TypeScript 前端指南 |
| [TEST_LAYERS](dev/TEST_LAYERS.md) | 测试分类、归置与 CI 执行语义 |
| [CLI_REFERENCE](dev/CLI_REFERENCE.md) | `cli.py` 九个子命令 |
| [REPOSITORY_SETUP](dev/REPOSITORY_SETUP.md) | GitHub topics、labels、发布清单 |

### contracts/ —— 版本化契约

| 文档 | 说明 |
|------|------|
| [AUTONOMOUS_PHASE_CONTRACTS](contracts/AUTONOMOUS_PHASE_CONTRACTS.md) | 每个 orchestrator phase 的八字段契约（守护测试钉住） |
| [WORKSPACE_SESSION_DATA_CONTRACT](contracts/WORKSPACE_SESSION_DATA_CONTRACT.md) | 工作区会话三表的数据边界 |
| [WORKSPACE_ISOLATION_CAPABILITIES](contracts/WORKSPACE_ISOLATION_CAPABILITIES.md) | 版本化多用户隔离能力契约 |
| [TRANSCRIPT_CONTRACT](contracts/TRANSCRIPT_CONTRACT.md) | 远程会话记录与重放的钉死契约 |

### security/

| 文档 | 说明 |
|------|------|
| [API_EXCEPTIONS](security/API_EXCEPTIONS.md) | API 安全基线例外登记与生命周期 |
| [BANDIT_BASELINE](security/BANDIT_BASELINE.md) | Bandit 基线维护指南 |
| [SSH_KEY_BOUNDARY](security/SSH_KEY_BOUNDARY.md) | SSH 密钥同步安全边界 |
| [SSH_SYNC_CONFIGURATION](security/SSH_SYNC_CONFIGURATION.md) | SSH 同步白名单与评审流程 |

### 按角色阅读路径

| 角色 | 路径 |
|------|------|
| 评估 Open ACE | INTRO → ARCHITECTURE → DEPLOYMENT |
| 部署 / 运维 | DEPLOYMENT → WORKSPACE_ISOLATION → ENV_REFERENCE → OPERATIONS → UPGRADING |
| 出了问题 | TROUBLESHOOTING → 链接到的组件指南 |
| API 集成 | API → PERMISSION_MODEL → SSO_CONFIG |
| 前端开发 | FRONTEND_GUIDE → DEVELOPMENT |
| 自主开发维护 | AUTONOMOUS_DEVELOPMENT → SANDBOX_BACKENDS → contracts/ |
| 管理远程机器 | REMOTE_WORKSPACE → REMOTE_AGENT |
| 维护者 | REPOSITORY_SETUP → TEST_LAYERS |

### 生成文档

- `dev/API_PERMISSION_MATRIX.md`——修改权限装饰器后运行
  `python3 scripts/generate_permission_matrix.py` 重新生成。
- `dev/API.md` 的表格由路由清单生成；新增或移动端点后，在文档跟上之前
  `check_docs_bilingual.py` 会失败。

### 文档站同步

对外发布的 Docusaurus 站点在独立仓库 `open-ace/open-ace-docs`。其
`scripts/sync-docs.js` 在构建时以 `OPEN_ACE_SOURCE_DIR` 指向本仓库，**按
`## English` / `## 中文` 锚点拆分每个双语文件**：英文半边发布在
`docs/<section>/`，中文半边发布在
`i18n/zh-Hans/docusaurus-plugin-content-docs/current/<section>/`，保留站点
的语言切换体验。`dev-notes/` 不上站。因为锚点就是切分点，它们是保留标题：
正式文档中每个锚点必须恰好出现一次。切分器还要求联合 H1（`# EN title — CN title`，
em dash 即页面标题的切分点）与导航行；三者均由 `check_docs_bilingual.py` 强制。
写入时它会把 `../images/` 链接改写为 `/img/`，把越出 `docs/` 的链接（dev-notes、
仓库文件）改写为 GitHub blob URL。
