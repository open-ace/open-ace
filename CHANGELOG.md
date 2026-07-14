# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- 管理-设置-模型网关配置页面深色模式对比度问题：状态提示框（"网关路由未启用"警告与"已启用"成功状态）文字与背景对比度不足，新增 `--color-{success,warning,danger,info}-{bg,text}` CSS 变量并在深色模式下使用低透明度语义背景 + 明亮文字色，确保 WCAG AA ≥ 4.5:1。

### Changed
- 代码审查提醒 Issue（每 3 周自动生成）现自带体检报告：workflow 内自动执行 Bandit 安全扫描、pip-audit 依赖漏洞、迁移单 head 校验、前端构建体积，以及 DB 死索引/大表/慢查询 SQL 清单，并在模板补齐"🛡️ 安全"维度；每项体检步骤 `continue-on-error`，单项工具故障不阻断 Issue 创建。
- `MessageRepository.get_user_conversation_samples` 由 per-session N+1 查询改为单次批量查询（`IN` 列表 + 应用层按 session 分组），保留逐 session `(agent_session_id = S OR conversation_id = S)` 语义与值域碰撞/双字段场景的等价性；批量失败时回退到原 per-session 循环。
- 移除安装/升级路径中的 `baseline_2026_06_23` 一次性 cutover 调用，新增 `scripts/check_min_revision.py` 作为最低 revision 护栏；明确最低支持升级起点为 `baseline_2026_06_23`，低于该 revision 的数据库需从已知健康备份恢复后再升级 (Issue #1215)。

## [v1.1.0] - 2026-07-02

### Added
- AI autonomous development workflow with retry, fork/cancel milestones, and PR-issue linkage (#716, #740, #886)
- Real-time agent activity streaming for autonomous workflows (Issue #771)
- Persisted remote-agent run provenance timeline with milestone TL;DR and code-change views (#988, #993)
- Batched GitHub issues and `auto_merge` for autonomous workflows
- AI GitHub account configuration for the autonomous agent (Issue #786)
- ZCode CLI support — single-shot + app-server persistent mode, settings sync, session list (#1074, #1134, #1142)
- Tenant user management and admin account creation (Issue #870) (#1238)
- User quota gate on autonomous workflows (#1265) and quota reference panel (#1230)
- Unified `session_messages.source` and transcript contract (#1125/#1128)
- Password reset with temporary password flow (Issue #865) (#1246)
- Frontend token management UI with rotate/revoke (Issue #885)
- Remote agent identity hardening with token-based auth (Issue #754)
- WebSocket relay for terminals on private networks
- Upgrade mode for existing deployments (#882)
- Date range filter on Dashboard summary
- Global custom context menu (Issue #643)

### Changed
- Schema single source of truth — authoritative `schema.sql` loaded directly (#1273, #1276)
- Baseline migration `baseline_2026_06_23` absorbs #1125/#1128 transcript columns
- Dashboard UI layout optimization and Card actions (#1085)
- Autonomous workflow timeline UI redesign and milestone compact cards (#1025, #1037)
- Shared workspace base-dir helper + macOS symlink blacklist fix (#1138)
- Removed unused management components and dead diagnostic scripts (Issue #877, #1242)

### Fixed
- 项目管理页面总项目数统计应包含智能分类 (Issue #1384)
- 移除默认项目分类初始化，使用智能分类替代 (Issue #1382)
- fork 视图中「查看最近里程碑」按钮点击无响应 (Issue #1375) (#1380)
- 添加 autonomous_workflows.retry_count 列 (Issue #1376)
- 为 Sync to Gitee workflow 添加重试机制 (Issue #1378) (#1379)
- WebUI session token validation based on instance alive status (Issue #1169) (#1374)
- 项目分类按工作区路径智能提取项目名 (Issue #1371) (#1373)
- 单用户模式下使用固定端口3100，不依赖config.json解析端口 (Issue #1357) (#1370)
- 单用户模式下 iframe URL 添加 token 参数 (#1369)
- fetch脚本不覆盖已存在的DATABASE_URL环境变量 (Issue #1362) (#1363)
- WebUI进程使用请求中的IP连接主服务 (Issue #1357) (#1358)
- 编辑用户时同步 tenant_id 到后端 API (Issue #1359)
- PostgreSQL 创建租户时 boolean 类型兼容性修复 (#1353)
- 使用 CSS 变量替换骨架屏和终端硬编码颜色 (Issue #1334) (#1352)
- 修复页面刷新时竞态条件导致的错误提示 (Issue #1347) (#1349)
- 统一管理页面刷新按钮样式 (Issue #1337) (#1348)
- 修正 refreshKey 配置使刷新按钮生效 (Issue #1335) (#1336)
- 修复 PostgreSQL 下创建租户时的 cursor 类型错误 (Issue #1341) (#1345)
- 为必填字段添加红色星号标识 (Issue #1340) (#1342)
- Insights 报告使用 API Key 的 cli_settings 配置模型 (Issue #1171) (#1216)
- 隐藏空下拉菜单避免点击无内容 (#1332)
- 修复项目管理页面中文显示英文问题 (#1326)
- auto-detect SERVER_IP to fix iframe loading failure (Issue #1306) (#1307)
- Recognize post-baseline revisions so head DBs upgrade cleanly; widen legacy `alembic_version.version_num` to VARCHAR(64) (#1281, #1282)
- Work-page quota reads `agent_sessions` only; gray-style CLI sessions (#1272)
- Auto-provision users PostgreSQL type fix (Issue #1261) (#1266)
- Package multi-user mode `useradd` permission and `WORKSPACE_BASE_DIR=/home` (Issue #1217, #1262)
- `config.json` 600 permissions to protect sensitive info (Issue #1252) (#1258)
- Schema detection for existing databases (Issue #1095) (#1254)
- SSO provider registration passes `tenant_id` (Issue #1247) (#1251)
- Upstream quota-exceeded alert in workspace (Issue #1060) (#1229)
- Deterministic score for regenerated insights reports (Issue #685) (#1227)
- Hide 2FA toggle as feature not implemented (Issue #862) (#1236)
- Workspace fetch-source fallback + `remote_sync` message_count (#1221)

## [1.0.0] - 2026-06-02

### Added
- **Self-hosted AI workspace**: Work Mode for AI sessions, prompt reuse, conversation history, and project context.
- **Remote Workspace and Remote Agent**: Run AI CLIs on development, staging, or GPU machines through a browser-managed remote agent.
- **Multi-CLI support**: Claude Code, Qwen Code, Codex, and OpenClaw adapters with session recovery, permission modes, and history sync.
- **API Key proxy**: Encrypt LLM API keys on the server and issue short-lived proxy tokens to local and remote sessions.
- **Browser development loop**: Remote terminal, directory browsing, and VSCode/code-server proxy support.
- **AI governance dashboards**: Token usage, cost analysis, quotas, alerts, anomaly detection, audit trails, and ROI visibility.
- **Compliance reporting**: Governance reports with JSON/CSV export paths and practical risk recommendations.
- **Enterprise administration**: Multi-tenant access control, roles, SSO-ready permission model, and Feishu/DingTalk integration paths.
- **Deployment options**: Docker Compose quick start, production deployment docs, Kubernetes reference, and reverse proxy guidance.
- **Open source readiness**: Apache 2.0 license, contributing guide, code of conduct, security policy, public roadmap, issue templates, CODEOWNERS, Dependabot, and beginner-friendly issue labels.
- **Bilingual documentation**: Chinese and English docs for architecture, deployment, development, API usage, integrations, Remote Workspace, Remote Agent, Kubernetes, and repository setup.

### Architecture
- Flask backend with SQLAlchemy repositories and Alembic migrations.
- React 18, TypeScript, Vite, Bootstrap 5, and Chart.js frontend.
- PostgreSQL and SQLite support for development and deployment workflows.
- Remote agent package with CLI adapters, WebSocket terminal support, and session synchronization.

### Deployment
- Docker Compose quick start with PostgreSQL.
- Source installation path for local development.
- Kubernetes deployment reference.
- Linux systemd and macOS launchd support for remote agents.

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 1.0.0 | 2026-06-02 | Initial public release of the self-hosted AI workspace and governance platform |

---

[v1.1.0]: https://github.com/open-ace/open-ace/releases/tag/v1.1.0
[Unreleased]: https://github.com/open-ace/open-ace/compare/v1.1.0...HEAD
[1.0.0]: https://github.com/open-ace/open-ace/releases/tag/v1.0.0
