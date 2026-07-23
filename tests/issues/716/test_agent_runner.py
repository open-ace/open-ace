"""Unit tests for AutonomousAgentRunner."""

import json
import os
import queue
import threading
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import (
    AutonomousAgentRunner,
    _extract_cli_result_error,
    _LocalSession,
)
from app.modules.workspace.session_manager import AgentSession


class TestAgentRunnerInit:
    """Tests for agent runner initialization."""

    def test_default_init(self):
        runner = AutonomousAgentRunner()
        assert runner.session_manager is None
        assert runner.remote_session_manager is None
        assert runner._local_sessions == {}

    def test_init_with_managers(self):
        sm = MagicMock()
        rsm = MagicMock()
        runner = AutonomousAgentRunner(
            session_manager=sm,
            remote_session_manager=rsm,
            server_url="http://test:19888",
        )
        assert runner.session_manager is sm
        assert runner.remote_session_manager is rsm


class TestClaudeSessionIdExtraction:
    def test_extract_stream_session_id_accepts_camel_case_nested(self):
        runner = AutonomousAgentRunner()
        parsed = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "response": {
                    "sessionId": "claude-camel-123",
                },
            },
        }

        assert runner._extract_stream_session_id(parsed) == "claude-camel-123"


class TestAgentRunnerRunTask:
    """Tests for run_agent_task method."""

    def setup_method(self):
        self.sm = MagicMock()
        self.runner = AutonomousAgentRunner(session_manager=self.sm)

    def test_run_local_missing_executable(self):
        """Should fail gracefully when CLI tool not found."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "nonexistent-tool"
        # Must explicitly return bool — MagicMock truthiness causes dispatch confusion
        mock_adapter.supports_stdin_input.return_value = True
        mock_adapter.provides_full_command.return_value = False
        mock_adapter.build_start_args.return_value = ["nonexistent-tool"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        with patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}):
            with patch("shutil.which", return_value=None):
                result = self.runner.run_agent_task(
                    workflow_id="wf-1",
                    cli_tool="test-tool",
                    model="test-model",
                    project_path="/tmp/test",
                    prompt="Do something",
                )

        assert result.success is False
        assert "not found" in result.error
        assert result.tracking_session_id

    def test_sidebar_missing_executable_closes_orphan_wrapper(self):
        """When claude-code (sidebar source) fails to start, the eagerly-created
        workflow wrapper row must be closed to status='error' — otherwise it
        stays 'active' forever as a zombie row that leaks into the session list.
        (#1816 orphan wrapper)"""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "claude"
        mock_adapter.supports_stdin_input.return_value = True
        mock_adapter.provides_full_command.return_value = False
        mock_adapter.build_start_args.return_value = ["claude"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        sm = MagicMock()
        runner = AutonomousAgentRunner(session_manager=sm)

        with patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}):
            with patch("shutil.which", return_value=None):
                result = runner.run_agent_task(
                    workflow_id="wf-orphan",
                    cli_tool="claude-code",
                    model="test-model",
                    project_path="/tmp/test",
                    prompt="Do something",
                    workspace_type="local",
                )

        assert result.success is False
        # sidebar source + missing executable → empty session_id (no real CLI
        # session was established)
        assert result.session_id == ""
        assert result.tracking_session_id  # the tracking uuid still present
        # The wrapper row must be closed to status='error' with the failure
        # reason, so it doesn't linger as 'active'.
        status_updates = [
            call
            for call in sm.update_session_fields.call_args_list
            if call.args and len(call.args) >= 2 and call.args[1].get("status") == "error"
        ]
        assert status_updates, "orphan wrapper not closed to status='error'"
        # The closed row is keyed by the tracking uuid, with the error message.
        closed_call = status_updates[0]
        assert (
            closed_call.args[1].get("error_message")
            and "not found" in closed_call.args[1]["error_message"]
        )

    def test_run_local_success(self):
        """Test successful local agent execution — verify return value fields."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "test-tool"
        mock_adapter.build_start_args.return_value = ["test-tool", "--model", "m1"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        # Build stdout lines that simulate a real agent session:
        # assistant text -> tool_use -> result with usage
        stdout_lines = [
            json.dumps({"type": "assistant", "message": {"content": "Hello from agent"}}).encode(),
            json.dumps(
                {
                    "type": "tool_use",
                    "name": "read_file",
                    "input": {"path": "/tmp/test.py"},
                }
            ).encode(),
            json.dumps(
                {
                    "type": "result",
                    "data": {
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                    },
                }
            ).encode(),
            b"",  # EOF
        ]

        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(side_effect=stdout_lines)
        mock_stderr = MagicMock()
        mock_stderr.readline = MagicMock(return_value=b"")

        with patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}):
            with patch("shutil.which", return_value="/usr/bin/test-tool"):
                with patch("subprocess.Popen") as mock_popen:
                    proc = MagicMock()
                    proc.returncode = 0
                    proc.pid = 12345
                    proc.stdin = MagicMock()
                    proc.stdout = mock_stdout
                    proc.stderr = mock_stderr
                    mock_popen.return_value = proc

                    result = self.runner.run_agent_task(
                        workflow_id="wf-1",
                        cli_tool="test-tool",
                        model="m1",
                        project_path="/tmp/test",
                        prompt="Do something",
                        timeout=5,
                    )

        # Verify return value — not just that Popen was called
        assert result.success is True
        assert "Hello from agent" in result.response_text
        assert result.total_tokens == 150
        assert result.total_input_tokens == 100
        assert result.total_output_tokens == 50
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "read_file"

    def test_run_remote_no_session_manager(self):
        """Remote execution without session manager should fail."""
        runner = AutonomousAgentRunner()
        result = runner.run_agent_task(
            workflow_id="wf-1",
            user_id=1,
            cli_tool="claude-code",
            model="m1",
            project_path="/tmp/test",
            prompt="Do something",
            workspace_type="remote",
            remote_machine_id="machine-1",
        )
        assert result.success is False
        assert "not available" in result.error

    def test_run_remote_success(self):
        """Test remote agent execution via mocked RemoteSessionManager."""
        rsm = MagicMock()
        # Concrete RemoteSessionManager returns the payload directly, without
        # a success=True wrapper.
        rsm.create_remote_session.return_value = {"session_id": "rs-1"}

        sm = MagicMock()
        tracking = AgentSession(
            session_id="track-1",
            session_type="workflow",
            context={"workflow_id": "wf-1"},
        )
        remote = AgentSession(
            session_id="rs-1",
            # Reusable remote sidebar sessions remain active after a turn;
            # request completion is carried by the buffered output entry.
            status="active",
            total_tokens=500,
            total_input_tokens=300,
            total_output_tokens=200,
            request_count=2,
        )
        sm.get_session.side_effect = lambda sid: remote if sid == "rs-1" else tracking
        sm.get_messages.return_value = [
            {"role": "assistant", "content": "Task completed successfully"}
        ]

        runner = AutonomousAgentRunner(session_manager=sm, remote_session_manager=rsm)
        rsm.get_session_status.return_value = {
            "status": "active",
            "output": [{"stream": "stdout", "is_complete": True}],
        }

        with patch("time.sleep"):
            result = runner.run_agent_task(
                workflow_id="wf-1",
                user_id=7,
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="Do something",
                workspace_type="remote",
                remote_machine_id="machine-1",
                timeout=5,
                session_id="track-1",
            )

        assert result.success is True
        assert "Task completed" in result.response_text
        assert result.total_tokens == 500
        assert result.session_id == "rs-1"
        rsm.create_remote_session.assert_called_once()
        assert rsm.create_remote_session.call_args.kwargs["user_id"] == 7
        rsm.send_message.assert_called_once_with(
            session_id="rs-1", content="Do something", user_id=7
        )
        sm.get_session.assert_any_call("rs-1")
        assert any(
            call.args[0] == "track-1" and call.args[1].get("cli_session_id") == "rs-1"
            for call in sm.update_session_fields.call_args_list
        )
        # Remote usage reports already own the actual row's counters.
        sm.increment_session_usage.assert_not_called()

    def test_run_remote_requires_user_id_before_creation(self):
        rsm = MagicMock()
        runner = AutonomousAgentRunner(session_manager=MagicMock(), remote_session_manager=rsm)

        result = runner.run_agent_task(
            workflow_id="wf-1",
            cli_tool="claude-code",
            model="m1",
            project_path="/tmp/test",
            prompt="Do something",
            workspace_type="remote",
            remote_machine_id="machine-1",
        )

        assert result.success is False
        assert "User ID" in result.error
        rsm.create_remote_session.assert_not_called()

    def test_remote_abort_failed_event_is_not_successful_turn_completion(self):
        assert not AutonomousAgentRunner._remote_turn_complete(
            {
                "output": [
                    {
                        "stream": "request_state",
                        "is_complete": True,
                        "data": '{"type":"abort_failed"}',
                    }
                ]
            }
        )

    def test_run_remote_creation_failure(self):
        """Remote session creation failure."""
        rsm = MagicMock()
        rsm.create_remote_session.return_value = {"success": False, "error": "Machine offline"}

        runner = AutonomousAgentRunner(session_manager=MagicMock(), remote_session_manager=rsm)
        result = runner.run_agent_task(
            workflow_id="wf-1",
            user_id=1,
            cli_tool="claude-code",
            model="m1",
            project_path="/tmp/test",
            prompt="Do something",
            workspace_type="remote",
            remote_machine_id="machine-1",
        )
        assert result.success is False
        assert "Machine offline" in result.error

    def test_session_record_created(self):
        """Verify session record is created when session_manager is available."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "test-tool"
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        with patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}):
            with patch("shutil.which", return_value=None):
                self.runner.run_agent_task(
                    workflow_id="wf-1",
                    cli_tool="test-tool",
                    model="m1",
                    project_path="/tmp/test",
                    prompt="Do something",
                )

        self.sm.create_session.assert_called_once()

    def test_local_claude_keeps_tracking_session_and_stores_cli_session_mapping(self):
        """Local Claude should keep a workflow tracking session id and store the CLI resume id."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "claude"
        mock_adapter.build_start_args.return_value = ["claude", "--model", "m1"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        stdout_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg-1",
                        "model": "claude-sonnet",
                        "content": "Hello from Claude",
                    },
                }
            ).encode(),
            json.dumps(
                {
                    "type": "result",
                    "data": {
                        "usage": {"input_tokens": 120, "output_tokens": 30},
                    },
                }
            ).encode(),
            b"",
        ]

        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(side_effect=stdout_lines)
        mock_stderr = MagicMock()
        mock_stderr.readline = MagicMock(return_value=b"")

        with (
            patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}),
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.Popen") as mock_popen,
            patch.object(
                self.runner,
                "_find_latest_claude_session_id",
                return_value="real-claude-session",
            ),
        ):
            proc = MagicMock()
            proc.returncode = 0
            proc.pid = 12345
            proc.stdin = MagicMock()
            proc.stdout = mock_stdout
            proc.stderr = mock_stderr
            mock_popen.return_value = proc

            result = self.runner.run_agent_task(
                workflow_id="wf-1",
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="Do something",
                workspace_type="local",
                user_id=42,
                timeout=5,
            )

        assert result.success is True
        assert result.session_id == result.tracking_session_id
        assert result.session_id
        assert result.source_session_id == "real-claude-session"

        create_calls = self.sm.create_session.call_args_list
        assert len(create_calls) == 1
        assert create_calls[0].kwargs["session_id"] == result.session_id
        assert create_calls[0].kwargs["tool_name"] == "claude-code"
        assert create_calls[0].kwargs["user_id"] == 42

        persisted_updates = [
            call.args[0] for call in self.sm.update_session_fields.call_args_list if call.args
        ]
        assert result.session_id in persisted_updates
        context_updates = [
            call.args[1].get("context")
            for call in self.sm.update_session_fields.call_args_list
            if len(call.args) >= 2 and isinstance(call.args[1], dict) and "context" in call.args[1]
        ]
        field_updates = [
            call.args[1]
            for call in self.sm.update_session_fields.call_args_list
            if len(call.args) >= 2 and isinstance(call.args[1], dict)
        ]
        assert any(
            (ctx or {}).get("cli_session_id") == "real-claude-session" for ctx in context_updates
        )
        assert any(
            update.get("cli_session_id") == "real-claude-session" for update in field_updates
        )

    def test_local_claude_waits_for_late_session_detection(self):
        """Late JSONL detection after process completion should still resolve the real CLI session."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "claude"
        mock_adapter.build_start_args.return_value = ["claude", "--model", "m1"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        stdout_lines = [
            json.dumps({"type": "assistant", "message": {"content": "Hello from Claude"}}).encode(),
            json.dumps(
                {
                    "type": "result",
                    "data": {
                        "usage": {"input_tokens": 120, "output_tokens": 30},
                    },
                }
            ).encode(),
            b"",
        ]

        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(side_effect=stdout_lines)
        mock_stderr = MagicMock()
        mock_stderr.readline = MagicMock(return_value=b"")

        with (
            patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}),
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.Popen") as mock_popen,
            patch.object(
                self.runner,
                "_find_latest_claude_session_id",
                side_effect=["", "", "late-claude-session"],
            ),
            patch("time.sleep"),
        ):
            proc = MagicMock()
            proc.returncode = 0
            proc.pid = 12345
            proc.stdin = MagicMock()
            proc.stdout = mock_stdout
            proc.stderr = mock_stderr
            mock_popen.return_value = proc

            result = self.runner.run_agent_task(
                workflow_id="wf-1",
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="Do something",
                workspace_type="local",
                user_id=42,
                timeout=5,
            )

        assert result.success is True
        assert result.source_session_id == "late-claude-session"
        self.sm.create_session.assert_called_once()
        assert self.sm.create_session.call_args.kwargs["session_id"] == result.session_id

    def test_local_claude_captures_session_id_from_initialized_event(self):
        """System initialized events should bind the real Claude session without JSONL fallback."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "claude"
        mock_adapter.build_start_args.return_value = ["claude", "--model", "m1"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        stdout_lines = [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "initialized",
                    "session_id": "claude-init-123",
                }
            ).encode(),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg-1",
                        "model": "claude-sonnet",
                        "content": "Hello from Claude",
                    },
                }
            ).encode(),
            json.dumps(
                {
                    "type": "result",
                    "data": {
                        "usage": {"input_tokens": 120, "output_tokens": 30},
                    },
                }
            ).encode(),
            b"",
        ]

        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(side_effect=stdout_lines)
        mock_stderr = MagicMock()
        mock_stderr.readline = MagicMock(return_value=b"")

        with (
            patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}),
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.Popen") as mock_popen,
            patch.object(self.runner, "_find_latest_claude_session_id") as mock_find_latest,
        ):
            proc = MagicMock()
            proc.returncode = 0
            proc.pid = 12345
            proc.stdin = MagicMock()
            proc.stdout = mock_stdout
            proc.stderr = mock_stderr
            mock_popen.return_value = proc

            result = self.runner.run_agent_task(
                workflow_id="wf-1",
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="Do something",
                workspace_type="local",
                user_id=42,
                timeout=5,
            )

        assert result.success is True
        assert result.source_session_id == "claude-init-123"
        mock_find_latest.assert_not_called()

    def test_local_claude_captures_session_id_from_result_event(self):
        """Result events should bind the real Claude session when init omits it."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "claude"
        mock_adapter.build_start_args.return_value = ["claude", "--model", "m1"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        stdout_lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg-1",
                        "model": "claude-sonnet",
                        "content": "Hello from Claude",
                    },
                }
            ).encode(),
            json.dumps(
                {
                    "type": "result",
                    "session_id": "claude-result-456",
                    "data": {
                        "usage": {"input_tokens": 120, "output_tokens": 30},
                    },
                }
            ).encode(),
            b"",
        ]

        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(side_effect=stdout_lines)
        mock_stderr = MagicMock()
        mock_stderr.readline = MagicMock(return_value=b"")

        with (
            patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}),
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.Popen") as mock_popen,
            patch.object(self.runner, "_find_latest_claude_session_id") as mock_find_latest,
        ):
            proc = MagicMock()
            proc.returncode = 0
            proc.pid = 12345
            proc.stdin = MagicMock()
            proc.stdout = mock_stdout
            proc.stderr = mock_stderr
            mock_popen.return_value = proc

            result = self.runner.run_agent_task(
                workflow_id="wf-1",
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="Do something",
                workspace_type="local",
                user_id=42,
                timeout=5,
            )

        assert result.success is True
        assert result.source_session_id == "claude-result-456"
        mock_find_latest.assert_not_called()

    def test_sidebar_session_not_marked_resolved_when_context_sync_fails(self):
        """A failed tracking-session sync should not leave a ghost persisted session id."""
        self.sm.update_session_fields.side_effect = RuntimeError("db down")
        session = _LocalSession(
            session_id="tracking-1",
            process=MagicMock(),
            cli_tool="claude-code",
            project_path="/tmp/test",
            encoded_project_path=os.path.realpath("/tmp/test").replace("/", "-"),
            workflow_id="wf-1",
            user_id=42,
        )

        with patch.object(
            self.runner,
            "_find_latest_claude_session_id",
            return_value="real-claude-session",
        ):
            resolved = self.runner._ensure_sidebar_session(session)

        assert resolved == ""
        assert session.persisted_session_id == ""
        self.sm.update_session_fields.assert_called()


