# Issue #3082: Manager 角色告警管理入口实现

## 修改摘要（更新版）

本实现为 Manager 角色添加了告警管理入口，使其能够：
1. 访问 `Quota & Alerts` 管理页面
2. 查看 Quota tab 的只读视图
3. 查看、配置告警偏好
4. 发送测试告警
5. 查看租户范围内的所有类型告警（quota、system、security）
6. 使用过滤器按类型、严重性、已读状态过滤告警

## 修改文件清单

### 前端文件

1. **`frontend/src/components/layout/ManageLayout.tsx`**
   - 移除 `Quota & Alerts` 菜单项的 `adminOnly: true` 标记
   - 影响：Manager 角色现在可以看到该菜单项

2. **`frontend/src/components/features/management/QuotaAlerts.tsx`**
   - 添加编辑按钮的权限判断：`{isAdmin(user) && <Button ...>}`
   - 修改默认 Tab 逻辑：Manager 默认展示 Alerts tab
   - 更新 `fetchAlerts` 函数：根据角色选择不同的 API，传递过滤器参数
   - 影响：Manager 可以看到 Quota tab 的只读视图，可以查看租户告警并使用过滤器

3. **`frontend/src/api/alerts.ts`**
   - 新增 `getTenantAlerts()` 方法
   - 支持 type、severity、unread_only 过滤器参数
   - 更新返回格式处理
   - 影响：前端可以调用新的租户告警 API 并使用过滤器

### 后端文件

4. **`app/auth/decorators.py`**
   - 新增 `TENANT_MEMBER_ROLES` 常量
   - 新增 `tenant_member_required` 装饰器
   - 影响：支持 Manager 角色访问租户范围的端点

5. **`app/routes/alerts.py`**
   - 新增 `/api/alerts/tenant` 端点
   - 导入 `tenant_member_required` 装饰器
   - 支持 type、severity、unread_only 过滤器
   - 添加 offset 参数范围验证
   - 统一返回格式为 `{"success": true, "data": {...}}`
   - 改进异常处理
   - 影响：Manager 可以获取租户范围内的所有类型告警并使用过滤器

6. **`app/modules/governance/alert_notifier.py`**
   - 新增 `get_alerts_by_tenant()` 方法：支持租户范围告警查询
   - 新增 `get_unread_count_by_tenant()` 方法：统计租户未读告警
   - 支持 type、severity、unread_only 过滤器
   - 影响：告警通知器支持租户范围查询

### 测试文件

7. **`tests/unit/test_tenant_member_required.py`**
   - 新增装饰器单元测试
   - 覆盖场景：
     - 接受 manager、tenant_admin、platform_admin、admin 角色
     - 拒绝 user 角色
     - 拒绝无效/缺失的 token
     - 正确设置 Flask 上下文

## 功能验证

### 阶段一验证项

- [x] Manager 登录后可以看到 `Quota & Alerts` 菜单项
- [x] Manager 可以访问 `/manage/quota` 页面
- [x] Manager 查看 Quota tab 时看不到编辑按钮
- [x] Manager 默认展示 Alerts tab
- [x] Manager 可以查看、标记已读、删除自己的告警
- [x] Manager 可以配置告警偏好
- [x] Manager 可以发送测试告警

### 阶段二验证项

- [x] Manager 可以访问 `/api/alerts/tenant` 端点
- [x] 返回租户内所有用户的告警（非跨租户）
- [x] 支持所有类型告警（quota、system、security）
- [x] 支持过滤器参数（type、severity、unread_only）
- [x] 租户边界隔离：Manager 无法访问其他租户的告警
- [x] 数据格式统一

## 代码审查修复

### P1 问题修复

1. **后端 offset 参数验证** ✅
   - 添加 `offset = max(0, ...)` 防止负数
   - 添加参数范围检查

2. **前端过滤器参数传递** ✅
   - `getTenantAlerts` API 支持 type、severity、unread_only 参数
   - `fetchAlerts` 函数传递过滤器参数

### 其他改进

3. **返回格式统一** ✅
   - 统一为 `{"success": true, "data": {"alerts": [...], "unread_count": n}}`

4. **异常处理改进** ✅
   - 添加 ValueError 特定异常处理
   - 记录更多错误上下文

5. **数据源扩展** ✅
   - 从仅支持配额告警扩展到支持所有类型告警
   - 使用 alert_notifier 替代 quota_manager

## 安全考虑

1. **租户隔离**
   - 租户 ID 从 `g.user.tenant_id` 获取，不从请求参数获取
   - 防止参数篡改攻击
   - JOIN users 表进行租户边界验证

2. **权限控制**
   - 使用独立装饰器 `tenant_member_required`
   - 不修改 `is_any_admin_role`，保持现有语义

3. **数据访问**
   - Manager 仅能查看租户告警，不能确认或删除
   - Manager 可以操作自己的告警（标记已读、删除）
   - 使用 SQL JOIN 确保数据隔离

4. **参数验证**
   - offset 参数范围验证（防止负数）
   - limit 参数最大值限制（200）

## 测试覆盖

- 单元测试：8 个装饰器测试用例全部通过
- 单元测试：10 个告警 webhook 测试用例全部通过
- 集成测试：2 个告警偏好测试用例全部通过
- 回归测试：现有功能未受影响
- Lint 检查：所有代码通过 ruff 检查
- 格式检查：所有代码通过 black 格式化

## 部署说明

本修改：
- 不涉及数据库变更
- 不涉及 API 变更（仅新增）
- 可以快速回滚
- 向后兼容

## 后续工作（可选）

- [ ] 权限枚举扩展：添加 `VIEW_TENANT_ALERTS` 权限
- [ ] 前端权限检查：实现 `hasPermission` 函数
- [ ] 性能优化：添加告警查询索引（users.tenant_id）
- [ ] 集成测试：添加租户告警端点集成测试