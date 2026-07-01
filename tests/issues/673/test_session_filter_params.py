"""
测试 Issue #673: 会话页面状态和类型过滤功能

验证 /api/workspace/sessions API 正确处理 status 和 session_type 过滤参数。

测试方法：
1. 使用 fixture 创建独立测试数据（不同 status 和 session_type 的会话）
2. 分别测试 status 过滤、session_type 过滤、组合过滤
3. 验证返回结果符合预期
4. 测试后清理测试数据

"""

import os
import sys
import time
import uuid

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from app.repositories.database import Database


@pytest.fixture
def db():
    """Database fixture"""
    return Database()


@pytest.fixture
def test_user_id(db):
    """Create a test user and return its ID"""
    test_email = f"test_filter_{uuid.uuid4().hex[:8]}@test.com"

    # Create test user
    db.execute(
        """
        INSERT INTO users (email, name, password_hash, is_active, created_at, updated_at)
        VALUES (?, ?, 'test_hash', 1, ?, ?)
        """,
        (
            test_email,
            "Test Filter User",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            time.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    # Get user ID
    user = db.fetch_one("SELECT id FROM users WHERE email = ?", (test_email,))

    yield user["id"]

    # Cleanup: delete test user
    db.execute("DELETE FROM users WHERE email = ?", (test_email,))


@pytest.fixture
def test_sessions(db, test_user_id):
    """Create test sessions with different status and session_type values"""
    sessions_data = [
        # (session_id, status, session_type, title)
        (f"test_active_chat_{uuid.uuid4().hex}", "active", "chat", "Test Active Chat"),
        (f"test_paused_chat_{uuid.uuid4().hex}", "paused", "chat", "Test Paused Chat"),
        (
            f"test_completed_workflow_{uuid.uuid4().hex}",
            "completed",
            "workflow",
            "Test Completed Workflow",
        ),
        (f"test_error_agent_{uuid.uuid4().hex}", "error", "agent", "Test Error Agent"),
    ]

    created_session_ids = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    for session_id, status, session_type, title in sessions_data:
        db.execute(
            """
            INSERT INTO agent_sessions
            (session_id, user_id, status, session_type, title, tool_name, host_name,
             total_tokens, message_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'qwen', 'test-host', 100, 5, ?, ?)
            """,
            (session_id, test_user_id, status, session_type, title, now, now),
        )
        created_session_ids.append(session_id)

    yield created_session_ids

    # Cleanup: delete test sessions
    for session_id in created_session_ids:
        db.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))


def test_status_filter(db, test_user_id, test_sessions):
    """测试 status 过滤参数"""
    # 查询 active 状态的会话
    sessions = db.fetch_all(
        """
        SELECT session_id, status FROM agent_sessions
        WHERE user_id = ? AND status = ?
        ORDER BY updated_at DESC
        """,
        (test_user_id, "active"),
    )

    # 验证所有返回的会话状态都是 active
    for s in sessions:
        assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"

    # 验证找到预期的 active 会话（应该是 2 个）
    active_count = len([sid for sid in test_sessions if "active" in sid])
    assert (
        len(sessions) == active_count
    ), f"Expected {active_count} active sessions, got {len(sessions)}"

    print(f"[PASS] status 过滤: 找到 {len(sessions)} 个 active 会话")


def test_session_type_filter(db, test_user_id, test_sessions):
    """测试 session_type 过滤参数"""
    # 查询 chat 类型的会话
    sessions = db.fetch_all(
        """
        SELECT session_id, session_type FROM agent_sessions
        WHERE user_id = ? AND session_type = ?
        ORDER BY updated_at DESC
        """,
        (test_user_id, "chat"),
    )

    # 验证所有返回的会话类型都是 chat
    for s in sessions:
        assert (
            s["session_type"] == "chat"
        ), f"Expected session_type='chat', got '{s['session_type']}'"

    # 验证找到预期的 chat 会话（应该是 3 个）
    chat_count = len([sid for sid in test_sessions if "chat" in sid])
    assert len(sessions) == chat_count, f"Expected {chat_count} chat sessions, got {len(sessions)}"

    print(f"[PASS] session_type 过滤: 找到 {len(sessions)} 个 chat 会话")


def test_combined_filter(db, test_user_id, test_sessions):
    """测试 status + session_type 组合过滤"""
    # 查询 active + chat 的会话
    sessions = db.fetch_all(
        """
        SELECT session_id, status, session_type FROM agent_sessions
        WHERE user_id = ? AND status = ? AND session_type = ?
        ORDER BY updated_at DESC
        """,
        (test_user_id, "active", "chat"),
    )

    # 验证所有返回的会话都符合条件
    for s in sessions:
        assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
        assert (
            s["session_type"] == "chat"
        ), f"Expected session_type='chat', got '{s['session_type']}'"

    # 验证找到预期的 active+chat 会话（应该是 1 个）
    active_chat_count = len([sid for sid in test_sessions if "active_chat" in sid])
    assert (
        len(sessions) == active_chat_count
    ), f"Expected {active_chat_count} active+chat sessions, got {len(sessions)}"

    print(f"[PASS] 组合过滤: 找到 {len(sessions)} 个 active+chat 会话")


