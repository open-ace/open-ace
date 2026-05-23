"""Unit tests for Project, UserProject, ProjectStats, and ProjectDailyStats models."""

from datetime import datetime

import pytest

from app.models.project import Project, ProjectDailyStats, ProjectStats, UserProject


class TestProject:
    """Test Project dataclass."""

    def test_create_with_defaults(self):
        p = Project()
        assert p.id is None
        assert p.path == ""
        assert p.name is None
        assert p.description is None
        assert p.created_by is None
        assert p.created_at is None
        assert p.updated_at is None
        assert p.is_active is True
        assert p.is_shared is False

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        p = Project(
            id=1,
            path="/home/user/myproject",
            name="My Project",
            description="A test project",
            created_by=42,
            created_at=now,
            updated_at=now,
            is_active=True,
            is_shared=True,
        )
        assert p.id == 1
        assert p.path == "/home/user/myproject"
        assert p.name == "My Project"
        assert p.description == "A test project"
        assert p.created_by == 42
        assert p.created_at == now
        assert p.updated_at == now
        assert p.is_active is True
        assert p.is_shared is True

    def test_get_display_name_with_name(self):
        p = Project(name="Custom Name", path="/some/path")
        assert p.get_display_name() == "Custom Name"

    def test_get_display_name_falls_back_to_path_basename(self):
        p = Project(path="/home/user/my-project")
        assert p.get_display_name() == "my-project"

    def test_get_display_name_empty_string_name_falls_back(self):
        p = Project(name="", path="/home/user/fallback-name")
        # empty string is falsy, so falls back to path
        assert p.get_display_name() == "fallback-name"

    def test_get_display_name_windows_style_path(self):
        p = Project(path="C:\\Users\\dev\\myproject")
        assert p.get_display_name() == "myproject"

    def test_get_display_name_trailing_slash(self):
        p = Project(path="/home/user/myproject/")
        assert p.get_display_name() == "myproject"

    def test_get_display_name_trailing_backslash(self):
        p = Project(path="C:\\Users\\dev\\myproject\\")
        assert p.get_display_name() == "myproject"

    def test_get_display_name_no_name_no_path(self):
        p = Project()
        assert p.get_display_name() == "Unnamed Project"

    def test_to_dict(self):
        now = datetime(2025, 3, 1, 12, 0, 0)
        p = Project(
            id=5,
            path="/proj/a",
            name="ProjA",
            created_by=1,
            created_at=now,
            is_active=True,
            is_shared=False,
        )
        d = p.to_dict()
        assert d["id"] == 5
        assert d["path"] == "/proj/a"
        assert d["name"] == "ProjA"
        assert d["description"] is None
        assert d["created_by"] == 1
        assert d["created_at"] == "2025-03-01T12:00:00"
        assert d["updated_at"] is None
        assert d["is_active"] is True
        assert d["is_shared"] is False

    def test_from_dict_with_all_fields(self):
        data = {
            "id": 10,
            "path": "/home/dev/app",
            "name": "App",
            "description": "Main app",
            "created_by": 7,
            "created_at": "2025-07-20T09:30:00",
            "updated_at": "2025-07-21T10:00:00",
            "is_active": False,
            "is_shared": True,
        }
        p = Project.from_dict(data)
        assert p.id == 10
        assert p.path == "/home/dev/app"
        assert p.name == "App"
        assert p.description == "Main app"
        assert p.created_by == 7
        assert p.created_at == datetime(2025, 7, 20, 9, 30, 0)
        assert p.updated_at == datetime(2025, 7, 21, 10, 0, 0)
        assert p.is_active is False
        assert p.is_shared is True

    def test_from_dict_with_defaults(self):
        data = {}
        p = Project.from_dict(data)
        assert p.id is None
        assert p.path == ""
        assert p.name is None
        assert p.is_active is True
        assert p.is_shared is False

    def test_from_dict_datetime_as_datetime_object(self):
        now = datetime(2025, 8, 1, 14, 0, 0)
        data = {"created_at": now, "updated_at": now}
        p = Project.from_dict(data)
        assert p.created_at == now
        assert p.updated_at == now

    def test_from_dict_datetime_none(self):
        data = {"created_at": None, "updated_at": None}
        p = Project.from_dict(data)
        assert p.created_at is None
        assert p.updated_at is None

    def test_roundtrip_to_dict_from_dict(self):
        now = datetime(2025, 9, 1, 8, 0, 0)
        original = Project(
            id=3,
            path="/proj/x",
            name="X",
            description="Desc",
            created_by=2,
            created_at=now,
            updated_at=now,
            is_active=True,
            is_shared=False,
        )
        d = original.to_dict()
        restored = Project.from_dict(d)
        assert restored.id == original.id
        assert restored.path == original.path
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.created_by == original.created_by
        assert restored.is_active == original.is_active
        assert restored.is_shared == original.is_shared


