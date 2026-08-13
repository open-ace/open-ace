"""
测试 Issue #673: 会话页面状态和类型过滤功能

验证 /api/workspace/sessions API 正确处理 status 和 session_type 过滤参数。

测试方法：
1. 使用 fixture 在独立的临时 SQLite 库（权威 schema）创建测试数据
2. 分别测试 status 过滤、session_type 过滤、组合过滤
3. 验证返回结果符合预期
4. API 集成测试通过 Flask test client（补丁鉴权）验证真实路由行为

历史问题（#2457 baseline 6 条）：
- 旧 fixture 直接 ``Database()``：本地解析到 dev Postgres（``users.name`` 列不
  存在），CI 的 isolated-home 下解析到无表的空 SQLite（``no such table: users``）
  → 5 条 setup_error。现改为 load_schema_from_file 的临时库 + DATABASE_URL 指向。
- 旧 API 集成测试请求真实服务器且不带 token（401）→ 1 条 assertion_failure。
  现改为注册 workspace_bp 的 test client + 补丁 _load_user_from_token。
"""

import os
import sys
import time
import uuid
from unittest.mock import patch

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file

_AUTH_PATH = "app.routes.workspace._load_user_from_token"
_EXTRACT_PATH = "app.routes.workspace._extract_token"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """独立临时 SQLite 库（权威 schema），不依赖环境/服务器数据库。

    路由内 ``Database()`` 按 DATABASE_URL 解析，monkeypatch 指向本库后测试进程
    与被测路由共享同一份数据；用例结束自动还原环境变量与临时文件。
    """
    db_url = f"sqlite:///{tmp_path / 'issue673.db'}"
    load_schema_from_file(db_url=db_url, dialect="sqlite")
    monkeypatch.setenv("DATABASE_URL", db_url)
    return Database()


@pytest.fixture
def test_user_id(db):
    """Create a test user and return its ID"""
    test_email = f"test_filter_{uuid.uuid4().hex[:8]}@test.com"

    # Create test user (users 表列为 username；无 updated_at 列 —— 旧代码两处均误写)
    db.execute(
        """
        INSERT INTO users (email, username, password_hash, is_active, created_at)
        VALUES (?, ?, 'test_hash', 1, ?)
        """,
        (
            test_email,
            "Test Filter User",
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

    # 验证找到预期的 active 会话（应该是 1 个）
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

    # 验证找到预期的 chat 会话（应该是 2 个）
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


@pytest.fixture
def client(db):
    """Flask test client with the workspace blueprint (no live server needed)."""
    from flask import Flask

    from app.routes.workspace import workspace_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
    return app.test_client()


def _authed_get(client, url, user_id):
    """GET with auth patched to the seeded user (604-style blueprint test)."""
    user = {
        "id": user_id,
        "username": "Test Filter User",
        "role": "user",
        "tenant_id": None,
    }
    with patch(_AUTH_PATH, return_value=user), patch(_EXTRACT_PATH, return_value="tok"):
        return client.get(url)


def test_api_endpoint_integration(client, db, test_user_id, test_sessions):
    """
    集成测试: 通过 API 测试过滤参数（Flask test client + 补丁鉴权）

    旧版直接 requests 打真实服务器且无 token（CI 401）。现在注册 workspace_bp
    的 test client 上走真实路由/查询构建逻辑，鉴权指向 seeded 用户。
    """
    # 测试 status 过滤
    resp = _authed_get(client, "/api/workspace/sessions?status=active", test_user_id)
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.get_json()
    assert data.get("success") is True
    sessions = data.get("data", {}).get("sessions", [])
    for s in sessions:
        assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
    assert len(sessions) == 1, f"Expected 1 active session, got {len(sessions)}"
    print(f"[PASS] API status 过滤: 找到 {len(sessions)} 个 active 会话")

    # 测试 session_type 过滤
    resp = _authed_get(client, "/api/workspace/sessions?session_type=chat", test_user_id)
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.get_json()
    assert data.get("success") is True
    sessions = data.get("data", {}).get("sessions", [])
    for s in sessions:
        assert (
            s["session_type"] == "chat"
        ), f"Expected session_type='chat', got '{s['session_type']}'"
    assert len(sessions) == 2, f"Expected 2 chat sessions, got {len(sessions)}"
    print(f"[PASS] API session_type 过滤: 找到 {len(sessions)} 个 chat 会话")

    # 测试组合过滤
    resp = _authed_get(
        client, "/api/workspace/sessions?status=active&session_type=chat", test_user_id
    )
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.get_json()
    assert data.get("success") is True
    sessions = data.get("data", {}).get("sessions", [])
    for s in sessions:
        assert s["status"] == "active", f"Expected status='active', got '{s['status']}'"
        assert (
            s["session_type"] == "chat"
        ), f"Expected session_type='chat', got '{s['session_type']}'"
    assert len(sessions) == 1, f"Expected 1 active+chat session, got {len(sessions)}"
    print(f"[PASS] API 组合过滤: 找到 {len(sessions)} 个 active+chat 会话")

    # 测试无效参数（API 应忽略无效值并返回 200 + 未过滤列表）
    resp = _authed_get(client, "/api/workspace/sessions?status=invalid_status", test_user_id)
    assert resp.status_code == 200, f"API 返回 {resp.status_code}"
    data = resp.get_json()
    assert data.get("success") is True
    sessions = data.get("data", {}).get("sessions", [])
    assert len(sessions) == len(
        test_sessions
    ), f"Expected all {len(test_sessions)} sessions (invalid filter ignored), got {len(sessions)}"
    print("[PASS] API 无效参数处理: 返回 200 且忽略无效过滤")


if __name__ == "__main__":
    # 直接运行委托给 pytest（fixture 化后不再支持硬编码直跑模式）
    raise SystemExit(pytest.main([__file__, "-v"]))
