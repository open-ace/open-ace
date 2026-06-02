# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No changes yet.

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