class TestLocalSession:
    """Tests for _LocalSession dataclass."""

    def test_default_values(self):
        session = _LocalSession(session_id="s-1", process=MagicMock())
        assert session.session_id == "s-1"
        assert session.output_lines == []
        assert session.assistant_text == ""
        assert session.tool_calls == []
        assert session.total_tokens == 0
        assert session.error is None
        assert session.completed.is_set() is False


class TestStdoutParsing:
    """Tests for _read_stdout — exercises the actual method, not json.loads()."""

    def setup_method(self):
        self.runner = AutonomousAgentRunner()
        self.mock_process = MagicMock()

    def _run_read_stdout(self, lines):
        """Helper: create a session with mock stdout containing the given lines, then call _read_stdout."""
        mock_stdout = MagicMock()
        encoded_lines = [l.encode() if isinstance(l, str) else l for l in lines]
        mock_stdout.readline = MagicMock(side_effect=encoded_lines)
        self.mock_process.stdout = mock_stdout
        self.mock_process.returncode = 0

        session = _LocalSession(session_id="s-1", process=self.mock_process)
        self.runner._read_stdout(session)
        return session

    def test_parse_assistant_text_string_content(self):
        """_read_stdout accumulates assistant text from string content."""
        session = self._run_read_stdout(
            [
                json.dumps({"type": "assistant", "message": {"content": "Hello from AI"}}),
                "",
            ]
        )
        assert "Hello from AI" in session.assistant_text

    def test_parse_assistant_content_blocks(self):
        """_read_stdout accumulates assistant text from content block arrays."""
        session = self._run_read_stdout(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "Block 1"},
                                {"type": "text", "text": " Block 2"},
                            ]
                        },
                    }
                ),
                "",
            ]
        )
        assert session.assistant_text == "Block 1 Block 2"

    def test_parse_tool_use(self):
        """_read_stdout records tool_use messages in session.tool_calls."""
        session = self._run_read_stdout(
            [
                json.dumps(
                    {
                        "type": "tool_use",
                        "name": "read_file",
                        "input": {"path": "/tmp/test.py"},
                    }
                ),
                "",
            ]
        )
        assert len(session.tool_calls) == 1
        assert session.tool_calls[0]["name"] == "read_file"
        assert session.tool_calls[0]["input"]["path"] == "/tmp/test.py"

    def test_parse_result_with_usage(self):
        """_read_stdout extracts token usage from result messages and marks completed."""
        session = self._run_read_stdout(
            [
                json.dumps(
                    {
                        "type": "result",
                        "data": {
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 50,
                            }
                        },
                    }
                ),
                "",  # EOF sentinel
            ]
        )
        assert session.completed.is_set()
        assert session.total_tokens == 150
        assert session.total_input_tokens == 100
        assert session.total_output_tokens == 50

    def test_parse_error_result_marks_session_failed(self):
        """Claude ``result.is_error`` must become a failed local session."""
        session = self._run_read_stdout(
            [
                json.dumps(
                    {
                        "type": "result",
                        "is_error": True,
                        "errors": ["No conversation found with session ID: dead-session"],
                    }
                ),
                "",
            ]
        )
        assert session.completed.is_set()
        assert session.error == "No conversation found with session ID: dead-session"
        assert session.error_code == "resume_session_not_found"

    def test_extract_cli_result_error_uses_logged_out_message(self):
        """Authentication failures should be classified from observed CLI output."""
        error_code, error_message = _extract_cli_result_error(
            {"type": "result", "is_error": True},
            "Not logged in · Please run /login",
        )
        assert error_code == "cli_auth_failed"
        assert error_message == "Not logged in · Please run /login"

    def test_parse_control_request_auto_approve(self):
        """_read_stdout auto-approves control_request by writing a response to stdin."""
        self.mock_process.returncode = None  # Process still running
        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(
            side_effect=[
                json.dumps(
                    {
                        "type": "control_request",
                        "request_id": "req-123",
                        "request": {"subtype": "permission"},
                    }
                ).encode(),
                b"",
            ]
        )
        self.mock_process.stdout = mock_stdout

        session = _LocalSession(session_id="s-1", process=self.mock_process)
        self.runner._read_stdout(session)

        # Verify that a response was written to stdin
        stdin_write_calls = self.mock_process.stdin.write.call_args_list
        assert len(stdin_write_calls) > 0
        written = stdin_write_calls[0][0][0]
        response = json.loads(written)
        assert response["type"] == "control_response"
        assert response["response"]["request_id"] == "req-123"

    def test_non_json_lines_ignored(self):
        """_read_stdout silently ignores non-JSON output lines."""
        session = self._run_read_stdout(
            [
                "Some plain text log line",
                json.dumps({"type": "assistant", "message": {"content": "After noise"}}),
                "",
            ]
        )
        assert "After noise" in session.assistant_text
        assert len(session.output_lines) == 2  # Both lines recorded

    def test_empty_lines_skipped(self):
        """_read_stdout skips blank/whitespace-only lines without processing."""
        session = self._run_read_stdout(
            [
                "   ",  # whitespace-only: stripped to "" → skipped
                json.dumps({"type": "assistant", "message": {"content": "Real content"}}),
                "",  # EOF sentinel
            ]
        )
        assert "Real content" in session.assistant_text
        assert len(session.output_lines) == 1  # Only the non-empty JSON line

    def test_parse_assistant_stringified_thinking_json_is_hidden(self):
        """Stringified thinking/tool JSON must not pollute assistant_text."""
        session = self._run_read_stdout(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": '[{"type":"thinking","thinking":"internal scratchpad"}]'
                        },
                    }
                ),
                json.dumps({"type": "assistant", "message": {"content": "Final answer"}}),
                "",
            ]
        )
        assert session.assistant_text == "Final answer"
        assert [e["text"] for e in session.event_log if e.get("type") == "assistant"] == [
            "Final answer"
        ]