class TestUserProject:
    """Test UserProject dataclass."""

    def test_create_with_defaults(self):
        up = UserProject()
        assert up.id is None
        assert up.user_id == 0
        assert up.project_id == 0
        assert up.first_access_at is None
        assert up.last_access_at is None
        assert up.total_sessions == 0
        assert up.total_tokens == 0
        assert up.total_requests == 0
        assert up.total_duration_seconds == 0

    def test_create_with_values(self):
        now = datetime(2025, 6, 1, 10, 0, 0)
        up = UserProject(
            id=1,
            user_id=5,
            project_id=10,
            first_access_at=now,
            last_access_at=now,
            total_sessions=20,
            total_tokens=5000,
            total_requests=100,
            total_duration_seconds=7200,
        )
        assert up.id == 1
        assert up.user_id == 5
        assert up.project_id == 10
        assert up.total_sessions == 20
        assert up.total_tokens == 5000
        assert up.total_requests == 100
        assert up.total_duration_seconds == 7200

    def test_get_duration_hours(self):
        up = UserProject(total_duration_seconds=3600)
        assert up.get_duration_hours() == 1.0

    def test_get_duration_hours_fractional(self):
        up = UserProject(total_duration_seconds=5400)
        assert up.get_duration_hours() == 1.5

    def test_get_duration_hours_zero(self):
        up = UserProject(total_duration_seconds=0)
        assert up.get_duration_hours() == 0.0

    def test_to_dict(self):
        now = datetime(2025, 5, 10, 8, 30, 0)
        up = UserProject(
            id=2,
            user_id=3,
            project_id=7,
            first_access_at=now,
            total_sessions=5,
            total_tokens=1000,
            total_requests=50,
            total_duration_seconds=1800,
        )
        d = up.to_dict()
        assert d["id"] == 2
        assert d["user_id"] == 3
        assert d["project_id"] == 7
        assert d["first_access_at"] == "2025-05-10T08:30:00"
        assert d["last_access_at"] is None
        assert d["total_sessions"] == 5
        assert d["total_tokens"] == 1000
        assert d["total_requests"] == 50
        assert d["total_duration_seconds"] == 1800

    def test_from_dict(self):
        data = {
            "id": 4,
            "user_id": 8,
            "project_id": 12,
            "first_access_at": "2025-04-01T09:00:00",
            "last_access_at": "2025-04-02T10:00:00",
            "total_sessions": 15,
            "total_tokens": 3000,
            "total_requests": 80,
            "total_duration_seconds": 7200,
        }
        up = UserProject.from_dict(data)
        assert up.id == 4
        assert up.user_id == 8
        assert up.project_id == 12
        assert up.first_access_at == datetime(2025, 4, 1, 9, 0, 0)
        assert up.last_access_at == datetime(2025, 4, 2, 10, 0, 0)
        assert up.total_sessions == 15
        assert up.total_duration_seconds == 7200

    def test_from_dict_defaults(self):
        up = UserProject.from_dict({})
        assert up.id is None
        assert up.user_id == 0
        assert up.project_id == 0
        assert up.total_sessions == 0


