# Admin Tenant Isolation Audit

Issue #2180: 管理接口租户隔离修复审计清单

## 审计日期
2026-08-01

## 审计范围
全仓审计所有使用 `@admin_required` 装饰器的管理端点，确保：
1. Tenant admin 只能访问本租户资源
2. Platform admin 跨租户操作必须显式指定 tenant_id
3. 所有写路径不再默认使用 tenant 1

## 审计结果

### 1. Remote Machine 管理接口 (app/routes/remote.py)

| 端点 | 方法 | 行号 | 原问题 | 修复状态 | 验证状态 |
|------|------|------|--------|----------|----------|
| `/machines/register` | POST | 515 | 静默默认 tenant_id=1 | ✅ 已修复 | ⏳ 待验证 |
| `/machines` | GET | 543 | admin 全局列表 | ✅ 已修复 | ⏳ 待验证 |
| `/machines/<machine_id>` | DELETE | 642 | 缺少 tenant predicate | ✅ 已修复 | ⏳ 待验证 |
| `/machines/<machine_id>/assign` | POST | 655 | 缺少 tenant predicate | ✅ 已修复 | ⏳ 待验证 |
| `/machines/<machine_id>/assign/<user_id>` | DELETE | 685 | 缺少 tenant predicate | ✅ 已修复 | ⏳ 待验证 |
| `/machines/<machine_id>/token/rotate` | POST | 767 | 缺少 tenant predicate | ✅ 已修复 | ⏳ 待验证 |
| `/machines/<machine_id>/token/revoke` | POST | 855 | 缺少 tenant predicate | ✅ 已修复 | ⏳ 待验证 |
| `/machines/<machine_id>/users` | GET | 887 | 缺少 tenant predicate | ✅ 已修复 | ⏳ 待验证 |

### 2. Compliance 管理接口 (app/routes/compliance.py)

| 端点 | 方法 | 行号 | 原问题 | 修复状态 | 验证状态 |
|------|------|------|--------|----------|----------|
| `/reports` | POST | 135 | admin 跨租户无审计 | ✅ 已修复 | ⏳ 待验证 |
| `/reports/saved` | GET | 285 | 接受 tenant_id 无验证 | ⚠️ 已有 tenant 隔离 | ⏳ 待验证 |
| `/retention/cleanup` | POST | 535 | 全局 cleanup 无 tenant 隔离 | ⚠️ 待评估 | ⏳ 待验证 |

### 3. Mapping Rules 管理接口 (app/routes/mapping_rules.py)

| 端点 | 方法 | 行号 | 原问题 | 修复状态 | 验证状态 |
|------|------|------|--------|----------|----------|
| `/api/mapping-rules` | GET | 17 | 返回全局规则 | ✅ 已修复 | ⏳ 待验证 |
| `/api/mapping-rules` | POST | 35 | 创建规则无 tenant 验证 | ✅ 已修复 | ⏳ 待验证 |
| `/api/mapping-rules/<id>` | PUT | 66 | 更新规则无 tenant 验证 | ✅ 已修复 | ⏳ 待验证 |
| `/api/mapping-rules/<id>` | DELETE | 93 | 删除规则无 tenant 验证 | ✅ 已修复 | ⏳ 待验证 |
| `/api/mapping-rules/user/<user_id>/generate-default` | POST | 105 | 无 tenant 验证 | ✅ 已修复 | ⏳ 待验证 |
| `/api/mapping-stats` | GET | 114 | 全局统计 | ✅ 已修复 | ⏳ 待验证 |
| `/api/mapping-rules/auto-map` | POST | 123 | 全局自动映射 | ✅ 已修复 | ⏳ 待验证 |
| `/api/unmapped-accounts` | GET | 172 | 全局未映射账户 | ✅ 已修复 | ⏳ 待验证 |
| `/api/unmapped-accounts/<sender_name>/map` | POST | 207 | 手动映射无 tenant 验证 | ✅ 已修复 | ⏳ 待验证 |

