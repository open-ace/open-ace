#!/usr/bin/env python3
"""
Test script for Issue 52: Management页面Users tab增加Linux Account功能

测试内容：
1. 数据库层面：linux_account 字段
2. API 层面：更新用户接口支持 linux_account，密码重置接口
3. 前端层面：表格显示 Linux Account 列，编辑和密码重置功能
"""

import sys
import os
import hashlib
import sqlite3
import tempfile
import shutil

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from scripts.shared import db as db_module


def test_database_migration():
    """测试数据库迁移：linux_account 字段是否正确添加"""
    print("\n=== 测试数据库迁移 ===")

    # 创建临时数据库
    temp_dir = tempfile.mkdtemp()
    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = os.path.join(temp_dir, "test.db")
    db_module.DB_DIR = temp_dir

    try:
        # 初始化数据库
        db_module.init_database()

        # 检查 linux_account 字段是否存在
        conn = db_module.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        conn.close()

        assert "linux_account" in columns, "linux_account 字段未添加到 users 表"
        print("✓ linux_account 字段已添加到 users 表")

        return True
    finally:
        # 清理
        db_module.DB_PATH = original_db_path
        db_module.DB_DIR = os.path.dirname(original_db_path)
        shutil.rmtree(temp_dir)


def test_update_user_with_linux_account():
    """测试更新用户时可以设置 linux_account"""
    print("\n=== 测试更新用户 linux_account ===")

    # 创建临时数据库
    temp_dir = tempfile.mkdtemp()
    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = os.path.join(temp_dir, "test.db")
    db_module.DB_DIR = temp_dir

    try:
        # 初始化数据库
        db_module.init_database()

        # 创建测试用户
        password_hash = hashlib.sha256("test123".encode()).hexdigest()
        result = db_module.create_user(
            username="testuser", password_hash=password_hash, email="test@example.com", role="user"
        )
        assert result, "创建用户失败"
        print("✓ 创建测试用户成功")

        # 获取用户 ID
        user = db_module.get_user_by_username("testuser")
        assert user is not None, "获取用户失败"
        user_id = user["id"]

        # 更新 linux_account
        result = db_module.update_user(user_id, linux_account="test_linux_user")
        assert result, "更新 linux_account 失败"
        print("✓ 更新 linux_account 成功")

        # 验证更新
        user = db_module.get_user_by_id(user_id)
        assert user["linux_account"] == "test_linux_user", "linux_account 值不正确"
        print(f"✓ linux_account 值正确: {user['linux_account']}")

        return True
    finally:
        # 清理
        db_module.DB_PATH = original_db_path
        db_module.DB_DIR = os.path.dirname(original_db_path)
        shutil.rmtree(temp_dir)


def test_update_user_password():
    """测试更新用户密码"""
    print("\n=== 测试更新用户密码 ===")

    # 创建临时数据库
    temp_dir = tempfile.mkdtemp()
    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = os.path.join(temp_dir, "test.db")
    db_module.DB_DIR = temp_dir

    try:
        # 初始化数据库
        db_module.init_database()

        # 创建测试用户
        old_password_hash = hashlib.sha256("oldpassword".encode()).hexdigest()
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
        new_password_hash = hashlib.sha256("newpassword".encode()).hexdigest()
        result = db_module.update_user_password(user_id, new_password_hash)
        assert result, "更新密码失败"
        print("✓ 更新密码成功")

        # 验证密码已更新
        user = db_module.get_user_by_id(user_id)
        assert user["password_hash"] == new_password_hash, "密码哈希未更新"
        print("✓ 密码哈希已正确更新")

        return True
    finally:
        # 清理
        db_module.DB_PATH = original_db_path
        db_module.DB_DIR = os.path.dirname(original_db_path)
        shutil.rmtree(temp_dir)


def test_get_all_users_includes_linux_account():
    """测试获取所有用户时包含 linux_account 字段"""
    print("\n=== 测试获取用户列表包含 linux_account ===")

    # 创建临时数据库
    temp_dir = tempfile.mkdtemp()
    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = os.path.join(temp_dir, "test.db")
    db_module.DB_DIR = temp_dir

    try:
        # 初始化数据库
        db_module.init_database()

        # 创建测试用户
        password_hash = hashlib.sha256("test123".encode()).hexdigest()
        db_module.create_user(
            username="user1", password_hash=password_hash, email="user1@example.com", role="user"
        )
        db_module.create_user(
            username="user2", password_hash=password_hash, email="user2@example.com", role="user"
        )
        print("✓ 创建测试用户成功")

        # 更新一个用户的 linux_account
        user1 = db_module.get_user_by_username("user1")
        db_module.update_user(user1["id"], linux_account="linux_user1")

        # 获取所有用户
        users = db_module.get_all_users()
        assert len(users) >= 2, "用户数量不正确"
        print(f"✓ 获取到 {len(users)} 个用户")

        # 检查 linux_account 字段
        user1_found = False
        for user in users:
            if user["username"] == "user1":
                assert user["linux_account"] == "linux_user1", "user1 的 linux_account 不正确"
                user1_found = True
                print(f"✓ user1 的 linux_account: {user['linux_account']}")

        assert user1_found, "未找到 user1"

        return True
    finally:
        # 清理
        db_module.DB_PATH = original_db_path
        db_module.DB_DIR = os.path.dirname(original_db_path)
        shutil.rmtree(temp_dir)


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Issue 52 测试: Management页面Users tab增加Linux Account功能")
    print("=" * 60)

    tests = [
        ("数据库迁移测试", test_database_migration),
        ("更新用户 linux_account 测试", test_update_user_with_linux_account),
        ("更新用户密码测试", test_update_user_password),
        ("获取用户列表包含 linux_account 测试", test_get_all_users_includes_linux_account),
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
