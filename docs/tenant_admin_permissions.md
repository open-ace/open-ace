# 租户管理员权限模型

## Issue #2179

本文档描述了 Open ACE 的租户管理员权限模型实现。

## 背景

在 Issue #2179 之前，系统使用单一的 `role="admin"` 角色来标识所有管理员，无论是平台级还是租户级。这导致租户管理员可以跨租户访问其他租户的数据，存在严重的安全隐患。

## 权限模型

### 角色定义

系统现在支持以下角色：

| 角色 | 说明 | tenant_id 要求 |
|------|------|----------------|
| `platform_admin` | 平台管理员，可管理所有租户 | 可以为 NULL |
| `tenant_admin` | 租户管理员，只能管理自己的租户 | 必须非 NULL |
| `admin` | 历史管理员角色（迁移过渡期） | - |
| `manager` | 管理员 | - |
| `user` | 普通用户 | - |
| `readonly` | 只读用户 | - |

### 权限规则

#### 平台管理员 (platform_admin)

- 可以查看、创建、修改、删除任何租户
- 可以执行跨租户操作
- 所有跨租户操作都会记录审计日志
- tenant_id 可以为 NULL 或任何值

#### 租户管理员 (tenant_admin)

- 只能管理自己的租户
- 不能列出所有租户
- 不能创建新租户
- 不能暂停、激活、删除其他租户
- 不能修改其他租户的配额
- 可以修改自己租户的设置
- 可以查看自己租户的使用情况和统计
- tenant_id 必须非 NULL

## 权限装饰器

### platform_admin_required

仅限平台管理员访问。

```python
from app.auth.decorators import platform_admin_required

@tenant_bp.route("", methods=["GET"])
@platform_admin_required
def list_tenants():
    """列出所有租户（仅平台管理员）"""
    ...
```

### tenant_admin_required

仅限租户管理员访问，且只能访问自己的租户。

```python
from app.auth.decorators import tenant_admin_required

@some_route
@tenant_admin_required
def manage_own_tenant(tenant_id: int):
    """租户管理员只能访问自己的租户"""
    ...
```

### same_tenant_or_platform_admin

同租户或平台管理员可访问。

```python
from app.auth.decorators import same_tenant_or_platform_admin

@tenant_bp.route("/<int:tenant_id>/settings", methods=["PUT"])
@same_tenant_or_platform_admin
def update_tenant_settings(tenant_id: int):
    """修改租户设置（租户管理员可修改自己的租户）"""
    ...
```

## Fail-Closed 机制

### TenantContext

`app/core/tenant_context.py` 提供了 Fail-Closed 的租户上下文访问：

```python
from app.core.tenant_context import TenantContext

# 获取 tenant_id，缺失时抛出异常（推荐）
tenant_id = TenantContext.get_required_tenant_id()

# 获取 tenant_id，允许缺失（用于平台管理员场景）
tenant_id = TenantContext.get_optional_tenant_id()
```

**禁止模式：**

```python
# ❌ 禁止：静默回退到 tenant_id=1
tenant_id = tenant_id or 1

# ❌ 禁止：使用默认值
tenant_id = data.get("tenant_id", 1)

# ✅ 正确：Fail-Closed
tenant_id = TenantContext.get_required_tenant_id()
```

### ActorContext

`app/core/actor_context.py` 封装了操作者上下文：

```python
from app.core.actor_context import ActorContext

actor = ActorContext(
    user_id=1,
    role="tenant_admin",
    tenant_id=5
)

# 检查权限
if actor.can_access_tenant(target_tenant_id):
    # 执行操作
    ...
```

## 数据迁移

### 迁移脚本

`migrations/versions/20260801_001_add_platform_tenant_admin_roles.py`

迁移策略：
1. 无 tenant_id 的 admin → platform_admin
2. 有 tenant_id 的 admin → tenant_admin
3. 空字符串 tenant_id 规范化为 NULL
4. 添加数据一致性约束

### 执行迁移

```bash
# 在维护窗口执行
alembic upgrade head
```

### 回滚迁移

```bash
alembic downgrade -1
```

## 审计日志

平台管理员执行跨租户操作时，系统会记录审计日志：

- `actor_user_id`: 操作者用户 ID
- `actor_tenant_id`: 操作者的租户 ID
- `target_tenant_id`: 目标租户 ID
- `action`: 操作类型
- `result`: 操作结果
- `request_id`: 请求 ID

## API 权限矩阵

| 端点 | 方法 | 权限 |
|-----|------|------|
| `/api/tenants` | GET | platform_admin |
| `/api/tenants` | POST | platform_admin |
| `/api/tenants/<id>` | GET | platform_admin |
| `/api/tenants/<id>` | PUT | platform_admin |
| `/api/tenants/<id>` | DELETE | platform_admin |
| `/api/tenants/<id>/suspend` | POST | platform_admin |
| `/api/tenants/<id>/activate` | POST | platform_admin |
| `/api/tenants/<id>/quota` | PUT | platform_admin |
| `/api/tenants/<id>/settings` | PUT | same_tenant_or_platform_admin |
| `/api/tenants/<id>/usage` | GET | same_tenant_or_platform_admin |
| `/api/tenants/<id>/stats` | GET | same_tenant_or_platform_admin |
| `/api/tenants/<id>/check-quota` | POST | same_tenant_or_platform_admin |
| `/api/tenants/plans` | GET | auth_required |

## 测试

相关测试文件：
- `tests/unit/test_actor_context.py`
- `tests/unit/test_tenant_context.py`
- `tests/unit/test_user_model_extensions.py`

运行测试：

```bash
pytest tests/unit/test_actor_context.py -v
pytest tests/unit/test_tenant_context.py -v
pytest tests/unit/test_user_model_extensions.py -v
```

## 更新日志

### 2026-08-01

- 新增 `platform_admin` 和 `tenant_admin` 角色
- 实现 Fail-Closed 租户上下文机制
- 消除 23 处静默回退到 tenant_id=1 的逻辑
- 实现三种权限装饰器
- 更新租户管理路由权限
- 添加数据迁移脚本
- 增加审计日志记录

## 参考资料

- Issue #2179: 租户管理员权限模型
- Issue #1775: 租户作用域验证
- Issue #1896: URL Token 安全