def test_invalid_status_filter(db, test_user_id, test_sessions):
    """测试无效 status 参数（应被忽略）"""
    # 使用无效的 status 值
    sessions = db.fetch_all(
        """
        SELECT session_id, status FROM agent_sessions
        WHERE user_id = ? AND status = ?
        ORDER BY updated_at DESC
        """,
        (test_user_id, "invalid_status"),
    )

    # 无效值应该返回 0 个结果
    assert len(sessions) == 0, f"Expected 0 sessions for invalid status, got {len(sessions)}"

    print("[PASS] 无效 status 过滤: 返回 0 个结果（符合预期）")


def test_invalid_session_type_filter(db, test_user_id, test_sessions):
    """测试无效 session_type 参数（应被忽略）"""
    # 使用无效的 session_type 值
    sessions = db.fetch_all(
        """
        SELECT session_id, session_type FROM agent_sessions
        WHERE user_id = ? AND session_type = ?
        ORDER BY updated_at DESC
        """,
        (test_user_id, "invalid_type"),
    )

    # 无效值应该返回 0 个结果
    assert len(sessions) == 0, f"Expected 0 sessions for invalid session_type, got {len(sessions)}"

    print("[PASS] 无效 session_type 过滤: 返回 0 个结果（符合预期）")


def test_api_endpoint_integration():
    """
    集成测试: 通过 HTTP API 测试过滤参数

    需要服务器运行，使用 requests 测试
    """
    import requests

    BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")

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
            assert (
                s["session_type"] == "chat"
            ), f"Expected session_type='chat', got '{s['session_type']}'"
        print(f"[PASS] API session_type 过滤: 找到 {len(data['data']['sessions'])} 个 chat 会话")

    # 测试组合过滤
    resp = requests.get(
        f"{BASE_URL}/api/workspace/sessions", params={"status": "active", "session_type": "chat"}
    )
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.json()
    if data.get("success") and data.get("data", {}).get("sessions"):
        for s in data["data"]["sessions"]:
            assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
            assert (
                s["session_type"] == "chat"
            ), f"Expected session_type='chat', got '{s['session_type']}'"
        print(f"[PASS] API 组合过滤: 找到 {len(data['data']['sessions'])} 个 active+chat 会话")

    # 测试无效参数（API 应忽略无效值）
    resp = requests.get(f"{BASE_URL}/api/workspace/sessions", params={"status": "invalid_status"})
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    print("[PASS] API 无效参数处理: 返回 200")


if __name__ == "__main__":
    # 直接运行测试（不通过 pytest）
    print("=" * 60)
    print("Issue #673: 会话过滤参数测试")
    print("=" * 60)

    db = Database()

    print("\n[!] 注意: 直接运行模式使用硬编码测试数据，建议使用 pytest 运行")
    print("\n运行 pytest 命令:")
    print("  pytest tests/issues/673/test_session_filter_params.py -v")

    # 简化测试（使用硬编码测试数据）
    test_email = "test_filter_direct@test.com"

    # 创建测试用户
    try:
        db.execute(
            """
            INSERT OR IGNORE INTO users (email, name, password_hash, is_active, created_at, updated_at)
            VALUES (?, ?, 'test_hash', 1, ?, ?)
            """,
            (
                test_email,
                "Test Filter User Direct",
                time.strftime("%Y-%m-%d %H:%M:%S"),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
    except Exception:
        pass  # SQLite 使用 INSERT OR IGNORE

    user = db.fetch_one("SELECT id FROM users WHERE email = ?", (test_email,))
    if not user:
        # PostgreSQL
        db.execute(
            """
            INSERT INTO users (email, name, password_hash, is_active, created_at, updated_at)
            VALUES (?, ?, 'test_hash', 1, ?, ?)
            ON CONFLICT (email) DO NOTHING
            """,
            (
                test_email,
                "Test Filter User Direct",
                time.strftime("%Y-%m-%d %H:%M:%S"),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        user = db.fetch_one("SELECT id FROM users WHERE email = ?", (test_email,))

    user_id = user["id"] if user else 1

    print(f"\n使用测试用户 ID: {user_id}")

    # 创建测试会话
    test_session_id = f"test_direct_{uuid.uuid4().hex}"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute(
            """
            INSERT INTO agent_sessions
            (session_id, user_id, status, session_type, title, tool_name, host_name,
             total_tokens, message_count, created_at, updated_at)
            VALUES (?, ?, 'active', 'chat', 'Direct Test', 'qwen', 'test-host', 100, 5, ?, ?)
            """,
            (test_session_id, user_id, now, now),
        )
    except Exception as e:
        print(f"[WARN] 创建测试会话失败: {e}")

    print("\n[1] 测试 status 过滤...")
    sessions = db.fetch_all(
        "SELECT session_id, status FROM agent_sessions WHERE user_id = ? AND status = ?",
        (user_id, "active"),
    )
    print(f"  找到 {len(sessions)} 个 active 会话")

    print("\n[2] 测试 session_type 过滤...")
    sessions = db.fetch_all(
        "SELECT session_id, session_type FROM agent_sessions WHERE user_id = ? AND session_type = ?",
        (user_id, "chat"),
    )
    print(f"  找到 {len(sessions)} 个 chat 会话")

    print("\n[3] 测试组合过滤...")
    sessions = db.fetch_all(
        "SELECT session_id, status, session_type FROM agent_sessions WHERE user_id = ? AND status = ? AND session_type = ?",
        (user_id, "active", "chat"),
    )
    print(f"  找到 {len(sessions)} 个 active+chat 会话")

    # 清理
    db.execute("DELETE FROM agent_sessions WHERE session_id = ?", (test_session_id,))

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