class TestStopSession:
    """Tests for stop_session."""

    def test_stop_running_session(self):
        """Stopping a session sets _stopped and completed events."""
        runner = AutonomousAgentRunner()
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.pid = 12345

        session = _LocalSession(session_id="s-1", process=mock_process)
        runner._local_sessions["s-1"] = session

        with patch("os.killpg"):
            with patch("os.getpgid", return_value=12345):
                runner.stop_session("s-1")

        assert session._stopped.is_set(), "_stopped event should be set after stop"
        assert session.completed.is_set(), "completed event should be set after stop"

    def test_stop_nonexistent_session(self):
        runner = AutonomousAgentRunner()
        # Should not raise
        runner.stop_session("nonexistent")

    def test_stop_remote_session_marks_tracker_and_notifies_manager(self):
        remote_manager = MagicMock()
        runner = AutonomousAgentRunner(remote_session_manager=remote_manager)
        session = _LocalSession(
            session_id="remote-1",
            process=None,
            persisted_session_id="remote-actual-1",
        )
        runner._local_sessions["remote-1"] = session

        runner.stop_session("remote-1")

        remote_manager.stop_session.assert_called_once_with("remote-actual-1")
        assert session._stopped.is_set()
        assert session.completed.is_set()

    def test_stop_remote_session_still_drains_when_manager_fails(self):
        remote_manager = MagicMock()
        remote_manager.stop_session.side_effect = RuntimeError("remote unavailable")
        runner = AutonomousAgentRunner(remote_session_manager=remote_manager)
        session = _LocalSession(session_id="remote-2", process=None)
        runner._local_sessions["remote-2"] = session

        runner.stop_session("remote-2")

        assert session._stopped.is_set()
        assert session.completed.is_set()

    def test_shutdown_during_remote_creation_stops_actual_before_prompt(self):
        create_started = threading.Event()
        allow_create_to_finish = threading.Event()
        remote_manager = MagicMock()

        def create_remote_session(**_kwargs):
            create_started.set()
            assert allow_create_to_finish.wait(timeout=2)
            return {"session_id": "remote-actual-race"}

        remote_manager.create_remote_session.side_effect = create_remote_session
        session_manager = MagicMock()
        session_manager.get_session.return_value = AgentSession(
            session_id="remote-actual-race",
            status="active",
        )
        runner = AutonomousAgentRunner(
            session_manager=session_manager,
            remote_session_manager=remote_manager,
        )
        result_box = {}

        def run_remote():
            result_box["result"] = runner._run_remote(
                session_id="tracking-race",
                user_id=8,
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="must not dispatch",
                remote_machine_id="machine-1",
                permission_mode="auto-edit",
                timeout=5,
            )

        worker = threading.Thread(target=run_remote)
        worker.start()
        assert create_started.wait(timeout=2)
        runner.stop_session("tracking-race")
        allow_create_to_finish.set()
        worker.join(timeout=2)

        assert not worker.is_alive()
        assert result_box["result"].success is False
        remote_manager.send_message.assert_not_called()
        assert any(
            call.args[0] == "remote-actual-race"
            for call in remote_manager.stop_session.call_args_list
        )


