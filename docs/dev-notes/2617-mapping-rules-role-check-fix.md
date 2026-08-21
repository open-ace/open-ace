# Issue #2617 修复总结

## 修复内容

将 `app/routes/mapping_rules.py` 中所有12个端点的角色检查统一为使用 `is_platform_admin_role` 函数，确保严格模式下 admin 角色正确受租户限制。

## 修改的端点

以下端点的角色检查从硬编码 `user_role == "tenant_admin"` 改为 `is_platform_admin_role(user_role)`:

1. `GET /api/mapping-stats` - L302-323
2. `POST /api/mapping-rules/auto-map` - L326-364
3. `POST /api/mapping-rules/test-match` - L367-408
4. `GET /api/unmapped-accounts` - L411-437
5. `GET /api/unmapped-accounts/<sender_name>/suggest-mapping` - L440-477
6. `POST /api/unmapped-accounts/<sender_name>/map` - L480-521

已修改的端点（之前已经正确）:
- `GET /api/mapping-rules` - L60-90
- `GET /api/mapping-rules/user/<int:user_id>` - L93-116
- `POST /api/mapping-rules` - L119-164
- `PUT /api/mapping-rules/<int:id>` - L167-220
- `DELETE /api/mapping-rules/<int:id>` - L223-255
- `POST /api/mapping-rules/user/<int:user_id>/generate-default` - L258-299

## 验证结果

- ✅ 所有39个单元测试通过（包括严格模式测试）
- ✅ 所有69个跨租户攻击防护测试通过
- ✅ 所有21个严格模式配置测试通过
- ✅ 所有36个 auto mapping service 测试通过
- ✅ 无硬编码的 `user_role == "tenant_admin"` 检查
- ✅ 所有端点统一使用 `is_platform_admin_role`

## 影响分析

| 模式 | 角色 | 修复前行为 | 修复后行为 |
|------|------|-----------|-----------|
| 非严格 | admin | 允许跨租户操作 | 允许跨租户操作 ✅ |
| 严格 | admin | **允许跨租户操作（漏洞）** | **拒绝跨租户操作** ✅ |
| 任意 | tenant_admin | 拒绝跨租户操作 | 拒绝跨租户操作 ✅ |
| 任意 | platform_admin | 允许跨租户操作 | 允许跨租户操作 ✅ |

## 测试覆盖

- 单元测试：`tests/unit/test_mapping_rules_api.py::TestTenantValidation`
  - 严格模式下 admin 跨租户操作被拒绝
  - 非严格模式下 admin 跨租户操作被允许
  - tenant_admin 跨租户操作被拒绝
  - platform_admin 跨租户操作被允许

- 集成测试：`tests/integration/test_tenant_isolation_mapping_rules.py`
  - 租户隔离验证
  - 跨租户操作被阻止

## 相关 Issue

- Issue #2617: 租户校验逻辑缺陷
- Issue #2180: Tenant isolation for mapping rules
- Issue #2286: Platform admin access control
