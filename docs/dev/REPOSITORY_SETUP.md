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

Prepare a release on a branch, then merge the reviewed changes before tagging:

```bash
git switch -c release/vX.Y.Z origin/main
./scripts/release.sh --version X.Y.Z --dry-run
./scripts/release.sh --version X.Y.Z
python3 scripts/check_release_version.py --tag vX.Y.Z
# Commit the prepared files, open a PR, and merge it into main.
```

`pyproject.toml` is the product version source. The script updates it, the
frontend package and lockfile, and `CHANGELOG.md`. CI rejects mismatches. The
script does not commit, tag, or push, so the release change can be reviewed.

After the PR is merged, tag the merged main commit and create the GitHub Release:
```bash
git switch main
git pull --ff-only origin main
python3 scripts/check_release_version.py --tag vX.Y.Z
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "Open ACE vX.Y.Z" --notes-file release_notes.md --latest
```
The release workflows verify the tag, all product versions, and that the
tagged commit is reachable from main before publishing. Published tags are
immutable; correct historical metadata in release notes rather than retagging.

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
- Verify the Python package, frontend build, and image OCI version all report X.Y.Z.
- The Release workflow runs `scripts/run_extended_tests.py --category critical`
  before building and publishing release artifacts.
- Use `scripts/generate_changelog.py` to collect commits since last release:
  ```bash
  python3 scripts/generate_changelog.py --since v1.1.0
  ```
- Attach Docker/deployment notes and a short upgrade guide to each release.
- Keep `CHANGELOG.md` aligned with the latest release.
- Set `PYPI_PUBLISH=true` only after configuring PyPI Trusted Publishing for `open-ace-server`.
- Without `PYPI_PUBLISH=true`, the workflow still uploads GitHub release assets.

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

在发布分支准备版本变更，审查并合并后再打标签：

```bash
git switch -c release/vX.Y.Z origin/main
./scripts/release.sh --version X.Y.Z --dry-run
./scripts/release.sh --version X.Y.Z
python3 scripts/check_release_version.py --tag vX.Y.Z
# 提交准备好的文件，创建 PR 并合并到 main。
```

`pyproject.toml` 是产品版本号的唯一来源。脚本同步更新前端 package、锁文件和
`CHANGELOG.md`；CI 拒绝版本不一致。脚本不再自动提交、打标签或推送，以便通过 PR 审查。

PR 合并后，给已合并的 main 提交打标签，再创建 GitHub Release：
```bash
git switch main
git pull --ff-only origin main
python3 scripts/check_release_version.py --tag vX.Y.Z
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "Open ACE vX.Y.Z" --notes-file release_notes.md --latest
```
发布工作流会检查标签、各产品版本以及对应提交是否属于 main，再发布产物。已发布标签保持不变；
历史元数据差异通过发布说明勘误，不重写标签。

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
- 校验 Python 包、前端构建及镜像 OCI 版本均为 X.Y.Z。
- Release 工作流在构建并发布 release 制品之前，会先运行 `scripts/run_extended_tests.py --category critical`。
- 使用 `scripts/generate_changelog.py` 收集自上次发布以来的提交：
  ```bash
  python3 scripts/generate_changelog.py --since v1.1.0
  ```
- 为每个版本附上 Docker/部署说明以及简短的升级指南。
- 保持 `CHANGELOG.md` 与最新 release 一致。
- 配置 `open-ace-server` 的 PyPI Trusted Publishing 后，再设置 `PYPI_PUBLISH=true`。
- 未设置 `PYPI_PUBLISH=true` 时，工作流仍会上传 GitHub release 资产。

## 演示（Demo）

- 避免公开共享的管理员凭据。
- 在沙箱加固之前，优先使用可重置的沙箱账户、只读示例数据或简短的引导视频。