class TestProjectStats:
    """Test ProjectStats dataclass."""

    def test_create_with_required_fields(self):
        ps = ProjectStats(project_id=1, project_path="/proj/a")
        assert ps.project_id == 1
        assert ps.project_path == "/proj/a"
        assert ps.project_name is None
        assert ps.total_users == 0
        assert ps.total_sessions == 0
        assert ps.total_tokens == 0
        assert ps.total_requests == 0
        assert ps.total_duration_seconds == 0
        assert ps.first_access is None
        assert ps.last_access is None
        assert ps.user_stats == []

    def test_create_with_values(self):
        now = datetime(2025, 1, 15, 10, 0, 0)
        up = UserProject(user_id=1, project_id=1, total_sessions=3)
        ps = ProjectStats(
            project_id=1,
            project_path="/proj/b",
            project_name="ProjB",
            total_users=5,
            total_sessions=100,
            total_tokens=50000,
            total_requests=500,
            total_duration_seconds=36000,
            first_access=now,
            last_access=now,
            user_stats=[up],
        )
        assert ps.project_name == "ProjB"
        assert ps.total_users == 5
        assert ps.total_sessions == 100
        assert ps.total_tokens == 50000
        assert len(ps.user_stats) == 1

    def test_get_duration_hours(self):
        ps = ProjectStats(project_id=1, project_path="/p", total_duration_seconds=7200)
        assert ps.get_duration_hours() == 2.0

    def test_to_dict(self):
        now = datetime(2025, 3, 20, 14, 0, 0)
        up = UserProject(user_id=1, project_id=1, total_duration_seconds=3600)
        ps = ProjectStats(
            project_id=1,
            project_path="/proj/c",
            project_name="C",
            total_users=3,
            total_duration_seconds=10800,
            first_access=now,
            user_stats=[up],
        )
        d = ps.to_dict()
        assert d["project_id"] == 1
        assert d["project_path"] == "/proj/c"
        assert d["project_name"] == "C"
        assert d["total_users"] == 3
        assert d["total_duration_hours"] == 3.0
        assert d["first_access"] == "2025-03-20T14:00:00"
        assert d["last_access"] is None
        assert len(d["user_stats"]) == 1


class TestProjectDailyStats:
    """Test ProjectDailyStats dataclass."""

    def test_create_with_required_fields(self):
        pds = ProjectDailyStats(date="2025-01-01", project_id=1, project_path="/proj/a")
        assert pds.date == "2025-01-01"
        assert pds.project_id == 1
        assert pds.project_path == "/proj/a"
        assert pds.total_tokens == 0
        assert pds.total_input_tokens == 0
        assert pds.total_output_tokens == 0
        assert pds.total_requests == 0
        assert pds.active_users == 0
        assert pds.total_duration_seconds == 0

    def test_create_with_values(self):
        pds = ProjectDailyStats(
            date="2025-06-15",
            project_id=2,
            project_path="/proj/b",
            total_tokens=1000,
            total_input_tokens=400,
            total_output_tokens=600,
            total_requests=50,
            active_users=10,
            total_duration_seconds=14400,
        )
        assert pds.total_tokens == 1000
        assert pds.total_input_tokens == 400
        assert pds.total_output_tokens == 600
        assert pds.total_requests == 50
        assert pds.active_users == 10
        assert pds.total_duration_seconds == 14400

    def test_to_dict(self):
        pds = ProjectDailyStats(
            date="2025-07-20",
            project_id=3,
            project_path="/proj/c",
            total_tokens=2000,
            total_requests=100,
            active_users=5,
            total_duration_seconds=7200,
        )
        d = pds.to_dict()
        assert d["date"] == "2025-07-20"
        assert d["project_id"] == 3
        assert d["project_path"] == "/proj/c"
        assert d["total_tokens"] == 2000
        assert d["total_requests"] == 100
        assert d["active_users"] == 5
        assert d["total_duration_seconds"] == 7200
        assert d["total_duration_hours"] == 2.0

    def test_to_dict_includes_computed_duration_hours(self):
        pds = ProjectDailyStats(
            date="2025-01-01",
            project_id=1,
            project_path="/p",
            total_duration_seconds=1800,
        )
        d = pds.to_dict()
        assert d["total_duration_hours"] == 0.5
