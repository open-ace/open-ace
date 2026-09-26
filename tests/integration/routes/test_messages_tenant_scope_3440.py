"""Tenant isolation for the messages blueprint (Issue #3440).

Every endpoint in ``app/routes/messages.py`` used to be guarded by
``@auth_required`` alone and never passed a tenant to the repository, so any
authenticated user of any tenant read every tenant's senders, message content
and conversations. ``/api/senders`` additionally cached the global list in a
module-global dict shared by all callers.

These tests drive the real route -> service -> repository stack against a
temporary SQLite database holding two tenants' messages and pin:

* tenant-scoped users (including ``tenant_admin``) see only their tenant;
* platform admins keep the global view;
* a non-platform-admin with no tenant is denied (fail closed);
* the senders cache never serves one tenant's list to another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

pytestmark = [pytest.mark.regression, pytest.mark.issue(3440), pytest.mark.security]

DATE = "2026-09-20"

# (tenant_id, sender_name, conversation_id, message_id, content)
_ROWS = [
    (1, "alice", "conv-t1", "m1", "tenant one secret A"),
    (1, "bob", "conv-t1", "m2", "tenant one secret B"),
    (2, "carol", "conv-t2", "m3", "tenant two secret C"),
]

PLATFORM_ADMIN = {"id": 1, "username": "pa", "role": "platform_admin", "tenant_id": None}
TENANT1_USER = {"id": 10, "username": "u1", "role": "user", "tenant_id": 1}
TENANT2_ADMIN = {"id": 20, "username": "ta2", "role": "tenant_admin", "tenant_id": 2}
NO_TENANT_USER = {"id": 30, "username": "nt", "role": "user", "tenant_id": None}

ALL_ENDPOINTS = [
    "/api/senders",
    f"/api/messages?date={DATE}",
    f"/api/messages/count?date={DATE}",
    f"/api/conversation-history?date={DATE}",
    "/api/conversation-timeline/conv-t1",
    "/api/conversation-details/conv-t1",
]


@pytest.fixture
def client(tmp_db):
    from app.repositories.message_repo import MessageRepository
    from app.routes.messages import messages_bp
    from app.services.message_service import MessageService

    with tmp_db.get_connection() as conn:
        cursor = conn.cursor()
        for tenant_id, sender, conv, msg_id, content in _ROWS:
            cursor.execute(
                "INSERT INTO daily_messages (date, tool_name, host_name, message_id, role, "
                "content, sender_name, conversation_id, timestamp, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    DATE,
                    "claude",
                    "localhost",
                    msg_id,
                    "user",
                    content,
                    sender,
                    conv,
                    f"{DATE}T10:00:00",
                    tenant_id,
                ),
            )
        conn.commit()

    service = MessageService(message_repo=MessageRepository(db=tmp_db))
    MessageService.get_all_senders.cache_clear()

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.register_blueprint(messages_bp, url_prefix="/api")

    with patch("app.routes.messages.message_service", service):
        test_client = app.test_client()
        test_client.set_cookie("session_token", "test-token")
        yield test_client

    MessageService.get_all_senders.cache_clear()


def _get(client, path, user):
    with patch("app.auth.decorators._load_user_from_token", return_value=dict(user)):
        return client.get(path)


class TestSendersTenantScope:
    def test_tenant_user_sees_only_own_tenant_senders(self, client):
        resp = _get(client, "/api/senders", TENANT1_USER)
        assert resp.status_code == 200
        assert resp.get_json() == ["alice", "bob"]

    def test_platform_admin_sees_all_senders(self, client):
        resp = _get(client, "/api/senders", PLATFORM_ADMIN)
        assert resp.status_code == 200
        assert resp.get_json() == ["alice", "bob", "carol"]

    def test_tenant_admin_is_scoped_not_global(self, client):
        """``tenant_admin`` is in ADMIN_ROLES but must not get the global list."""
        resp = _get(client, "/api/senders", TENANT2_ADMIN)
        assert resp.status_code == 200
        assert resp.get_json() == ["carol"]

    def test_cache_does_not_bleed_across_tenants(self, client):
        # Warm the cache with the global list first -- the old module-global
        # cache would then have served it to every later caller.
        assert _get(client, "/api/senders", PLATFORM_ADMIN).get_json() == [
            "alice",
            "bob",
            "carol",
        ]
        assert _get(client, "/api/senders", TENANT1_USER).get_json() == ["alice", "bob"]
        assert _get(client, "/api/senders", TENANT2_ADMIN).get_json() == ["carol"]
        # And the other direction: a tenant entry must not be served to the admin.
        assert _get(client, "/api/senders", PLATFORM_ADMIN).get_json() == [
            "alice",
            "bob",
            "carol",
        ]

    def test_host_filter_is_tenant_scoped_too(self, client):
        resp = _get(client, "/api/senders?host=localhost", TENANT2_ADMIN)
        assert resp.get_json() == ["carol"]


class TestSiblingEndpointsTenantScope:
    def test_messages_list_only_own_tenant(self, client):
        body = _get(client, f"/api/messages?date={DATE}", TENANT1_USER).get_json()
        assert {m["content"] for m in body["messages"]} == {
            "tenant one secret A",
            "tenant one secret B",
        }
        assert body["total"] == 2

    def test_messages_list_admin_global(self, client):
        body = _get(client, f"/api/messages?date={DATE}", PLATFORM_ADMIN).get_json()
        assert body["total"] == 3

    def test_messages_count_scoped(self, client):
        assert _get(client, f"/api/messages/count?date={DATE}", TENANT2_ADMIN).get_json() == {
            "count": 1
        }
        assert _get(client, f"/api/messages/count?date={DATE}", PLATFORM_ADMIN).get_json() == {
            "count": 3
        }

    def test_conversation_history_scoped(self, client):
        # Conversations are grouped per sender, so conv-t1 yields two rows.
        body = _get(client, f"/api/conversation-history?date={DATE}", TENANT1_USER).get_json()
        assert {c["sender_name"] for c in body["data"]} == {"alice", "bob"}
        assert body["total"] == 2
        admin = _get(client, f"/api/conversation-history?date={DATE}", PLATFORM_ADMIN).get_json()
        assert {c["sender_name"] for c in admin["data"]} == {"alice", "bob", "carol"}
        assert admin["total"] == 3

    def test_timeline_of_other_tenant_conversation_is_empty(self, client):
        assert _get(client, "/api/conversation-timeline/conv-t2", TENANT1_USER).get_json() == []
        own = _get(client, "/api/conversation-timeline/conv-t2", TENANT2_ADMIN).get_json()
        assert [m["content"] for m in own] == ["tenant two secret C"]
        admin = _get(client, "/api/conversation-timeline/conv-t2", PLATFORM_ADMIN).get_json()
        assert len(admin) == 1

    def test_details_of_other_tenant_conversation_is_404(self, client):
        assert _get(client, "/api/conversation-details/conv-t2", TENANT1_USER).status_code == 404
        own = _get(client, "/api/conversation-details/conv-t2", TENANT2_ADMIN)
        assert own.status_code == 200
        assert own.get_json()["sender_name"] == "carol"
        assert _get(client, "/api/conversation-details/conv-t2", PLATFORM_ADMIN).status_code == 200


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_non_admin_without_tenant_is_denied(client, path):
    resp = _get(client, path, NO_TENANT_USER)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "Tenant scope required"}
