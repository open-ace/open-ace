# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Curated summary of 795 commits since `v1.0.0`. Regenerate the full grouped list
> with `python3 scripts/generate_changelog.py --since v1.0.0`, or browse every
> commit via `git log v1.0.0..HEAD --oneline`.

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

[Unreleased]: https://github.com/open-ace/open-ace/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/open-ace/open-ace/releases/tag/v1.0.0
