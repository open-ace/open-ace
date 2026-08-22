#!/usr/bin/env python3
"""
Test script for Issue 52: Management页面Users tab增加Linux Account功能

测试内容：
1. 数据库层面：system_account 字段（原 linux_account，随多用户工作区改造更名）
2. API 层面：更新用户接口支持 system_account，密码重置接口
3. 前端层面：表格显示 System Account 列，编辑和密码重置功能

#2457 realignment notes:
- linux_account 在迁移中更名为 system_account（db.py 会把旧列名重命名或
  补新列），断言随之更名。
- get_connection() 经 _get_db_url() 解析连接目标并缓存；只改 DB_PATH 不
  生效，历史上这些测试实际落在环境数据库上。现在直接把 _db_url_cache
  指到临时 SQLite 文件，测试真正自包含、不再污染 lane 数据库。
"""

import hashlib
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from scripts.shared import db as db_module


@contextmanager
def _temp_sqlite_db():
    """Point the legacy db module at a throwaway SQLite file (#2457).

    get_connection() resolves its target through _get_db_url() (cached), so
    patching DB_PATH alone never redirected anything — the tests used to run
    against whatever ambient database the environment configured (a local
    PostgreSQL in dev, the shared lane DB in CI). Overriding the cache pins
    the connection to a temp file regardless of ambient config.
    """
    temp_dir = tempfile.mkdtemp()
    original_db_path = db_module.DB_PATH
    original_db_dir = db_module.DB_DIR
    original_url_cache = db_module._db_url_cache
    db_module.DB_PATH = os.path.join(temp_dir, "test.db")
    db_module.DB_DIR = temp_dir
    db_module._db_url_cache = f"sqlite:///{db_module.DB_PATH}"
    try:
        yield
    finally:
        db_module.DB_PATH = original_db_path
        db_module.DB_DIR = original_db_dir
        db_module._db_url_cache = original_url_cache
        shutil.rmtree(temp_dir)


def test_database_migration():
    """测试数据库迁移：system_account 字段是否正确添加（原 linux_account）"""
    print("\n=== 测试数据库迁移 ===")

    with _temp_sqlite_db():
        # 初始化数据库
        db_module.init_database()

        # 检查 system_account 字段是否存在
        conn = db_module.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        conn.close()

        assert "system_account" in columns, "system_account 字段未添加到 users 表"
        print("✓ system_account 字段已添加到 users 表")

        return True


def test_update_user_with_system_account():
    """测试更新用户时可以设置 system_account"""
    print("\n=== 测试更新用户 system_account ===")

    with _temp_sqlite_db():
        # 初始化数据库
        db_module.init_database()

        # 创建测试用户
        password_hash = hashlib.sha256(b"test123").hexdigest()
        result = db_module.create_user(
            username="testuser", password_hash=password_hash, email="test@example.com", role="user"
        )
        assert result, "创建用户失败"
        print("✓ 创建测试用户成功")

        # 获取用户 ID
        user = db_module.get_user_by_username("testuser")
        assert user is not None, "获取用户失败"
        user_id = user["id"]

        # 更新 system_account
        result = db_module.update_user(user_id, system_account="test_sys_user")
        assert result, "更新 system_account 失败"
        print("✓ 更新 system_account 成功")

        # 验证更新
        user = db_module.get_user_by_id(user_id)
        assert user["system_account"] == "test_sys_user", "system_account 值不正确"
        print(f"✓ system_account 值正确: {user['system_account']}")

        return True


def test_update_user_password():
    """测试更新用户密码"""
    print("\n=== 测试更新用户密码 ===")

    with _temp_sqlite_db():
        # 初始化数据库
        db_module.init_database()

        # 创建测试用户
        old_password_hash = hashlib.sha256(b"oldpassword").hexdigest()
        result = db_module.create_user(
            username="passwordtest",
            password_hash=old_password_hash,
            email="password@example.com",
            role="user",
        )
        assert result, "创建用户失败"
        print("✓ 创建测试用户成功")

        # 获取用户 ID
        user = db_module.get_user_by_username("passwordtest")
        assert user is not None, "获取用户失败"
        user_id = user["id"]

        # 更新密码
        new_password_hash = hashlib.sha256(b"newpassword").hexdigest()
        result = db_module.update_user_password(user_id, new_password_hash)
        assert result, "更新密码失败"
        print("✓ 更新密码成功")

        # 验证密码已更新
        user = db_module.get_user_by_id(user_id)
        assert user["password_hash"] == new_password_hash, "密码哈希未更新"
        print("✓ 密码哈希已正确更新")

        return True


def test_get_all_users_projection_and_system_account():
    """测试用户列表投影，及 system_account 的持久化可见性

    get_all_users 是配额脚本用的精简投影（不含 system_account）；完整的
    system_account 值经 get_user_by_id（SELECT *）读取 —— 与 Management
    页面走 app 层 user_repo 的行为区分开。
    """
    print("\n=== 测试获取用户列表投影与 system_account ===")

    with _temp_sqlite_db():
        # 初始化数据库
        db_module.init_database()

        # 创建测试用户
        password_hash = hashlib.sha256(b"test123").hexdigest()
        db_module.create_user(
            username="user1", password_hash=password_hash, email="user1@example.com", role="user"
        )
        db_module.create_user(
            username="user2", password_hash=password_hash, email="user2@example.com", role="user"
        )
        print("✓ 创建测试用户成功")

        # 更新一个用户的 system_account
        user1 = db_module.get_user_by_username("user1")
        db_module.update_user(user1["id"], system_account="system_user1")

        # 获取所有用户
        users = db_module.get_all_users()
        assert len(users) >= 2, "用户数量不正确"
        print(f"✓ 获取到 {len(users)} 个用户")

        # 配额脚本的固定投影列
        usernames = {user["username"] for user in users}
        assert {"user1", "user2"} <= usernames, "用户列表缺少测试用户"
        for user in users:
            assert set(user.keys()) == {
                "id",
                "username",
                "email",
                "role",
                "daily_token_quota",
                "daily_request_quota",
                "is_active",
                "created_at",
            }, f"get_all_users 投影列漂移: {sorted(user.keys())}"
        print("✓ get_all_users 投影列符合配额脚本契约")

        # system_account 持久化，经详情读取
        detail = db_module.get_user_by_id(user1["id"])
        assert detail["system_account"] == "system_user1", "user1 的 system_account 不正确"
        print(f"✓ user1 的 system_account: {detail['system_account']}")

        return True


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Issue 52 测试: Management页面Users tab增加System Account功能")
    print("=" * 60)

    tests = [
        ("数据库迁移测试", test_database_migration),
        ("更新用户 system_account 测试", test_update_user_with_system_account),
        ("更新用户密码测试", test_update_user_password),
        ("用户列表投影与 system_account 测试", test_get_all_users_projection_and_system_account),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
                print(f"\n✓ {name} 通过")
        except AssertionError as e:
            failed += 1
            print(f"\n✗ {name} 失败: {e}")
        except Exception as e:
            failed += 1
            print(f"\n✗ {name} 异常: {e}")

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
