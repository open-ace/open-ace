# SSO 设置迁移指南

## Issue #2128: SSO 全局开关改进

## 背景

在 Issue #2128 的修复中，SSO 启用开关被明确为全局系统设置。本文档说明如何从旧的租户级设置迁移到新的全局设置。

## 变更说明

### 架构变更

| 方面 | 旧实现 | 新实现 |
|------|--------|--------|
| SSO 开关位置 | `TenantSettings.sso_enabled` | `config.json → system_settings.sso_enabled` |
| 控制范围 | 租户级别（概念上） | 全局（影响所有租户的登录页面） |
| API | `/api/tenants/{id}/settings` | `/api/system/settings` |

### 当前设计

```
全局开关 (system_settings.sso_enabled)
    │
    ├─ 禁用 → 登录页不显示任何 SSO 选项
    │
    └─ 启用 → 登录页显示所有已启用的 Provider
                    │
                    ├─ Provider A (tenant_id=1) → 用户选择后归属租户 1
                    ├─ Provider B (tenant_id=2) → 用户选择后归属租户 2
                    └─ Provider C (tenant_id=1) → 同一租户可有多个 Provider
```

## 迁移步骤

### 1. 检查现有配置

如果您之前在租户设置中配置了 `sso_enabled: true`：

```bash
# 查看租户设置
curl -X GET "http://localhost:5000/api/tenants/1" -H "Authorization: Bearer <token>"
```

### 2. 迁移到全局设置

使用系统设置 API 设置全局 SSO 开关：

```bash
# 启用全局 SSO
curl -X PUT "http://localhost:5000/api/system/settings" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"sso_enabled": true}'
```

### 3. 配置 SSO Provider

每个租户需要注册自己的 SSO Provider：

```bash
# 为租户 1 注册 Google SSO
curl -X POST "http://localhost:5000/api/sso/providers" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "google",
    "predefined": true,
    "client_id": "<client-id>",
    "client_secret": "<client-secret>",
    "tenant_id": 1
  }'
```

### 4. 验证配置

检查登录页面是否正确显示 SSO 选项：

1. 访问登录页面
2. 确认显示已配置的 SSO Provider 按钮
3. 测试 SSO 登录流程

## 向后兼容性

### API 兼容性

租户设置 API 仍然接受 `sso_enabled` 和 `sso_provider` 字段，但会输出警告日志：

```
WARNING: Issue #2128: Tenant-level SSO settings are deprecated: tenant_id=1, fields=['sso_enabled'].
Use system-level sso_enabled (via /api/system/settings) and register SSO providers instead.
These fields will be removed in a future version.
```

### 迁移时间表

- **当前版本**: 软废弃 - 字段仍接受但输出警告
- **未来版本**: 硬废弃 - 字段将被移除

## 常见问题

### Q: 租户能否独立控制本租户的 SSO？

A: 通过 Provider 级别的启用/禁用实现。每个 Provider 可以独立启用或禁用，租户管理员可以控制本租户的 Provider 状态。

### Q: 全局开关禁用后会怎样？

A: 所有租户的登录页面都不显示 SSO 选项。这用于安全事件期间的紧急关闭或维护场景。

### Q: 现有的集成脚本需要修改吗？

A: 如果脚本使用租户设置 API 设置 `sso_enabled`，建议修改为使用系统设置 API。现有脚本短期内仍可工作，但会输出警告日志。

## 参考

- Issue #2128: SSO 全局开关改进
- Issue #2125: SSO 登录页面 Provider 查询逻辑修复
- API 文档: `/api/system/settings`