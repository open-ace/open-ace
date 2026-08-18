# Issue #2759 - 工具账号管理接口授权修复

## 问题概述

工具账号管理接口（`app/routes/tool_accounts.py`）仅使用 `auth_required` 装饰器，任何已认证用户都可以：
1. 读取全局工具账号映射
2. 为任意用户创建/修改/删除映射
3. 跨租户操作数据

## 解决方案

### 1. 授权辅助模块（`app/auth/tool_account_auth.py`）

创建集中式授权辅助模块，提供以下函数：
- `validate_user_in_tenant(user_id, tenant_id)` - 验证用户属于指定租户且非 platform-level 角色
- `get_tenant_scoped_user_ids(tenant_id)` - 获取租户内所有用户 ID
- `get_mapping_and_validate_tenant(mapping_id, tenant_id)` - 获取映射并验证租户归属
- `validate_target_user_for_write(user_id, actor_tenant_id)` - 写操作目标用户验证

### 2. 路由层改造

- 将 `before_request` 的 `@auth_required` 改为 `@admin_required`
- 所有接口添加租户隔离逻辑：
  - `tenant_admin`：仅可操作本租户用户
  - `platform_admin`：全局访问（带跨租户审计日志）

### 3. 审计日志集成

添加 4 个新的审计动作：
- `TOOL_ACCOUNT_MAPPING_CREATE`
- `TOOL_ACCOUNT_MAPPING_UPDATE`
- `TOOL_ACCOUNT_MAPPING_DELETE`
- `TOOL_ACCOUNT_MAPPING_BATCH`

## 授权矩阵

| 角色 | 查看 | 创建/修改/删除 |
|------|-----|---------------|
| platform_admin | 全局 | 全局 |
| tenant_admin | 本租户 | 本租户 |
| manager | 禁止 | 禁止 |
| user | 禁止 | 禁止 |

## 测试覆盖

- 单元测试：授权辅助函数（`test_tool_accounts_auth_2759.py`）
- 集成测试：API 端点授权矩阵
- 边界条件：跨租户访问、platform-level 账号保护

## 文件变更

1. `app/auth/tool_account_auth.py` - 新增授权辅助模块
2. `app/routes/tool_accounts.py` - 重写路由层添加授权控制
3. `app/modules/governance/audit_logger.py` - 添加审计动作
4. `tests/integration/test_tool_accounts_auth_2759.py` - 新增测试
5. `tests/unit/test_audit_logger.py` - 更新动作计数测试

## 参考

- Issue #2180：`mapping_rules.py` 的租户隔离模式
- Issue #2179：`admin_required` 装饰器体系
