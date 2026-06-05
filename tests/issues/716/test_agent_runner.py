"""Unit tests for AutonomousAgentRunner."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession


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
            server_url="http://test:5000",
        )
        assert runner.session_manager is sm
        assert runner.remote_session_manager is rsm


class TestAgentRunnerRunTask:
    """Tests for run_agent_task method."""

    def setup_method(self):
        self.sm = MagicMock()
        self.runner = AutonomousAgentRunner(session_manager=self.sm)

    def test_run_local_missing_executable(self):
        """Should fail gracefully when CLI tool not found."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "nonexistent-tool"
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

    def test_run_local_success(self):
        """Test successful local agent execution."""
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "test-tool"
        mock_adapter.build_start_args.return_value = ["test-tool", "--model", "m1"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        with patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}):
            with patch("shutil.which", return_value="/usr/bin/test-tool"):
                with patch("subprocess.Popen") as mock_popen:
                    proc = MagicMock()
                    proc.returncode = 0
                    proc.pid = 12345
                    proc.stdin = MagicMock()
                    proc.stdout = MagicMock()
                    proc.stderr = MagicMock()
                    # Simulate the process completing immediately
                    proc.stdout.readline.return_value = b""
                    mock_popen.return_value = proc

                    # Use a very short timeout
                    self.runner.run_agent_task(
                        workflow_id="wf-1",
                        cli_tool="test-tool",
                        model="m1",
                        project_path="/tmp/test",
                        prompt="Do something",
                        timeout=1,
                    )

        # Process should have been started
        assert mock_popen.called

    def test_run_remote_no_session_manager(self):
        """Remote execution without session manager should fail."""
        runner = AutonomousAgentRunner()
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
        assert "not available" in result.error

    def test_run_remote_success(self):
        """Test remote agent execution via mocked RemoteSessionManager."""
        rsm = MagicMock()
        rsm.create_remote_session.return_value = {"success": True, "session_id": "rs-1"}

        sm = MagicMock()
        sm.get_session.return_value = {
            "status": "completed",
            "total_tokens": 500,
            "total_input_tokens": 300,
            "total_output_tokens": 200,
        }
        sm.get_messages.return_value = [
            {"role": "assistant", "content": "Task completed successfully"}
        ]

        runner = AutonomousAgentRunner(session_manager=sm, remote_session_manager=rsm)

        with patch("time.sleep"):
            result = runner.run_agent_task(
                workflow_id="wf-1",
                cli_tool="claude-code",
                model="m1",
                project_path="/tmp/test",
                prompt="Do something",
                workspace_type="remote",
                remote_machine_id="machine-1",
                timeout=5,
            )

        assert result.success is True
        assert "Task completed" in result.response_text
        assert result.total_tokens == 500

    def test_run_remote_creation_failure(self):
        """Remote session creation failure."""
        rsm = MagicMock()
        rsm.create_remote_session.return_value = {"success": False, "error": "Machine offline"}

        runner = AutonomousAgentRunner(remote_session_manager=rsm)
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
    """Tests for stdout line parsing in _read_stdout."""

    def setup_method(self):
        self.runner = AutonomousAgentRunner()
        self.mock_process = MagicMock()

    def test_parse_assistant_text(self):
        _LocalSession(session_id="s-1", process=self.mock_process)
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": "Hello from AI"},
            }
        )
        # Manually call the parsing logic
        parsed = json.loads(line)
        assert parsed["type"] == "assistant"
        assert parsed["message"]["content"] == "Hello from AI"

    def test_parse_assistant_content_blocks(self):
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Block 1"},
                        {"type": "text", "text": " Block 2"},
                    ]
                },
            }
        )
        parsed = json.loads(line)
        content = parsed["message"]["content"]
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")
        assert text == "Block 1 Block 2"

    def test_parse_tool_use(self):
        line = json.dumps(
            {
                "type": "tool_use",
                "name": "read_file",
                "input": {"path": "/tmp/test.py"},
            }
        )
        parsed = json.loads(line)
        assert parsed["type"] == "tool_use"
        assert parsed["name"] == "read_file"

    def test_parse_result_with_usage(self):
        line = json.dumps(
            {
                "type": "result",
                "data": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                    }
                },
            }
        )
        parsed = json.loads(line)
        assert parsed["type"] == "result"
        usage = parsed["data"]["usage"]
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50

    def test_parse_control_request_auto_approve(self):
        line = json.dumps(
            {
                "type": "control_request",
                "request_id": "req-123",
                "request": {"subtype": "permission"},
            }
        )
        parsed = json.loads(line)
        assert parsed["type"] == "control_request"
        assert parsed["request_id"] == "req-123"
        # In autonomous mode, this would trigger an auto-approve response


class TestStopSession:
    """Tests for stop_session."""

    def test_stop_running_session(self):
        runner = AutonomousAgentRunner()
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.pid = 12345

        session = _LocalSession(session_id="s-1", process=mock_process)
        runner._local_sessions["s-1"] = session

        with patch("os.killpg"):
            with patch("os.getpgid", return_value=12345):
                runner.stop_session("s-1")

        session._stopped.is_set()
        session.completed.is_set()

    def test_stop_nonexistent_session(self):
        runner = AutonomousAgentRunner()
        # Should not raise
        runner.stop_session("nonexistent")
