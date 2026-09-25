"""Issue #3431 (Option 2): container backend of scripts/openace-webui-confine.py.

Pins the root decisions of ``launch --backend container`` without root or
Docker: the exact ``docker run`` command line, the container planning
(policy section, groups, mount-path safety), the root-owned run directory,
the probe's reason tokens, and ``inner``'s stdin environment + supervisor
watchdog input. The real gVisor run is scripts/webui_confine_acceptance.py
with ``--backend container`` on a disposable Docker + runsc host.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.issue(3431)]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "openace-webui-confine.py"
IMAGE = "sha256:" + "a" * 64


@pytest.fixture(scope="module")
def confine():
    spec = importlib.util.spec_from_file_location("openace_webui_confine_c", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _container(confine, image=IMAGE):
    return confine.ContainerPolicy(image, "runsc-openace", "/usr/bin/docker")


def _docker_argv(confine, **overrides):
    kwargs = {
        "container": _container(confine),
        "uid": 3001,
        "gid": 3001,
        "extra_gids": [4000],
        "port": 3150,
        "memory_max": "4G",
        "cpu_quota": 150,
        "tasks_max": 512,
        "home": "/wsbase/alice",
        "shared": "/wsbase/shared",
        "log_dir": "/tmp/qwen-code-webui-7",
        "run_dir": "/run/openace-webui/3001-3150",
        "script": "/usr/local/bin/openace-webui-confine",
        "webui_argv": ["/usr/bin/qwen-code-webui", "--port", "3150"],
    }
    kwargs.update(overrides)
    return confine.build_docker_argv(**kwargs)


# ── docker argv ─────────────────────────────────────────────────────────────


def test_docker_argv_is_fully_pinned(confine):
    argv = _docker_argv(confine)
    joined = " ".join(argv)
    assert argv[:6] == ["/usr/bin/docker", "run", "--rm", "-i", "--init", "--name"]
    for fragment in (
        "--runtime runsc-openace",
        "--network none",
        "--user 3001:3001",
        "--group-add 4000",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--memory 4G --memory-swap 4G",
        "--cpus 1.50",
        "--pids-limit 512",
        "type=bind,src=/wsbase/alice,dst=/wsbase/alice",
        "type=bind,src=/wsbase/shared,dst=/wsbase/shared",
        "type=bind,src=/tmp/qwen-code-webui-7,dst=/tmp/qwen-code-webui-7",
        "type=bind,src=/run/openace-webui/3001-3150,dst=/run/openace,readonly",
        "type=bind,src=/usr/local/bin/openace-webui-confine,dst=/opt/openace/confine.py,readonly",
        "--entrypoint python3",
    ):
        assert fragment in joined, fragment
    image_at = argv.index(IMAGE)
    assert argv[image_at + 1 :] == [
        "-I", "/opt/openace/confine.py", "inner", "--env-stdin", "--watch-supervisor",
        "--port", "3150", "--", "/usr/bin/qwen-code-webui", "--port", "3150",
    ]  # fmt: skip
    # no environment on the command line: it travels on stdin
    assert "-e" not in argv and "--env" not in argv and "--env-file" not in argv


def test_docker_argv_without_shared_root(confine):
    assert "/wsbase/shared" not in " ".join(_docker_argv(confine, shared=None))


@pytest.mark.parametrize("bad", ["/wsbase/a,b", "/wsbase/a=b", "/wsbase/a\nb"])
def test_mount_paths_with_separators_are_refused(confine, bad):
    with pytest.raises(confine.ConfineError, match="cannot be bind-mounted"):
        _docker_argv(confine, home=bad)


# ── policy: container section ───────────────────────────────────────────────


def _load(confine, tmp_path, monkeypatch, data):
    policy_dir = tmp_path / "etc"
    policy_dir.mkdir(mode=0o755)
    policy_dir.chmod(0o755)
    path = policy_dir / "webui-confine.json"
    path.write_text(json.dumps(data))
    path.chmod(0o644)
    for parent in [policy_dir, *policy_dir.parents]:
        if parent.stat().st_mode & 0o022:
            pytest.skip(f"{parent} is group/world-writable on this machine")
    real_fstat, real_lstat = os.fstat, os.lstat

    def _root(result):
        fields = list(result)
        fields[4] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(confine.os, "fstat", lambda fd: _root(real_fstat(fd)))
    monkeypatch.setattr(confine.os, "lstat", lambda p: _root(real_lstat(p)))
    return confine.load_policy(str(path))


@pytest.mark.parametrize(
    "image",
    [IMAGE, "registry.example.com/openace/webui@sha256:" + "0123456789abcdef" * 4],
)
def test_policy_accepts_pinned_images(confine, tmp_path, monkeypatch, image):
    policy = _load(
        confine,
        tmp_path,
        monkeypatch,
        {
            "webui": ["/usr/bin/qwen-code-webui"],
            "container": {"image": image, "runtime": "runsc-openace"},
        },
    )
    assert policy.container == confine.ContainerPolicy(image, "runsc-openace", "/usr/bin/docker")


@pytest.mark.parametrize(
    "container",
    [{"image": "openace/webui:latest", "runtime": "runsc"},  # a tag can be re-pointed
     {"image": "sha256:" + "a" * 63, "runtime": "runsc"},
     {"image": IMAGE, "runtime": "runsc --host-uds=all"},
     {"image": IMAGE, "runtime": "runsc", "docker": "docker"},
     "not-an-object"],
)  # fmt: skip
def test_policy_rejects_unpinned_or_malformed_container(confine, tmp_path, monkeypatch, container):
    with pytest.raises(confine.ConfineError):
        _load(confine, tmp_path, monkeypatch, {"webui": ["/w"], "container": container})


# ── planning ────────────────────────────────────────────────────────────────


@pytest.fixture
def planned_container(confine, monkeypatch):
    entry = pwd.struct_passwd(("alice", "x", 3001, 3001, "", "/wsbase/alice", "/bin/bash"))
    state = {
        "policy": confine.Policy(
            frozenset({"/usr/bin/qwen-code-webui"}),
            "/usr/local/bin:/usr/bin:/bin",
            frozenset(),
            confine.PRIVILEGED_GROUPS,
            _container(confine),
        )
    }
    monkeypatch.setattr(confine, "load_policy", lambda path=None: state["policy"])
    monkeypatch.setattr(confine, "resolve_account", lambda name, denied: entry)
    monkeypatch.setattr(
        confine, "workspace_layout", lambda e, b: ("/wsbase", "/wsbase/alice", "/wsbase/shared")
    )
    monkeypatch.setattr(confine, "validate_log_dir", lambda p, e: p)
    monkeypatch.setattr(
        confine, "account_groups", lambda e: {3001: "alice", 4000: "openace-shared"}
    )
    checked: list[str] = []
    monkeypatch.setattr(confine, "require_root_controlled_executable", checked.append)
    monkeypatch.setattr(confine.shutil, "which", lambda tool, path=None: f"/usr/bin/{tool}")

    def _plan():
        argv = [
            "--account", "alice", "--port", "3150", "--memory-max", "4G", "--cpu-quota", "150",
            "--tasks-max", "512", "--allow", "10.0.0.5:19888",
            "--log-dir", "/tmp/qwen-code-webui-7", "--webui", "/usr/bin/qwen-code-webui",
            "--backend", "container", "--", "--port", "3150",
        ]  # fmt: skip
        return confine.plan_launch(argv, json.dumps({"OPENAI_API_KEY": "tok"}))

    _plan.checked = checked
    _plan.state = state
    return _plan


def test_plan_container_launch(confine, planned_container):
    docker_argv, payload = planned_container()
    assert docker_argv[0] == "/usr/bin/docker"
    group_add = docker_argv.index("--group-add")
    assert docker_argv[group_add + 1] == "4000"
    assert "tok" not in " ".join(docker_argv)
    assert payload["backend"] == "container"
    assert payload["socket_dir"] == "/run/openace-webui/3001-3150"
    assert payload["tasks_max"] == 512
    assert payload["inner_env"]["OPENAI_API_KEY"] == "tok"
    assert payload["inner_env"]["HOME"] == "/wsbase/alice"
    setpriv = payload["setpriv_argv"]
    assert "--init-groups" in setpriv and setpriv[-1] == "supervise"
    # the docker CLI must be root-controlled; the host-side WebUI checks do not apply
    assert planned_container.checked == ["/usr/bin/docker"]


def test_plan_container_requires_the_policy_section(confine, planned_container):
    planned_container.state["policy"] = planned_container.state["policy"]._replace(container=None)
    with pytest.raises(confine.ConfineError, match="no 'container' section"):
        planned_container()


# ── run directory ───────────────────────────────────────────────────────────


def test_prepare_run_dir_creates_a_fresh_private_leaf(confine, tmp_path):
    root = tmp_path / "run"
    leaf = root / "3001-3150"
    confine.prepare_run_dir(str(leaf), os.getuid(), os.getgid(), str(root))
    assert (leaf.stat().st_mode & 0o777) == 0o700
    (leaf / "stale.sock").write_text("x")
    confine.prepare_run_dir(str(leaf), os.getuid(), os.getgid(), str(root))
    assert list(leaf.iterdir()) == []  # leftovers are gone
    # a planted symlink is replaced, never followed
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    leaf.rmdir()
    leaf.symlink_to(victim)
    confine.prepare_run_dir(str(leaf), os.getuid(), os.getgid(), str(root))
    assert (victim / "keep").exists() and not leaf.is_symlink()


def test_prepare_run_dir_refuses_a_writable_root(confine, tmp_path):
    root = tmp_path / "run"
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    with pytest.raises(confine.ConfineError, match="not a directory controlled"):
        confine.prepare_run_dir(str(root / "x"), os.getuid(), os.getgid(), str(root))


# ── probe tokens ────────────────────────────────────────────────────────────


@pytest.fixture
def probe(confine, monkeypatch, capsys, tmp_path):
    policy = confine.Policy(frozenset(), "/usr/bin", frozenset(), frozenset(), _container(confine))
    monkeypatch.setattr(confine, "load_policy", lambda path=None: policy)
    monkeypatch.setattr(confine, "require_root_controlled_executable", lambda p: None)
    # production runs as root and hands the probe socket to nobody (65534)
    monkeypatch.setattr(confine.os, "chown", lambda *a, **k: None)
    # AF_UNIX paths are capped (~104 bytes on macOS): keep the run root short
    short_root = tempfile.mkdtemp(prefix="oc3431p-", dir="/tmp")
    monkeypatch.setattr(confine, "RUN_ROOT", short_root)
    monkeypatch.setattr(
        confine, "prepare_run_dir", lambda path, uid, gid, root=None: os.makedirs(path)
    )

    def _run(responses):
        def _fake(argv, **kwargs):
            rc, out = responses.get(argv[1], (0, ""))
            return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")

        monkeypatch.setattr(confine.subprocess, "run", _fake)
        rc = confine.run_container_probe()
        return rc, capsys.readouterr().out.strip().splitlines()[-1]

    yield _run
    shutil.rmtree(short_root, ignore_errors=True)


RUNTIMES = json.dumps({"runsc-openace": {}})


def test_probe_reports_docker_unavailable(probe):
    assert probe({"info": (1, "")}) == (1, "docker:unavailable")


def test_probe_reports_missing_runtime(probe):
    assert probe({"info": (0, json.dumps({"runc": {}}))}) == (1, "runtime:missing")


def test_probe_reports_missing_image(probe):
    assert probe({"info": (0, RUNTIMES), "image": (1, "")}) == (1, "image:missing")


def test_probe_requires_a_gvisor_kernel(probe):
    result = probe({"info": (0, RUNTIMES), "run": (0, "Linux version 6.8.0-generic")})
    assert result == (1, "kernel:unverified")


def test_probe_requires_host_uds(probe):
    # a gVisor kernel, but nothing connected to the probe socket
    result = probe({"info": (0, RUNTIMES), "run": (0, "Linux version 4.19.0-gvisor")})
    assert result == (1, "runtime:no-host-uds")


# ── inner: environment on stdin, supervisor watchdog input ──────────────────


def test_tunnel_health_detects_a_vanished_supervisor(confine, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(confine.time, "monotonic", lambda: clock[0])
    health = confine._TunnelHealth()
    health.connected()
    clock[0] += 100
    assert not health.supervisor_gone(30)  # an idle tunnel is held: alive
    health.released()
    assert not health.supervisor_gone(200)
    assert health.supervisor_gone(30)  # nothing held, no success for 100 s


def test_inner_reads_its_environment_from_stdin(confine, monkeypatch):
    monkeypatch.setattr(confine.sys, "stdin", io.StringIO('{"SECRET_X": "s3"}\n'))
    started = {}

    class _Child:
        def __init__(self, argv, **kwargs):
            started["env"] = kwargs["env"]

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(confine.subprocess, "Popen", _Child)
    monkeypatch.setattr(confine.threading.Thread, "start", lambda self: None)
    monkeypatch.delenv("SECRET_X", raising=False)
    try:
        assert confine.run_inner(["--port", "1", "--env-stdin", "--", "/bin/true"]) == 0
        assert started["env"]["SECRET_X"] == "s3"
        assert started["env"]["NODE_USE_ENV_PROXY"] == "1"
    finally:
        os.environ.pop("SECRET_X", None)


# ── review round 6 nits (shared with the bwrap backend) ─────────────────────


def test_equivalent_ip_spellings_match(confine):
    allow = [confine.parse_allow_entry("[0:0::1]:8080"), confine.parse_allow_entry("10.0.0.5:1")]
    assert allow[0] == ("::1", 8080)
    assert confine.host_allowed("[::1]", 8080, allow)
    assert confine.host_allowed("[0000:0::0001]", 8080, allow)


def test_summaries_slow_down_past_the_ceiling(confine, tmp_path, monkeypatch):
    clock = [500.0]
    monkeypatch.setattr(confine.time, "monotonic", lambda: clock[0])
    path = tmp_path / "egress.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proxy = confine.EgressProxy([], fd, max_bytes=1, lines_per_second=1)  # tiny ceilings
        for second in range(120):
            clock[0] = 500.0 + second
            for _ in range(3):
                proxy.log("ALLOW", "CONNECT", "api.example.com", 443, entry="api.example.com:443")
            proxy.flush()
        proxy.flush(final=True)
    finally:
        os.close(fd)
    lines = path.read_text().splitlines()
    summaries = [line for line in lines if "ALLOW-SUMMARY" in line]
    assert 2 <= len(summaries) <= 4  # about once a minute, not once a second
    written = [line for line in lines if " ALLOW 'CONNECT'" in line]
    counted = sum(int(line.rsplit("x", 1)[1]) for line in summaries)
    assert len(written) + counted == 120 * 3  # nothing lost: every decision is counted
