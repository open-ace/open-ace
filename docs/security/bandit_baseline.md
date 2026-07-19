# Bandit Baseline 维护指南

本文档说明如何维护 `scripts/lint/bandit_baseline.json` 文件。

## 概述

Bandit baseline 用于豁免已知的低严重性安全 findings，避免它们阻断 PR。本文件遵循 **severity + confidence 分层策略**：

| 严重性 | 置信度 | 处理方式 |
|--------|--------|----------|
| HIGH | HIGH/MEDIUM/LOW | 阻断 PR（不可豁免） |
| MEDIUM | 任意 | 警告但不阻断 |
| LOW | 任意 | 可豁免到 baseline |

## Baseline 文件结构

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

### 字段说明

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

## 添加新 Baseline Finding 的流程

### 1. 评估 Finding

首先确认 finding 不应修复：

- **必须豁免**：仅 LOW severity findings
- **禁止豁免**：HIGH severity findings，必须修复代码

### 2. 在 PR 中说明

在添加 baseline finding 的 PR 中：

1. 说明为什么该 finding 可以豁免
2. 引用相关 Issue（如有）
3. 说明是否有计划修复

### 3. 获取审批

- **Security Team** 或 **Admin** 审批后方可合并
- 审批人在 `approved_by` 字段记录

### 4. 更新 Baseline 文件

```bash
# 手动添加 finding 到 baseline
# 或运行脚本生成（待实现）
```

## 季度审查机制

每季度（1/4/7/10 月）自动生成 baseline 审查 Issue：

1. Security Team 审查所有 baseline findings
2. 确认每个 finding 是否仍需豁免
3. 移除已修复或不再适用的 findings
4. 更新 `approved_at` 日期

## 紧急绕过

如果需要在 PR 中临时绕过 Bandit 检查：

1. 添加 `skip-security-check` 标签到 PR
2. 在 PR 描述中说明绕过原因
3. 创建后续 Issue 跟踪需要修复的问题
4. Admin 审批后方可合并

> ⚠️ **警告**：`skip-security-check` 仅用于紧急情况，滥用将被记录审查。

## 常见 LOW Severity Findings

| Test ID | 名称 | 常见场景 |
|---------|------|----------|
| B101 | assert_used | 测试文件中的 assert 语句 |
| B311 | random_module | 非加密用途的随机数 |

## 参考资料

- [Bandit 文档](https://bandit.readthedocs.io/)
- [Bandit Test IDs](https://bandit.readthedocs.io/en/latest/plugins/index.html)
- [Issue #1856](https://github.com/open-ace/open-ace/issues/1856) - CI 质量门改进