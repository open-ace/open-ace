"""Agent task resource-policy config parsing (Issue #2020 Phase A).

``/etc/openace/agent-launcher.conf`` carries the per-task isolation knobs:
the runtime tree root, the cgroup root, the resource limits (memory/pids/CPU),
the cgroup enablement mode, and the scheduler concurrency cap. The launcher
(bash) and the scheduler (Python) must read the same file, so the parsing
contract is pinned here.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy, read_agent_task_policy


def _write_conf(tmp_path, body: str):
    p = tmp_path / "agent-launcher.conf"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_defaults_when_conf_absent(tmp_path):
    policy = read_agent_task_policy(str(tmp_path / "missing.conf"))
    assert isinstance(policy, AgentTaskPolicy)
    assert policy.task_root == "/run/openace-agent-tasks"
    assert policy.cgroup_root == "/sys/fs/cgroup/openace-agent"
    assert policy.cgroup_enabled == "auto"
    assert policy.memory_max_bytes == 0  # 0 = unset/no limit
    assert policy.pids_max == 0
    assert policy.cpu_max == ""
    assert policy.max_concurrent_workflows == 3


def test_parses_resource_keys(tmp_path):
    conf = _write_conf(
        tmp_path,
        "\n".join(
            [
                "# agent-launcher.conf",
                "agent_account=openace-agent",
                "workspace_root=/home/openace-workspaces",
                "agent_task_root=/custom/tasks",
                "agent_task_cgroup_root=/sys/fs/cgroup/openace-agent",
                "agent_task_cgroup_enabled=on",
                "agent_task_memory_max_bytes=2147483648",
                "agent_task_pids_max=512",
                'agent_task_cpu_max="200000 100000"',
                "agent_max_concurrent_workflows=5",
            ]
        ),
    )
    policy = read_agent_task_policy(conf)
    assert policy.task_root == "/custom/tasks"
    assert policy.cgroup_enabled == "on"
    assert policy.memory_max_bytes == 2147483648
    assert policy.pids_max == 512
    assert policy.cpu_max == "200000 100000"
    assert policy.max_concurrent_workflows == 5


def test_ignores_malformed_lines_and_comments(tmp_path):
    conf = _write_conf(
        tmp_path,
        "\n".join(
            [
                "# comment",
                "",
                "agent_task_pids_max=not-an-int",
                "   agent_task_memory_max_bytes=1024   ",
                "no_equals_here",
                "agent_task_cgroup_enabled=OFF",
            ]
        ),
    )
    policy = read_agent_task_policy(conf)
    # malformed int falls back to default (0)
    assert policy.pids_max == 0
    # value is trimmed
    assert policy.memory_max_bytes == 1024
    assert policy.cgroup_enabled == "off"


def test_cgroup_enabled_normalizes_and_rejects_unknown(tmp_path):
    for raw, expected in (
        ("auto", "auto"),
        ("ON", "on"),
        ("TRUE", "on"),
        ("yes", "on"),
        ("off", "off"),
        ("0", "off"),
        ("maybe", "auto"),
    ):
        conf = _write_conf(tmp_path, f"agent_task_cgroup_enabled={raw}")
        assert read_agent_task_policy(conf).cgroup_enabled == expected
