# API Security Exceptions Management — API 安全例外管理

[English](#english) | [中文](#中文)

---

## English

**Issue**: #1897

## Overview

This document describes the process for managing API security baseline suppressions and exceptions in the Open ACE project.

## Security Scanner

The API security scanner (`scripts/lint/api_security_scanner.py`) detects security violations in Flask routes:

- **SEC001**: Route handler has no authentication
- **SEC002**: Route with ID parameter missing ownership check
- **SEC003**: Blueprint missing `@before_request` auth hook

## Baseline File

Security suppressions are stored in `scripts/lint/security_baseline.json`. Current baseline entries: 0 as of 2026-09-29 (`scripts/lint/security_baseline.json`). Each suppression must have complete metadata:

```json
{
  "key": "SEC002|app/routes/workspace.py|/api/workspace/knowledge/<entry_id>",
  "rule": "SEC002",
  "file": "app/routes/workspace.py",
  "line": 2038,
  "endpoint": "/api/workspace/knowledge/<entry_id>",
  "message": "Route ... has ID param(s) ['entry_id'] but no ownership check",
  "metadata": {
    "owner": "@team-backend",
    "justification": "Reason why this suppression is necessary",
    "reviewed_at": "2026-07-23",
    "expires_at": "2027-01-23T00:00:00Z",
    "risk_level": "low",
    "test_coverage": "tests/integration/routes/test_example.py::test_example",
    "alternative_controls": [
      "List of alternative security controls in place"
    ]
  }
}
```

### Required Metadata Fields

- **owner**: GitHub username or team responsible for this suppression
- **justification**: Clear explanation of why the exception is necessary
- **test_coverage**: Test file and test name that validates the security control

### Optional Metadata Fields

- **reviewed_at**: Date of last review (ISO format)
- **expires_at**: Expiration date (ISO format) - CI will fail if expired
- **risk_level**: Risk assessment (low/medium/high)
- **alternative_controls**: List of security controls in place
- **automated_check**: Metadata about automated checks

## Adding a New Suppression

### Step 1: Document the Exception

Before adding a suppression, ensure:

1. You have a valid reason for the exception
2. Alternative security controls are in place (e.g., workspace-level access control)
3. Test coverage exists or will be added

### Step 2: Use `@security_annotated` Decorator

For endpoints with ownership checks implemented in non-standard patterns:

```python
from app.auth.decorators import security_annotated

@workspace_bp.route("/resource/<int:resource_id>", methods=["GET"])
@security_annotated(reason="Ownership via get_user_resource + permission check")
def get_resource(resource_id):
    # Ownership check implemented inline
    resource = get_user_resource(user_id, resource_id)
    if not resource:
        return jsonify({"error": "Access denied"}), 403
    ...
```

### Step 3: Update Baseline

If the scanner still flags the endpoint after adding `@security_annotated`:

```bash
python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
```

### Step 4: Add Metadata

Edit `scripts/lint/security_baseline.json` and add complete metadata for the new suppression.

### Step 5: Validate

Run metadata validation:

```bash
python scripts/lint/validate_baseline_metadata.py
```

## Removing a Suppression

Suppressions should be removed when:

1. Code is fixed and security issue is resolved
2. Endpoint is removed
3. Alternative security controls are no longer necessary

### Step 1: Fix the Issue

Implement proper security controls or remove the endpoint.

### Step 2: Regenerate Baseline

```bash
python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
```

### Step 3: Validate

```bash
python scripts/lint/api_security_scanner.py
python scripts/lint/validate_baseline_metadata.py
```

## CI Integration

The security scanner runs in CI for every pull request:

1. **API Security Scanner**: Detects new violations not in baseline
2. **Baseline Diff Check**: Detects changes to baseline (requires justification)
3. **Metadata Validation**: Ensures all suppressions have complete metadata

### Bypassing Security Checks

In emergencies, use the `skip-security-check` label on PRs:

```bash
gh pr edit --add-label skip-security-check
```

⚠️ **Warning**: This should only be used in emergencies and requires review by security team.

## Quarterly Audit

Baseline suppressions are audited quarterly:

1. Check `expires_at` dates - CI will fail if expired
2. Review `owner` and `justification` - ensure they're still accurate
3. Verify `test_coverage` - ensure tests still pass
4. Assess `alternative_controls` - ensure they're still effective

Audit reminders are currently manual — no scheduled workflow audits these suppressions yet (the quarterly `false-positive-review.yml` workflow covers test annotations, not security exceptions).

## Feature Flags

Some security features can be enabled gradually using environment variables:

### ENFORCE_PROMPT_OWNERSHIP

Controls prompt template ownership enforcement:

- `true` (default): Enforce ownership checks
- `false`: Log only, do not reject requests (for gradual rollout)

The one-time gradual rollout plan (phasing, rollback procedure, success criteria) has been moved to [docs/dev-notes/2026-09-29-enforce-prompt-owership-rollout.md](../dev-notes/2026-09-29-enforce-prompt-owership-rollout.md).

## Security Review Checklist

When reviewing PRs with baseline changes:

- [ ] All new suppressions have complete metadata
- [ ] `owner` is a valid GitHub user/team
- [ ] `justification` clearly explains the exception
- [ ] `test_coverage` exists and tests the security control
- [ ] `expires_at` is set and not too far in the future
- [ ] `alternative_controls` are documented and effective
- [ ] PR description explains why the exception is necessary

## Related Documentation

- [Issue #1897](https://github.com/open-ace/open-ace/issues/1897): API Security Baseline Cleanup
- [API Security Scanner](../../scripts/lint/api_security_scanner.py)
- [Baseline Metadata Validator](../../scripts/lint/validate_baseline_metadata.py)
- [Baseline Diff Checker](../../scripts/lint/baseline_diff.py)

## Contact

For questions about API security exceptions:

- Security team: @security
- Backend team: @team-backend

---

## 中文

**Issue**：#1897

## 概述

本文档描述 Open ACE 项目中管理 API 安全基线抑制（suppression）与例外的流程。

## 安全扫描器

API 安全扫描器（`scripts/lint/api_security_scanner.py`）检测 Flask 路由中的安全违规：

- **SEC001**：路由处理函数没有认证
- **SEC002**：带 ID 参数的路由缺少所有权检查
- **SEC003**：Blueprint 缺少 `@before_request` 认证钩子

## 基线文件

安全抑制存放在 `scripts/lint/security_baseline.json`。当前基线条目：截至 2026-09-29 为 0 条（`scripts/lint/security_baseline.json`）。每条抑制都必须有完整的元数据：

```json
{
  "key": "SEC002|app/routes/workspace.py|/api/workspace/knowledge/<entry_id>",
  "rule": "SEC002",
  "file": "app/routes/workspace.py",
  "line": 2038,
  "endpoint": "/api/workspace/knowledge/<entry_id>",
  "message": "Route ... has ID param(s) ['entry_id'] but no ownership check",
  "metadata": {
    "owner": "@team-backend",
    "justification": "Reason why this suppression is necessary",
    "reviewed_at": "2026-07-23",
    "expires_at": "2027-01-23T00:00:00Z",
    "risk_level": "low",
    "test_coverage": "tests/integration/routes/test_example.py::test_example",
    "alternative_controls": [
      "List of alternative security controls in place"
    ]
  }
}
```

### 必填元数据字段

- **owner**：负责该抑制的 GitHub 用户名或团队
- **justification**：清楚说明为什么需要这条例外
- **test_coverage**：验证该安全控制的测试文件与测试名

### 可选元数据字段

- **reviewed_at**：上次审查日期（ISO 格式）
- **expires_at**：过期日期（ISO 格式）——过期后 CI 会失败
- **risk_level**：风险评估（low/medium/high）
- **alternative_controls**：已有安全控制的列表
- **automated_check**：关于自动化检查的元数据

## 新增一条抑制

### 步骤 1：登记例外

新增抑制之前，先确认：

1. 你有设立该例外的正当理由
2. 已有替代安全控制（例如工作区级访问控制）
3. 已有或即将补充测试覆盖

### 步骤 2：使用 `@security_annotated` 装饰器

对于以非标准模式实现所有权检查的端点：

```python
from app.auth.decorators import security_annotated

@workspace_bp.route("/resource/<int:resource_id>", methods=["GET"])
@security_annotated(reason="Ownership via get_user_resource + permission check")
def get_resource(resource_id):
    # Ownership check implemented inline
    resource = get_user_resource(user_id, resource_id)
    if not resource:
        return jsonify({"error": "Access denied"}), 403
    ...
```

### 步骤 3：更新基线

如果添加 `@security_annotated` 后扫描器仍标记该端点：

```bash
python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
```

### 步骤 4：补充元数据

编辑 `scripts/lint/security_baseline.json`，为新增抑制补齐完整元数据。

### 步骤 5：校验

运行元数据校验：

```bash
python scripts/lint/validate_baseline_metadata.py
```

## 移除一条抑制

出现以下情况时应移除抑制：

1. 代码已修复、安全问题已解决
2. 端点已被移除
3. 替代安全控制已不再必要

### 步骤 1：修复问题

实现适当的安全控制，或移除该端点。

### 步骤 2：重新生成基线

```bash
python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
```

### 步骤 3：校验

```bash
python scripts/lint/api_security_scanner.py
python scripts/lint/validate_baseline_metadata.py
```

## CI 集成

安全扫描器在 CI 中对每个 pull request 运行：

1. **API Security Scanner**：检测不在基线中的新违规
2. **Baseline Diff Check**：检测基线变更（需要说明理由）
3. **Metadata Validation**：确保所有抑制都有完整元数据

### 绕过安全检查

紧急情况下，可在 PR 上使用 `skip-security-check` 标签：

```bash
gh pr edit --add-label skip-security-check
```

⚠️ **警告**：仅限紧急情况使用，且需要安全团队审查。

## 季度审计

基线抑制按季度审计：

1. 检查 `expires_at` 日期——过期后 CI 会失败
2. 复核 `owner` 与 `justification`——确认仍然准确
3. 验证 `test_coverage`——确保测试仍然通过
4. 评估 `alternative_controls`——确认仍然有效

审计提醒目前靠人工——还没有定时 workflow 审计这些抑制（季度性的 `false-positive-review.yml` workflow 覆盖的是测试注解，不是安全例外）。

## 功能开关

部分安全特性可以通过环境变量逐步启用：

### ENFORCE_PROMPT_OWNERSHIP

控制提示词模板所有权校验：

- `true`（默认）：强制执行所有权检查
- `false`：仅记录日志，不拒绝请求（用于灰度发布）

一次性灰度方案（阶段划分、回滚步骤、成功标准）已迁至 [docs/dev-notes/2026-09-29-enforce-prompt-owership-rollout.md](../dev-notes/2026-09-29-enforce-prompt-owership-rollout.md)。

## 安全审查清单

审查带基线变更的 PR 时：

- [ ] 所有新增抑制都有完整元数据
- [ ] `owner` 是有效的 GitHub 用户/团队
- [ ] `justification` 清楚解释了该例外
- [ ] `test_coverage` 存在且测试的是该安全控制
- [ ] `expires_at` 已设置且不过分久远
- [ ] `alternative_controls` 已记录且有效
- [ ] PR 描述说明了为什么需要该例外

## 相关文档

- [Issue #1897](https://github.com/open-ace/open-ace/issues/1897)：API Security Baseline Cleanup
- [API Security Scanner](../../scripts/lint/api_security_scanner.py)
- [Baseline Metadata Validator](../../scripts/lint/validate_baseline_metadata.py)
- [Baseline Diff Checker](../../scripts/lint/baseline_diff.py)

## 联系方式

关于 API 安全例外的问题：

- 安全团队：@security
- 后端团队：@team-backend
