# Repository Setup Checklist — 仓库配置清单

[English](#english) | [中文](#中文)

---

## English

Use this checklist for GitHub settings that cannot be fully configured from files in the repository.

## About Section

- Description: `Self-hosted enterprise AI workspace and governance platform`
- Website: `https://www.open-ace.com`
- Docs: `https://open-ace.github.io/open-ace-docs/docs/intro`
- Docs repository: `https://github.com/open-ace/open-ace-docs`
- Topics: `ai-governance`, `ai-workspace`, `enterprise-ai`, `llmops`, `flask`, `react`, `self-hosted`, `claude-code`, `qwen-code`, `codex`

## Community

- Enable Discussions.
- Pin a "Show and tell" or "Adoption stories" discussion.
- Create labels: `good first issue`, `help wanted`, `documentation`, `first-run`, `deployment`, `security`, `frontend`, `backend`.
- Keep 5-10 small issues labeled `good first issue`.

## Releases

### Release Process

Use `scripts/release.sh` to prepare and publish a new release:

```bash
# Preview changes (dry-run)
./scripts/release.sh --version 1.2.0 --dry-run

# Execute release
./scripts/release.sh --version 1.2.0
```

The script automates:
1. Validate version format (SemVer: X.Y.Z)
2. Update `pyproject.toml` version
3. Update `CHANGELOG.md` (move [Unreleased] to new version section)
4. Create git commit and tag
5. Push tag to origin

After tag is pushed, create GitHub Release with CHANGELOG content:
```bash
gh release create v1.2.0 --title "Open ACE v1.2.0" --notes-file release_notes.md --latest
```

### Release Cadence

| Version Type | Version Bump | Trigger Conditions | Frequency |
|--------------|-------------|-------------------|-----------|
| **Major** | 2.0.0 | Architecture refactor, breaking API changes | 1-2 per year |
| **Minor** | 1.1.0 → 1.2.0 | New features, significant improvements | Monthly (1st Tuesday) |
| **Patch** | 1.1.1 | Bug fixes, security patches | As needed (weekly or on-demand) |

**Patch Release Guidelines:**
- Accumulate ≥3 bug fixes before releasing patch
- Security fixes can be released immediately
- Wait ≥7 days between patch releases to avoid excessive frequency

### Release Checklist

- Publish releases from Git tags such as `v1.1.0`.
- The Release workflow runs `scripts/run_extended_tests.py --category critical`
  before building and publishing release artifacts.
- Use `scripts/generate_changelog.py` to collect commits since last release:
  ```bash
  python3 scripts/generate_changelog.py --since v1.1.0
  ```
- Attach Docker/deployment notes and a short upgrade guide to each release.
- Keep `CHANGELOG.md` aligned with the latest release.
- Configure `PYPI_API_TOKEN` only after confirming the intended PyPI project and package ownership.
- If `PYPI_API_TOKEN` is not configured, the release workflow skips PyPI publishing and still uploads GitHub release assets.

## Demo

- Avoid shared public administrator credentials.
- Prefer a resettable sandbox account, read-only sample data, or a short guided video until the sandbox is hardened.

---

## 中文

本清单用于配置那些无法完全通过仓库内文件完成的 GitHub 设置。

## About（仓库信息）区域

- Description（描述）：`Self-hosted enterprise AI workspace and governance platform`
- Website（网站）：`https://www.open-ace.com`
- Docs（文档）：`https://open-ace.github.io/open-ace-docs/docs/intro`
- Docs repository（文档仓库）：`https://github.com/open-ace/open-ace-docs`
- Topics（主题）：`ai-governance`、`ai-workspace`、`enterprise-ai`、`llmops`、`flask`、`react`、`self-hosted`、`claude-code`、`qwen-code`、`codex`

## 社区

- 启用 Discussions。
- 置顶一个 “Show and tell”（展示分享）或 “Adoption stories”（采用故事）讨论。
- 创建标签：`good first issue`、`help wanted`、`documentation`、`first-run`、`deployment`、`security`、`frontend`、`backend`。
- 保持 5-10 个标记为 `good first issue` 的小型 issue。

## 发布（Releases）

### 发布流程

使用 `scripts/release.sh` 准备并发布新版本：

```bash
# Preview changes (dry-run)
./scripts/release.sh --version 1.2.0 --dry-run

# Execute release
./scripts/release.sh --version 1.2.0
```

该脚本会自动完成：
1. 校验版本格式（SemVer：X.Y.Z）
2. 更新 `pyproject.toml` 版本
3. 更新 `CHANGELOG.md`（把 [Unreleased] 移入新版本小节）
4. 创建 git 提交和标签
5. 将标签推送到 origin

标签推送之后，使用 CHANGELOG 内容创建 GitHub Release：
```bash
gh release create v1.2.0 --title "Open ACE v1.2.0" --notes-file release_notes.md --latest
```

### 发布节奏

| 版本类型 | 版本升级 | 触发条件 | 频率 |
|--------------|-------------|-------------------|-----------|
| **Major（主版本）** | 2.0.0 | 架构重构、破坏性 API 变更 | 每年 1-2 次 |
| **Minor（次版本）** | 1.1.0 → 1.2.0 | 新功能、显著改进 | 每月一次（每月第一个周二） |
| **Patch（修订版）** | 1.1.1 | 缺陷修复、安全补丁 | 按需（每周或临时） |

**Patch 发布准则：**
- 累积 ≥3 个缺陷修复后再发布 patch
- 安全修复可以立即发布
- 两次 patch 发布之间间隔 ≥7 天，避免过于频繁

### 发布检查清单

- 从 Git 标签（例如 `v1.1.0`）发布版本。
- Release 工作流在构建并发布 release 制品之前，会先运行 `scripts/run_extended_tests.py --category critical`。
- 使用 `scripts/generate_changelog.py` 收集自上次发布以来的提交：
  ```bash
  python3 scripts/generate_changelog.py --since v1.1.0
  ```
- 为每个版本附上 Docker/部署说明以及简短的升级指南。
- 保持 `CHANGELOG.md` 与最新 release 一致。
- 仅在确认目标 PyPI 项目与包所有权之后，才配置 `PYPI_API_TOKEN`。
- 如果未配置 `PYPI_API_TOKEN`，release 工作流会跳过 PyPI 发布，但仍会上传 GitHub release 资产。

## 演示（Demo）

- 避免公开共享的管理员凭据。
- 在沙箱加固之前，优先使用可重置的沙箱账户、只读示例数据或简短的引导视频。
