# dev-notes — 开发过程文档

这里放**过程性**文档：修复复盘、CI 事故分析、实现总结、进度快照、交接说明、
按 issue 编号的方案稿。人写的和 agent 写的都放这里。

## 为什么有这个目录

2026-08-05 至 08-12 之间，自主开发流水线把 23 份这类文档（2,554 行）直接提交到了仓库根目录 ——
`CI_FIX_ROUND2.md`、`CI_FIX_FINAL_VERIFICATION.md`、`CI_MERGE_FIX_FINAL.md`、
`IMPLEMENTATION_SUMMARY_2327.md`、`FINAL_STATUS.md` 等等。它们互相引用，但仓库里没有任何
其他文件引用它们。后果是：根目录的 `.md` 从 5 个涨到 29 个，**外部访客点进仓库看到的第一屏
是 20 多份 CI 抢修记录，而不是产品说明** —— 而这恰好发生在项目第一次有外部使用者到访的窗口。

这些文档本身有价值，问题只在于位置。所以给它们一个正式的家。

## 约定

- **过程文档一律放这里**，不要放仓库根目录
- 建议命名：`<issue编号>-<短描述>.md`（如 `2437-flock-reclaim-plan.md`）
  或 `<日期>-<短描述>.md`（如 `2026-08-11-ci-lint-fix.md`）
- 同一件事不要开多份 `ROUND2` / `FINAL` / `FINAL_VERIFICATION` —— 更新同一个文件即可
- 属于长期项目文档（架构、运维手册、API 契约）的，放 `docs/` 下对应位置，不要放这里
- 与代码强相关的短说明，优先写成代码注释或 docstring，而不是单独开文件

## 根目录允许保留的 Markdown

`README.md` / `README_EN.md` / `CHANGELOG.md` / `CONTRIBUTING.md` / `CODE_OF_CONDUCT.md` /
`ROADMAP.md` / `SECURITY.md` / `GOVERNANCE.md` / `MAINTAINERS.md` / `AUTHORS.md` /
`LICENSE.md`，以及 AI 工具的指令文件（`CLAUDE.md` / `AGENTS.md` / `GEMINI.md` / `QWEN.md`）。

## 怎么强制的

三层，从松到紧：

1. `.gitignore` —— 根目录的 `CI_*.md`、`*_SUMMARY.md`、`FINAL_*.md` 等命名默认不会被 `git add` 带进来
2. `scripts/lint/check_root_docs.py` —— pre-commit 钩子，兜住 `git add -f`、重命名等绕过 .gitignore 的情况
3. `CLAUDE.md` —— 告诉 agent 该往哪写（流水线的 agent 会读它）

要新增一个根目录长期文档，把文件名加进 `scripts/lint/check_root_docs.py` 的 `ALLOWED`。
