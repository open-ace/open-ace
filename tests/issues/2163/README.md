# Issue #2163 Implementation

## 概述

本实现为 Issue #2163 提供租户迁移和会话失效机制，解决跨租户数据泄露风险。

## 已实现组件

### Phase 1: 核心功能

#### 1. TenantMigrationService (`app/services/tenant_migration.py`)
- 提供用户租户迁移的事务性操作
- 支持分批提交（大规模迁移）
- 支持断点续传
- 提供 dry-run 模式预检
- 提供 rollback 机制

#### 2. TenantCheckMiddleware (`app/middleware/tenant_check.py`)
- 全局注册的认证中间件
- 检测 tenant_version 不匹配
- 区分 TENANT_MIGRATED 和 SESSION_EXPIRED 错误
- 支持国际化（en/zh/ja/ko）

#### 3. TenantResolver (`app/utils/tenant_resolver.py`)
- 统一的租户 ID 解析逻辑
- 支持 fail-closed 和 fail-open 模式
- 消除跨模块的重复实现

#### 4. RequestContext (`app/utils/request_context.py`)
- 统一的请求上下文工具
- 提供获取当前用户、租户 ID、租户版本的辅助函数

#### 5. 数据库迁移 (`migrations/versions/20260731_001_add_tenant_version.py`)
- 添加 `tenant_version` 列到 `users` 和 `agent_sessions` 表
- 创建 `tenant_migrations` 表记录迁移历史
- 添加必要的索引

## 测试覆盖

- 总计 59 个单元测试
- 覆盖率：
  - TenantMigrationService: 100%
  - TenantCheckMiddleware: 100%
  - TenantResolver: 100%
  - RequestContext: 100%

## 使用示例

### 迁移用户到新租户

```python
from app.services.tenant_migration import TenantMigrationService

service = TenantMigrationService()

# 单用户迁移
result = service.migrate_user_tenant(
    user_id=123,
    new_tenant_id=2,
    migrated_by=1  # admin user ID
)

if result.success:
    print(f"迁移成功: {result.affected_sessions} sessions, {result.affected_projects} projects")
else:
    print(f"迁移失败: {result.error}")

# 批量迁移
results = service.migrate_users_batch(
    user_ids=[1, 2, 3],
    new_tenant_id=2,
    migrated_by=1,
    batch_size=10
)
```

### 在路由中使用 RequestContext

```python
from app.utils.request_context import get_current_tenant_id, require_tenant_id

@app.route("/api/projects")
def list_projects():
    # 获取当前租户 ID（可能为 None）
    tenant_id = get_current_tenant_id()

    # 或要求必须有租户上下文
    tenant_id = require_tenant_id()

    # 使用租户 ID 查询数据
    return get_projects_by_tenant(tenant_id)
```

### 使用 TenantResolver

```python
from app.utils.tenant_resolver import TenantResolver

# 写操作（fail-closed）
tenant_id = TenantResolver.resolve_for_write(
    tenant_id=explicit_tenant_id,  # 可选
    user_id=current_user_id,       # 可选
    db=db
)

# 读操作（fail-open）
tenant_id = TenantResolver.resolve_for_read(
    tenant_id=explicit_tenant_id,
    user_id=current_user_id,
    db=db,
    default=1  # 默认租户
)
```

## 下一步

根据审定方案，还需要实现：

### Phase 2: 代码质量改进
- 重构现有代码使用 TenantResolver
- 统一 `_current_tenant_id` helper
- client_secret 安全加固

### Phase 3: 架构改进
- K8s storageClassName 配置化
- SAML SLO 实现
- 组织同步 SQL 优化
- 集成测试

## 注意事项

1. 中间件已全局注册，无需在每个 blueprint 中手动添加
2. 迁移操作使用 PostgreSQL advisory lock 防止并发冲突
3. 前端需要处理 TENANT_MIGRATED 和 SESSION_EXPIRED 两种不同的 401 错误
4. 数据库迁移需要先运行 `alembic upgrade head`