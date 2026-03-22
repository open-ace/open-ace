# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial open source release preparation
- Apache 2.0 License
- Contributing guidelines
- Comprehensive documentation

## [1.0.0] - 2026-03-21

### Added
- **Multi-tool Support**: Track token usage for Claude, Qwen, and OpenClaw
- **Web Dashboard**: Interactive visualization with Chart.js
  - Summary page with usage statistics
  - Messages page with filtering and search
  - Analysis page with trends and heatmaps
  - Conversation history viewer
- **CLI Tool**: Command-line interface for quick queries
  - `today` - View today's usage
  - `top` - View last 7 days usage
  - `summary` - View total summary
  - `report` - Generate email report
- **Data Collection**: Automated scripts for log parsing
  - `fetch_claude.py` - Claude log collector
  - `fetch_qwen.py` - Qwen log collector
  - `fetch_openclaw.py` - OpenClaw log collector
- **Email Reports**: Automated daily usage reports
- **Feishu Integration**: User and group name resolution
- **Authentication**: User management with role-based access
- **Internationalization**: English and Chinese language support

### Architecture
- SQLite database for data storage
- Flask web framework
- Bootstrap-based responsive UI
- RESTful API design

### Deployment
- Support for local and remote deployment
- systemd service configuration for Linux
- launchd configuration for macOS
- Docker support (planned)

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 1.0.0 | 2026-03-21 | Initial open source release |

---

[Unreleased]: https://github.com/your-org/open-ace/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/your-org/open-ace/releases/tag/v1.0.0