class TestPersistLocalSessionMessages:
    """Tests for local message persistence metadata."""

    def test_event_log_persists_message_and_tool_metadata(self):
        sm = MagicMock()
        runner = AutonomousAgentRunner(session_manager=sm)

        from app.modules.workspace.autonomous.models import AgentTaskResult

        result = AgentTaskResult(
            event_log=[
                {
                    "type": "assistant",
                    "text": "hello",
                    "message_id": "msg-123",
                    "model": "claude-sonnet",
                },
                {
                    "type": "tool_use",
                    "tool_name": "Read",
                    "tool_input": {"file_path": "app.py"},
                    "tool_use_id": "tool-456",
                },
            ]
        )

        runner._persist_local_session_messages("sess-1", result)

        assert sm.append_transcript_message.call_count == 2
        first_call = sm.append_transcript_message.call_args_list[0].kwargs
        assert first_call["session_id"] == "sess-1"
        assert first_call["role"] == "tool"
        assert first_call["metadata"] == {
            "tool_name": "Read",
            "tool_use_id": "tool-456",
        }
        assert first_call["source"] == "autonomous_local_runner"
        assert first_call["external_message_id"] == "tool-456"

        second_call = sm.append_transcript_message.call_args_list[1].kwargs
        assert second_call["session_id"] == "sess-1"
        assert second_call["role"] == "assistant"
        assert second_call["model"] == "claude-sonnet"
        assert second_call["metadata"] == {"message_id": "msg-123"}
        assert second_call["source"] == "autonomous_local_runner"
        assert second_call["external_message_id"] == "msg-123"

    def test_event_log_persists_only_final_assistant_turn(self):
        sm = MagicMock()
        runner = AutonomousAgentRunner(session_manager=sm)
        from app.modules.workspace.autonomous.models import AgentTaskResult

        result = AgentTaskResult(
            event_log=[
                {"type": "assistant", "text": "Let me inspect the codebase. "},
                {"type": "tool_use", "tool_name": "Read", "tool_input": {"file_path": "a.py"}},
                {"type": "assistant", "text": "## Final Plan\n"},
                {"type": "assistant", "text": "1. Fix the bug"},
            ]
        )

        runner._persist_local_session_messages("sess-1", result)

        assert sm.append_transcript_message.call_count == 2
        first_call = sm.append_transcript_message.call_args_list[0].kwargs
        assert first_call["role"] == "tool"
        second_call = sm.append_transcript_message.call_args_list[1].kwargs
        assert second_call["role"] == "assistant"
        assert second_call["content"] == "## Final Plan\n1. Fix the bug"

    def test_milestone_prompt_is_persisted_as_user_message(self):
        sm = MagicMock()
        runner = AutonomousAgentRunner(session_manager=sm)
        from app.modules.workspace.autonomous.models import AgentTaskResult

        result = AgentTaskResult(
            prompt="Implement the fix",
            event_log=[{"type": "assistant", "text": "done", "message_id": "msg-1"}],
        )

        runner._persist_local_session_messages("sess-1", result, milestone_id="ms-1")

        assert sm.append_transcript_message.call_count == 2
        first_call = sm.append_transcript_message.call_args_list[0].kwargs
        assert first_call["role"] == "user"
        assert first_call["content"] == "Implement the fix"
        assert first_call["milestone_id"] == "ms-1"
        assert first_call["source"] == "autonomous_local_runner"
        assert first_call["external_message_id"] == "phase-prompt:ms-1"
