"""Tests for issue #3019: Feishu org sync must not skip root department users.

The bug: _fetch_directory_snapshot() had a ``continue`` statement that skipped
calling _fetch_department_users() when processing the root department (ID="0"),
causing all users directly under the root department to be omitted from the
sync snapshot.

Fix: removed the ``if current_department_id == FEISHU_ROOT_DEPARTMENT_ID: continue``
guard so that root department users are fetched like any other department.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.feishu_org_sync import (
    FEISHU_ROOT_DEPARTMENT_ID,
    FeishuDepartment,
    FeishuOrgSyncService,
    FeishuUser,
)


def _make_service() -> FeishuOrgSyncService:
    """Create a minimal FeishuOrgSyncService with mocked dependencies."""
    service = FeishuOrgSyncService.__new__(FeishuOrgSyncService)
    service.db = MagicMock()
    service.user_repo = MagicMock()
    service.sso_manager = MagicMock()
    service.collaboration_manager = MagicMock()
    service.config_override = None
    service.http = MagicMock()
    service._token_cache = {}
    service._active_app_id = None
    service._active_app_secret = None
    return service


@pytest.mark.issue(3019)
@pytest.mark.regression
class TestFetchDirectorySnapshotRootUsers:
    """Verify that _fetch_directory_snapshot includes root department users."""

    def test_root_department_only_no_children(self):
        """Issue #3019: Root department with users but no sub-departments
        must return users_seen > 0 (previously returned 0)."""
        service = _make_service()
        root_users = [
            FeishuUser(
                open_id="ou_root_user1",
                name="Root User One",
                email="root1@example.com",
                department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
            ),
        ]

        with (
            patch.object(
                service,
                "_fetch_child_departments",
                return_value=[],
            ),
            patch.object(
                service,
                "_fetch_department_users",
                return_value=root_users,
            ),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        assert len(users) == 1
        assert users[0].open_id == "ou_root_user1"
        assert users[0].department_ids == [FEISHU_ROOT_DEPARTMENT_ID]
        # Root department itself should NOT be in the departments list
        # (only child departments are added)
        assert len(departments) == 0

    def test_root_and_child_departments_both_have_users(self):
        """Users in both root and child departments must all be synced."""
        service = _make_service()

        root_users = [
            FeishuUser(
                open_id="ou_root_alice",
                name="Alice Root",
                email="alice@example.com",
                department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
            ),
        ]
        child_dept = FeishuDepartment(
            department_id="dep-eng",
            name="Engineering",
            parent_department_id=FEISHU_ROOT_DEPARTMENT_ID,
        )
        child_users = [
            FeishuUser(
                open_id="ou_eng_bob",
                name="Bob Eng",
                email="bob@example.com",
                department_ids=["dep-eng"],
            ),
        ]

        def fake_fetch_children(token, dept_id):
            if dept_id == FEISHU_ROOT_DEPARTMENT_ID:
                return [child_dept]
            return []

        def fake_fetch_users(token, dept_id, **kwargs):
            if dept_id == FEISHU_ROOT_DEPARTMENT_ID:
                return root_users
            if dept_id == "dep-eng":
                return child_users
            return []

        with (
            patch.object(service, "_fetch_child_departments", side_effect=fake_fetch_children),
            patch.object(service, "_fetch_department_users", side_effect=fake_fetch_users),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        assert len(departments) == 1
        assert departments[0].department_id == "dep-eng"
        assert len(users) == 2
        open_ids = {u.open_id for u in users}
        assert "ou_root_alice" in open_ids
        assert "ou_eng_bob" in open_ids

    def test_multi_level_hierarchy_with_root_users(self):
        """Deep hierarchy: root -> eng -> backend, with users at each level."""
        service = _make_service()

        eng_dept = FeishuDepartment(
            department_id="dep-eng",
            name="Engineering",
            parent_department_id=FEISHU_ROOT_DEPARTMENT_ID,
        )
        backend_dept = FeishuDepartment(
            department_id="dep-backend",
            name="Backend",
            parent_department_id="dep-eng",
        )

        root_users = [
            FeishuUser(
                open_id="ou_root_ceo",
                name="CEO",
                email="ceo@example.com",
                department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
            ),
        ]
        eng_users = [
            FeishuUser(
                open_id="ou_eng_vp",
                name="VP Engineering",
                email="vp@example.com",
                department_ids=["dep-eng"],
            ),
        ]
        backend_users = [
            FeishuUser(
                open_id="ou_dev_charlie",
                name="Charlie Dev",
                email="charlie@example.com",
                department_ids=["dep-backend"],
            ),
        ]

        def fake_fetch_children(token, dept_id):
            if dept_id == FEISHU_ROOT_DEPARTMENT_ID:
                return [eng_dept]
            if dept_id == "dep-eng":
                return [backend_dept]
            return []

        def fake_fetch_users(token, dept_id, **kwargs):
            mapping = {
                FEISHU_ROOT_DEPARTMENT_ID: root_users,
                "dep-eng": eng_users,
                "dep-backend": backend_users,
            }
            return mapping.get(dept_id, [])

        with (
            patch.object(service, "_fetch_child_departments", side_effect=fake_fetch_children),
            patch.object(service, "_fetch_department_users", side_effect=fake_fetch_users),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        assert len(departments) == 2
        assert len(users) == 3
        open_ids = {u.open_id for u in users}
        assert open_ids == {"ou_root_ceo", "ou_eng_vp", "ou_dev_charlie"}

    def test_user_in_root_and_child_is_deduplicated(self):
        """A user who belongs to both root and a child department should appear
        only once with merged department_ids."""
        service = _make_service()

        child_dept = FeishuDepartment(
            department_id="dep-eng",
            name="Engineering",
            parent_department_id=FEISHU_ROOT_DEPARTMENT_ID,
        )
        # Same user appears under root and under child
        user_as_root = FeishuUser(
            open_id="ou_multi_dept",
            name="Multi Dept",
            email="multi@example.com",
            department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
        )
        user_as_child = FeishuUser(
            open_id="ou_multi_dept",
            name="Multi Dept",
            email="multi@example.com",
            department_ids=["dep-eng"],
        )

        def fake_fetch_children(token, dept_id):
            if dept_id == FEISHU_ROOT_DEPARTMENT_ID:
                return [child_dept]
            return []

        def fake_fetch_users(token, dept_id, **kwargs):
            if dept_id == FEISHU_ROOT_DEPARTMENT_ID:
                return [user_as_root]
            if dept_id == "dep-eng":
                return [user_as_child]
            return []

        with (
            patch.object(service, "_fetch_child_departments", side_effect=fake_fetch_children),
            patch.object(service, "_fetch_department_users", side_effect=fake_fetch_users),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        assert len(users) == 1
        assert users[0].open_id == "ou_multi_dept"
        # department_ids should be merged and sorted
        assert FEISHU_ROOT_DEPARTMENT_ID in users[0].department_ids
        assert "dep-eng" in users[0].department_ids

    def test_empty_root_no_users_no_children(self):
        """Empty root (no users, no children) should return empty lists."""
        service = _make_service()

        with (
            patch.object(service, "_fetch_child_departments", return_value=[]),
            patch.object(service, "_fetch_department_users", return_value=[]),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        assert len(departments) == 0
        assert len(users) == 0

    def test_root_users_sync_idempotent(self):
        """Calling _fetch_directory_snapshot twice should yield the same result."""
        service = _make_service()

        root_users = [
            FeishuUser(
                open_id="ou_root_idem",
                name="Idem User",
                email="idem@example.com",
                department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
            ),
        ]

        with (
            patch.object(service, "_fetch_child_departments", return_value=[]),
            patch.object(service, "_fetch_department_users", return_value=root_users),
        ):
            deps1, users1 = service._fetch_directory_snapshot("fake-token")

        with (
            patch.object(service, "_fetch_child_departments", return_value=[]),
            patch.object(service, "_fetch_department_users", return_value=root_users),
        ):
            deps2, users2 = service._fetch_directory_snapshot("fake-token")

        assert len(deps1) == len(deps2) == 0
        assert len(users1) == len(users2) == 1
        assert users1[0].open_id == users2[0].open_id

    def test_root_department_not_in_departments_list(self):
        """Root department should NOT appear in the returned departments list
        (no Team created for root), even though its users are fetched."""
        service = _make_service()

        root_users = [
            FeishuUser(
                open_id="ou_root_only",
                name="Root Only",
                email="rootonly@example.com",
                department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
            ),
        ]

        with (
            patch.object(service, "_fetch_child_departments", return_value=[]),
            patch.object(service, "_fetch_department_users", return_value=root_users),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        # Root department itself is never added to the departments dict
        assert len(departments) == 0
        dep_ids = [d.department_id for d in departments]
        assert FEISHU_ROOT_DEPARTMENT_ID not in dep_ids
        # But users ARE fetched
        assert len(users) == 1

    def test_multiple_root_users(self):
        """Multiple users directly under root department are all fetched."""
        service = _make_service()

        root_users = [
            FeishuUser(
                open_id=f"ou_root_{i}",
                name=f"Root User {i}",
                email=f"root{i}@example.com",
                department_ids=[FEISHU_ROOT_DEPARTMENT_ID],
            )
            for i in range(5)
        ]

        with (
            patch.object(service, "_fetch_child_departments", return_value=[]),
            patch.object(service, "_fetch_department_users", return_value=root_users),
        ):
            departments, users = service._fetch_directory_snapshot("fake-token")

        assert len(users) == 5
        assert len(departments) == 0