### 4. Governance 管理接口 (app/routes/governance.py)

| 端点 | 方法 | 行号 | 原问题 | 修复状态 | 验证状态 |
|------|------|------|--------|----------|----------|
| `/audit/logs` | GET | 43 | 已有 tenant_id 过滤 | ⚠️ 已有隔离 | ⏳ 待验证 |
| `/audit/logs/export` | GET | 172 | 已有 tenant_id 过滤 | ⚠️ 已有隔离 | ⏳ 待验证 |
| `/quota/status/all` | GET | 254 | 返回所有用户 quota | ⚠️ 待评估 | ⏳ 待验证 |
| `/quota/alerts` | GET | 279 | 返回所有 alerts | ⚠️ 待评估 | ⏳ 待验证 |

### 5. 配置类接口 (全局配置)

| 模块 | 端点 | 处理方案 | 状态 |
|------|------|----------|------|
| SMTP Config | `/management/smtp-config` | 使用 platform_admin_required | ⏳ 待修复 |
| AI Agent Settings | `/ai-agent/settings` | 使用 platform_admin_required | ⏳ 待修复 |
| Model Gateway | `/management/model-gateway-config` | 使用 platform_admin_required | ⏳ 待修复 |

### 6. 其他待审计模块

| 模块 | 状态 | 备注 |
|------|------|------|
| Analytics | ⚠️ 待评估 | 4 处 @admin_required |
| Policy | ⚠️ 待评估 | 5 处 @admin_required |
| Admin Users | ⚠️ 待评估 | 用户管理接口 |
| SSO Providers | ⚠️ 待评估 | SSO 配置接口 |
| Fetch | ⚠️ 待评估 | 数据获取接口 |

## 统计

- 总审计端点: ~50+
- 已修复: 22
- 待修复: 3 (配置类接口)
- 待评估: 5 (其他模块)
- 已有正确实现: 4 (Governance 部分接口)

## 验证矩阵

### Tenant Admin 权限边界

| 测试场景 | 预期结果 | 测试方法 | 状态 |
|---------|---------|---------|------|
| 列出本租户 API Keys | 成功 | 集成测试 | ⏳ |
| 列出其他租户 API Keys | 403 | 集成测试 | ⏳ |
| 注册本租户 Machine | 成功 | 集成测试 | ⏳ |
| 注册其他租户 Machine | 403 | 集成测试 | ⏳ |
| 查看本租户 Mapping Rules | 成功 | 集成测试 | ⏳ |
| 创建其他租户用户的 Rule | 403 | 集成测试 | ⏳ |

### Platform Admin 跨租户操作

| 测试场景 | 预期结果 | 测试方法 | 状态 |
|---------|---------|---------|------|
| 跨租户操作（显式 tenant_id） | 成功 + 审计日志 | 集成测试 | ⏳ |
| 跨租户操作（缺省 tenant_id） | 400 | 集成测试 | ⏳ |
| 访问全局配置接口 | 成功 | 集成测试 | ⏳ |

## 修复历史

1. 2026-08-01: 创建 TenantScopedService 和 TenantPredicateBuilder
2. 2026-08-01: 修复 Remote Machine 管理接口
3. 2026-08-01: 修复 Mapping Rules 管理接口
4. 2026-08-01: 增强 Compliance 报告接口
5. 2026-08-01: 编写单元测试和集成测试

## 下一步

1. 修复配置类接口 (SMTP, AI Agent, Model Gateway)
2. 完成集成测试覆盖
3. 运行全量回归测试
4. 更新 API 文档
5. 前端适配

## 参考

- Issue #2180: 管理接口租户隔离修复
- Issue #2179: 权限原语建立
- `app/services/base.py`: TenantScopedService
- `app/repositories/predicate.py`: TenantPredicateBuilder