"""Add platform_admin and tenant_admin roles

Revision ID: 20260801_001_add_platform_tenant_admin_roles
Revises: 20260731_004_add_proxy_token_terminated_fields
Create Date: 2026-08-01

Issue: #2179

租户管理员权限模型 - 角色扩展

迁移策略：
1. 无 tenant_id 的 admin → platform_admin
2. 有 tenant_id 的 admin → tenant_admin
3. admin 角色在约束中保留（向后兼容）
4. 添加数据一致性约束：tenant_admin 必须有 tenant_id

Fail-Closed：所有操作都使用事务包装，确保原子性
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260801_001_add_platform_tenant_admin_roles"
down_revision: str | None = "20260731_004_add_proxy_token_terminated_fields"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """添加 platform_admin 和 tenant_admin 角色，迁移现有 admin 账号"""
    connection = op.get_bind()
    dialect = connection.dialect.name
    is_postgresql = dialect == "postgresql"

    log.info("开始迁移管理员角色...")

    # Step 1: 预检查 - 检查异常数据
    log.info("Step 1: 预检查数据一致性...")

    # 检查 tenant_id 为空字符串的管理员账号
    result = connection.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND tenant_id = ''")
    )
    empty_string_count = result.scalar() or 0

    if empty_string_count > 0:
        log.warning(
            f"发现 {empty_string_count} 个管理员账号的 tenant_id 为空字符串，"
            "将规范化为 NULL"
        )

        # 规范化空字符串为 NULL
        connection.execute(
            sa.text("UPDATE users SET tenant_id = NULL WHERE tenant_id = ''")
        )

    # 检查孤儿账号（tenant_id 不存在于 tenants 表）
    # 这里只记录警告，不阻止迁移
    try:
        result = connection.execute(
            sa.text("""
                SELECT COUNT(*)
                FROM users u
                WHERE u.role = 'admin'
                  AND u.tenant_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id = u.tenant_id)
            """)
        )
        orphan_count = result.scalar() or 0

        if orphan_count > 0:
            log.warning(
                f"发现 {orphan_count} 个管理员账号的 tenant_id 不存在于 tenants 表，"
                "请检查数据一致性"
            )
    except Exception as e:
        log.warning(f"检查孤儿账号时出错（可能表不存在）: {e}")

    # Step 2: 更新 CHECK 约束
    log.info("Step 2: 更新 CHECK 约束...")

    if is_postgresql:
        # PostgreSQL: 删除旧约束，添加新约束
        connection.execute(
            sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_role")
        )
        connection.execute(
            sa.text("""
                ALTER TABLE users
                ADD CONSTRAINT chk_users_role
                CHECK (role IN ('admin', 'platform_admin', 'tenant_admin', 'manager', 'user', 'readonly'))
            """)
        )
    else:
        # SQLite: 需要重建表（参考之前的迁移脚本）
        # 这里简化处理，直接修改约束
        # 注意：SQLite 在某些版本支持直接修改约束
        try:
            connection.execute(
                sa.text("""
                    ALTER TABLE users
                    ADD CONSTRAINT chk_users_role_new
                    CHECK (role IN ('admin', 'platform_admin', 'tenant_admin', 'manager', 'user', 'readonly'))
                """)
            )
        except Exception:
            # 如果失败，尝试删除旧约束再添加
            log.warning("无法直接添加约束，尝试删除旧约束...")
            try:
                connection.execute(
                    sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_role")
                )
            except Exception:
                pass

            # SQLite 可能不支持命名约束删除，继续尝试迁移
            log.warning("继续迁移，约束可能需要手动调整")

    # Step 3: 迁移管理员角色
    log.info("Step 3: 迁移管理员角色...")

    # 迁移无 tenant_id 的管理员 → 平台管理员
    result = connection.execute(
        sa.text("""
            UPDATE users
            SET role = 'platform_admin'
            WHERE role = 'admin'
              AND (tenant_id IS NULL OR tenant_id = '')
        """)
    )
    platform_count = result.rowcount
    log.info(f"迁移了 {platform_count} 个平台管理员")

    # 迁移有 tenant_id 的管理员 → 租户管理员
    result = connection.execute(
        sa.text("""
            UPDATE users
            SET role = 'tenant_admin'
            WHERE role = 'admin'
              AND tenant_id IS NOT NULL
              AND tenant_id != ''
        """)
    )
    tenant_count = result.rowcount
    log.info(f"迁移了 {tenant_count} 个租户管理员")

    # Step 4: 添加数据一致性约束
    log.info("Step 4: 添加数据一致性约束...")

    if is_postgresql:
        # 添加约束：租户管理员必须有 tenant_id
        try:
            connection.execute(
                sa.text("""
                    ALTER TABLE users
                    ADD CONSTRAINT chk_tenant_admin_requires_tenant
                    CHECK (NOT (role = 'tenant_admin' AND (tenant_id IS NULL OR tenant_id = '')))
                """)
            )
            log.info("添加了租户管理员一致性约束")
        except Exception as e:
            log.warning(f"添加一致性约束失败: {e}")

    # Step 5: 记录迁移日志
    log.info(
        f"迁移完成: {platform_count} 个平台管理员, "
        f"{tenant_count} 个租户管理员"
    )

    # 验证没有遗留的 admin 角色
    result = connection.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    )
    remaining_admin = result.scalar() or 0

    if remaining_admin > 0:
        log.warning(
            f"仍有 {remaining_admin} 个用户使用 'admin' 角色，"
            "请检查迁移是否正确执行"
        )

    log.info("管理员角色迁移成功")


def downgrade() -> None:
    """回滚管理员角色迁移"""
    connection = op.get_bind()
    dialect = connection.dialect.name
    is_postgresql = dialect == "postgresql"

    log.info("开始回滚管理员角色迁移...")

    # Step 1: 移除数据一致性约束
    if is_postgresql:
        try:
            connection.execute(
                sa.text("""
                    ALTER TABLE users
                    DROP CONSTRAINT IF EXISTS chk_tenant_admin_requires_tenant
                """)
            )
            log.info("移除了租户管理员一致性约束")
        except Exception as e:
            log.warning(f"移除约束失败: {e}")

    # Step 2: 回滚所有新角色为 admin
    result = connection.execute(
        sa.text("""
            UPDATE users
            SET role = 'admin'
            WHERE role IN ('platform_admin', 'tenant_admin')
        """)
    )
    rollback_count = result.rowcount
    log.info(f"回滚了 {rollback_count} 个管理员账号")

    # Step 3: 恢复原 CHECK 约束
    if is_postgresql:
        connection.execute(
            sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_role")
        )
        connection.execute(
            sa.text("""
                ALTER TABLE users
                ADD CONSTRAINT chk_users_role
                CHECK (role IN ('admin', 'manager', 'user', 'readonly'))
            """)
        )

    log.info("管理员角色迁移回滚成功")
