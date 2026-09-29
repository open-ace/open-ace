# Bandit Baseline Maintenance Guide — Bandit Baseline 维护指南

[English](#english) | [中文](#中文)

---

## English

This document explains how to maintain the `scripts/lint/bandit_baseline.json` file.

### Overview

The Bandit baseline is used to exempt known low-severity security findings so that they do not block PRs. This file follows the **severity + confidence tiering policy**:

| Severity | Confidence | Handling |
|--------|--------|----------|
| HIGH | HIGH/MEDIUM/LOW | Blocks the PR (cannot be exempted) |
| MEDIUM | Any | Warns but does not block |
| LOW | Any | Can be exempted into the baseline |

### Baseline File Structure

```json
{
  "generated_at": "2026-07-19",
  "version": "1.0.0",
  "description": "Bandit baseline for known low-severity findings.",
  "findings": [
    {
      "test_id": "B101",
      "severity": "LOW",
      "confidence": "HIGH",
      "file": "tests/test_example.py",
      "line": 42,
      "reason": "assert usage in test file is expected",
      "approved_by": "security-team",
      "approved_at": "2026-07-19"
    }
  ]
}
```

#### Field Reference

| Field | Required | Description |
|------|------|------|
| `test_id` | ✅ | Bandit test ID (e.g., B101) |
| `severity` | ✅ | Finding severity (only LOW can be exempted) |
| `confidence` | ✅ | Bandit confidence |
| `file` | ✅ | File path (relative to the repository root) |
| `line` | ✅ | Line number |
| `reason` | ✅ | Explanation of the exemption |
| `approved_by` | ✅ | Approver (security-team or admin) |
| `approved_at` | ✅ | Approval date |

### Process for Adding a New Baseline Finding

#### 1. Evaluate the Finding

First confirm that the finding should not be fixed:

- **Must be exempted**: only LOW severity findings
- **Must not be exempted**: HIGH severity findings; the code must be fixed

#### 2. Explain in the PR

In the PR that adds the baseline finding:

1. Explain why the finding can be exempted
2. Reference related issues (if any)
3. State whether a fix is planned

#### 3. Obtain Approval

- The PR can be merged only after approval by the **Security Team** or an **Admin**
- The approver is recorded in the `approved_by` field

#### 4. Update the Baseline File

```bash
# Manually add the finding to the baseline
# Or generate it with a script (to be implemented)
```

### Review & Bypass

Quarterly review of baseline findings and emergency bypass via the `skip-security-check` PR label follow the shared exception lifecycle defined in [API_EXCEPTIONS.md](./API_EXCEPTIONS.md#exception-lifecycle).

### Common LOW Severity Findings

| Test ID | Name | Common Scenario |
|---------|------|----------|
| B101 | assert_used | assert statements in test files |
| B311 | random_module | Randomness for non-cryptographic purposes |

### References

- [Bandit documentation](https://bandit.readthedocs.io/)
- [Bandit Test IDs](https://bandit.readthedocs.io/en/latest/plugins/index.html)
- [Issue #1856](https://github.com/open-ace/open-ace/issues/1856) - CI quality gate improvements

---

## 中文

本文档说明如何维护 `scripts/lint/bandit_baseline.json` 文件。

### 概述

Bandit baseline 用于豁免已知的低严重性安全 findings，避免它们阻断 PR。本文件遵循 **severity + confidence 分层策略**：

| 严重性 | 置信度 | 处理方式 |
|--------|--------|----------|
| HIGH | HIGH/MEDIUM/LOW | 阻断 PR（不可豁免） |
| MEDIUM | 任意 | 警告但不阻断 |
| LOW | 任意 | 可豁免到 baseline |

### Baseline 文件结构

```json
{
  "generated_at": "2026-07-19",
  "version": "1.0.0",
  "description": "Bandit baseline for known low-severity findings.",
  "findings": [
    {
      "test_id": "B101",
      "severity": "LOW",
      "confidence": "HIGH",
      "file": "tests/test_example.py",
      "line": 42,
      "reason": "assert usage in test file is expected",
      "approved_by": "security-team",
      "approved_at": "2026-07-19"
    }
  ]
}
```

#### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `test_id` | ✅ | Bandit test ID（如 B101） |
| `severity` | ✅ | Finding 严重性（仅 LOW 可豁免） |
| `confidence` | ✅ | Bandit 置信度 |
| `file` | ✅ | 文件路径（相对于仓库根目录） |
| `line` | ✅ | 行号 |
| `reason` | ✅ | 豁免原因说明 |
| `approved_by` | ✅ | 审批人（security-team 或 admin） |
| `approved_at` | ✅ | 审批日期 |

### 添加新 Baseline Finding 的流程

#### 1. 评估 Finding

首先确认 finding 不应修复：

- **必须豁免**：仅 LOW severity findings
- **禁止豁免**：HIGH severity findings，必须修复代码

#### 2. 在 PR 中说明

在添加 baseline finding 的 PR 中：

1. 说明为什么该 finding 可以豁免
2. 引用相关 Issue（如有）
3. 说明是否有计划修复

#### 3. 获取审批

- **Security Team** 或 **Admin** 审批后方可合并
- 审批人在 `approved_by` 字段记录

#### 4. 更新 Baseline 文件

```bash
# 手动添加 finding 到 baseline
# 或运行脚本生成（待实现）
```

### 审查与绕过

baseline findings 的季度审查与通过 `skip-security-check` 标签紧急绕过 PR 检查，统一遵循 [API_EXCEPTIONS.md](./API_EXCEPTIONS.md#例外生命周期) 中的例外生命周期。

### 常见 LOW Severity Findings

| Test ID | 名称 | 常见场景 |
|---------|------|----------|
| B101 | assert_used | 测试文件中的 assert 语句 |
| B311 | random_module | 非加密用途的随机数 |

### 参考资料

- [Bandit 文档](https://bandit.readthedocs.io/)
- [Bandit Test IDs](https://bandit.readthedocs.io/en/latest/plugins/index.html)
- [Issue #1856](https://github.com/open-ace/open-ace/issues/1856) - CI 质量门改进
