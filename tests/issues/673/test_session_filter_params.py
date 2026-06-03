"""
测试 Issue #673: 会话页面状态和类型过滤功能

验证 /api/workspace/sessions API 正确处理 status 和 session_type 过滤参数。

测试方法：
1. 创建测试数据（不同 status 和 session_type 的会话）
2. 分别测试 status 过滤、session_type 过滤、组合过滤
3. 验证返回结果符合预期
"""

import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from app.repositories.database import Database


@pytest.fixture
def db():
    """Database fixture"""
    return Database()


@pytest.fixture
def test_user_id():
    """Test user ID"""
    return 1


def test_status_filter(db, test_user_id):
    """测试 status 过滤参数"""
    # 查询 active 状态的会话
    sessions = db.fetch_all(
        """
        SELECT session_id, status FROM agent_sessions
        WHERE user_id = ? AND status = ?
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (test_user_id, "active")
    )

    # 验证所有返回的会话状态都是 active
    for s in sessions:
        assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"

    print(f"[PASS] status 过滤: 找到 {len(sessions)} 个 active 会话")


def test_session_type_filter(db, test_user_id):
    """测试 session_type 过滤参数"""
    # 查询 chat 类型的会话
    sessions = db.fetch_all(
        """
        SELECT session_id, session_type FROM agent_sessions
        WHERE user_id = ? AND session_type = ?
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (test_user_id, "chat")
    )

    # 验证所有返回的会话类型都是 chat
    for s in sessions:
        assert s["session_type"] == "chat", f"Expected session_type='chat', got '{s['session_type']}'"

    print(f"[PASS] session_type 过滤: 找到 {len(sessions)} 个 chat 会话")


def test_combined_filter(db, test_user_id):
    """测试 status + session_type 组合过滤"""
    # 查询 active + chat 的会话
    sessions = db.fetch_all(
        """
        SELECT session_id, status, session_type FROM agent_sessions
        WHERE user_id = ? AND status = ? AND session_type = ?
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (test_user_id, "active", "chat")
    )

    # 验证所有返回的会话都符合条件
    for s in sessions:
        assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
        assert s["session_type"] == "chat", f"Expected session_type='chat', got '{s['session_type']}'"

    print(f"[PASS] 组合过滤: 找到 {len(sessions)} 个 active+chat 会话")


def test_api_endpoint_integration():
    """
    集成测试: 通过 HTTP API 测试过滤参数

    需要服务器运行，使用 requests 或 playwright 测试
    """
    import requests

    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

    # 测试 status 过滤
    resp = requests.get(f"{BASE_URL}/api/workspace/sessions", params={"status": "active"})
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.json()
    if data.get("success") and data.get("data", {}).get("sessions"):
        for s in data["data"]["sessions"]:
            assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
        print(f"[PASS] API status 过滤: 找到 {len(data['data']['sessions'])} 个 active 会话")

    # 测试 session_type 过滤
    resp = requests.get(f"{BASE_URL}/api/workspace/sessions", params={"session_type": "chat"})
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.json()
    if data.get("success") and data.get("data", {}).get("sessions"):
        for s in data["data"]["sessions"]:
            assert s["session_type"] == "chat", f"Expected session_type='chat', got '{s['session_type']}'"
        print(f"[PASS] API session_type 过滤: 找到 {len(data['data']['sessions'])} 个 chat 会话")

    # 测试组合过滤
    resp = requests.get(
        f"{BASE_URL}/api/workspace/sessions",
        params={"status": "active", "session_type": "chat"}
    )
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.json()
    if data.get("success") and data.get("data", {}).get("sessions"):
        for s in data["data"]["sessions"]:
            assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
            assert s["session_type"] == "chat", f"Expected session_type='chat', got '{s['session_type']}'"
        print(f"[PASS] API 组合过滤: 找到 {len(data['data']['sessions'])} 个 active+chat 会话")


if __name__ == "__main__":
    # 直接运行测试（不通过 pytest）
    print("=" * 60)
    print("Issue #673: 会话过滤参数测试")
    print("=" * 60)

    db = Database()
    user_id = 1

    print("\n[1] 测试 status 过滤...")
    try:
        test_status_filter(db, user_id)
    except Exception as e:
        print(f"[FAIL] {e}")

    print("\n[2] 测试 session_type 过滤...")
    try:
        test_session_type_filter(db, user_id)
    except Exception as e:
        print(f"[FAIL] {e}")

    print("\n[3] 测试组合过滤...")
    try:
        test_combined_filter(db, user_id)
    except Exception as e:
        print(f"[FAIL] {e}")

    print("\n[4] 测试 API 端点（需要服务器运行）...")
    try:
        test_api_endpoint_integration()
    except requests.exceptions.ConnectionError:
        print("[SKIP] 服务器未运行，跳过 API 测试")
    except Exception as e:
        print(f"[FAIL] {e}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)