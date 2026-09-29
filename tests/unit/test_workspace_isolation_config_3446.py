"""Issue #3446: ``workspace.isolation`` — one level + backend vocabulary.

Pinned here: the parser (the removed keys are refused with their replacement,
level/backend pairs, where limits / egress / tier / container_webui belong),
the install-method availability of each backend, the startup validation, and
the snapshot's ``install_method`` / ``available_backends``.
"""

from __future__ import annotations

import json

import pytest

from app.services import workspace_isolation_config as wiconf
from app.services import workspace_isolation_contract as wic
from app.services.workspace_isolation_config import IsolationConfigError, parse_isolation

pytestmark = [pytest.mark.issue(3446)]


def _block(**kw):
    return {"isolation": kw}


# ── parser ──────────────────────────────────────────────────────────────────


def test_no_block_means_one_shared_webui():
    isolation = parse_isolation({"enabled": True})
    assert (isolation.level, isolation.backend) == ("none", "shared")


@pytest.mark.parametrize(
    ("level", "backend"),
    [("none", "shared"), ("os_user", "plain"), ("os_user", "bwrap"),
     ("sandboxed", "local-gvisor"), ("sandboxed", "local-kata"), ("sandboxed", "opensandbox")],
)  # fmt: skip
def test_every_backend_at_its_level(level, backend):
    isolation = parse_isolation(_block(level=level, backend=backend))
    assert (isolation.level, isolation.backend) == (level, backend)


@pytest.mark.parametrize(
    "key",
    ["multi_user_mode", "required_isolation_level", "os_user_confinement", "sandbox_tier",
     "confinement_memory_max", "confinement_cpu_quota", "confinement_tasks_max",
     "confinement_egress_allow", "confinement_container_webui"],
)  # fmt: skip
def test_removed_keys_are_refused_with_their_replacement(key):
    with pytest.raises(IsolationConfigError) as caught:
        parse_isolation({key: True, "isolation": {"level": "none", "backend": "shared"}})
    message = str(caught.value)
    assert key in message and "workspace.isolation" in message
    assert wiconf.REMOVED_KEYS[key] in message


def test_a_backend_at_the_wrong_level_names_the_allowed_ones():
    with pytest.raises(IsolationConfigError) as caught:
        parse_isolation(_block(level="os_user", backend="local-kata"))
    message = str(caught.value)
    assert '"sandboxed"' in message and "'plain'" in message and "'bwrap'" in message


@pytest.mark.parametrize(
    "block",
    [{"level": "strong", "backend": "plain"},
     {"level": "os_user", "backend": "docker"},
     {"level": "os_user"},
     {"backend": "plain"},
     {"level": "os_user", "backend": "plain", "mode": "x"},
     "plain"],
)  # fmt: skip
def test_invalid_blocks_are_refused(block):
    with pytest.raises(IsolationConfigError):
        parse_isolation({"isolation": block})


def test_limits_and_egress_belong_to_the_confined_backends():
    isolation = parse_isolation(
        _block(
            level="sandboxed",
            backend="local-kata",
            limits={"memory": "8G", "cpu_percent": 400, "tasks": 1024},
            egress_allow=["pypi.org:443", " "],
            container_webui="/opt/webui/bin/qwen-code-webui",
        )
    )
    assert (isolation.memory, isolation.cpu_percent, isolation.tasks) == ("8G", 400, 1024)
    assert isolation.egress_allow == ("pypi.org:443",)
    assert isolation.container_webui == "/opt/webui/bin/qwen-code-webui"
    for backend, level in (("plain", "os_user"), ("shared", "none")):
        with pytest.raises(IsolationConfigError, match="does not enforce"):
            parse_isolation(_block(level=level, backend=backend, limits={"memory": "4G"}))
    with pytest.raises(IsolationConfigError, match="tier"):
        parse_isolation(_block(level="sandboxed", backend="opensandbox", egress_allow=["a:1"]))


@pytest.mark.parametrize(
    "limits",
    [{"memory": "lots"}, {"cpu_percent": 0}, {"cpu_percent": True}, {"tasks": 8},
     {"tasks": "512"}, {"swap": "1G"}],
)  # fmt: skip
def test_bad_limits_are_refused(limits):
    with pytest.raises(IsolationConfigError):
        parse_isolation(_block(level="os_user", backend="bwrap", limits=limits))


def test_tier_and_container_webui_are_backend_specific():
    assert (
        parse_isolation(_block(level="sandboxed", backend="opensandbox", tier="kata")).tier
        == "kata"
    )
    with pytest.raises(IsolationConfigError, match="tier"):
        parse_isolation(_block(level="os_user", backend="plain", tier="kata"))
    with pytest.raises(IsolationConfigError, match="container_webui"):
        parse_isolation(_block(level="os_user", backend="bwrap", container_webui="/usr/bin/w"))


# ── install method ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "method"), [("docker", "docker"), (" Package ", "package"),
                                               ("", ""), ("k8s", "")])  # fmt: skip
def test_install_method_marker(monkeypatch, value, method):
    monkeypatch.setenv(wiconf.INSTALL_METHOD_ENV, value)
    assert wiconf.install_method() == method


def test_the_docker_install_cannot_provide_the_confined_backends():
    unavailable = wiconf.backend_unavailability("docker", "linux")
    assert set(unavailable) == {"bwrap", "local-gvisor", "local-kata"}
    assert {code for code, _ in unavailable.values()} == {"install_method_docker"}
    assert wiconf.backend_unavailability("package", "linux") == {}
    assert set(wiconf.backend_unavailability("package", "darwin")) == {
        "bwrap", "local-gvisor", "local-kata",
    }  # fmt: skip


def test_an_unavailable_backend_names_the_alternative():
    with pytest.raises(IsolationConfigError) as caught:
        wiconf.check_backend_available(
            parse_isolation(_block(level="sandboxed", backend="local-kata")), "docker", "linux"
        )
    message = str(caught.value)
    assert "Docker install" in message or "docker install" in message
    assert "'opensandbox'" in message and "package install" in message
    with pytest.raises(IsolationConfigError, match="'plain'"):
        wiconf.check_backend_available(
            parse_isolation(_block(level="os_user", backend="bwrap")), "docker", "linux"
        )
    wiconf.check_backend_available(
        parse_isolation(_block(level="os_user", backend="plain")), "docker", "linux"
    )


def test_validate_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv(wiconf.INSTALL_METHOD_ENV, "docker")
    path = tmp_path / "config.json"
    assert wiconf.validate_config_file(str(path), "linux") is None  # no file yet
    path.write_text(json.dumps({"workspace": {"enabled": True, "multi_user_mode": True}}))
    with pytest.raises(IsolationConfigError, match="multi_user_mode"):
        wiconf.validate_config_file(str(path), "linux")
    path.write_text(
        json.dumps({"workspace": {"enabled": True, **_block(level="os_user", backend="bwrap")}})
    )
    with pytest.raises(IsolationConfigError, match="not available"):
        wiconf.validate_config_file(str(path), "linux")
    # a disabled workspace is parsed but not held to the install method
    path.write_text(
        json.dumps({"workspace": {"enabled": False, **_block(level="os_user", backend="bwrap")}})
    )
    assert wiconf.validate_config_file(str(path), "linux").backend == "bwrap"


def test_the_server_refuses_to_start_with_an_invalid_isolation(tmp_path, monkeypatch):
    import app as app_module

    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": {"enabled": True, "os_user_confinement": "bwrap"}})
    )
    monkeypatch.setattr("app.repositories.database.CONFIG_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="os_user_confinement"):
        app_module._validate_workspace_isolation()


def test_a_bad_file_disables_the_workspace_at_runtime(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": {"enabled": True, "multi_user_mode": True}})
    )
    monkeypatch.setattr("app.repositories.database.CONFIG_DIR", str(tmp_path))
    from app.services.webui_manager import read_workspace_config

    config = read_workspace_config()
    assert config.enabled is False and config.isolation.backend == "shared"


# ── snapshot ────────────────────────────────────────────────────────────────


def test_snapshot_lists_install_method_and_available_backends(monkeypatch):
    monkeypatch.setenv(wiconf.INSTALL_METHOD_ENV, "docker")
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    data = wic._unsupported("webui_disabled", "off").public_dict()
    assert data["install_method"] == "docker"
    assert data["available_backends"]["plain"] == {"available": True}
    assert data["available_backends"]["opensandbox"] == {"available": True}
    assert data["available_backends"]["local-kata"] == {
        "available": False,
        "reason": "install_method_docker",
    }
    monkeypatch.delenv(wiconf.INSTALL_METHOD_ENV)
    assert wic._unsupported("webui_disabled", "off").public_dict()["install_method"] == "unknown"


# ── Docker root authorization follows the backend ──────────────────────────


@pytest.mark.parametrize(("backend", "os_accounts"), [("plain", True), ("shared", False),
                                                      ("opensandbox", False), ("", False)])  # fmt: skip
def test_security_baseline_reads_the_isolation_backend(monkeypatch, backend, os_accounts):
    from app.utils import security_baseline

    seen = []
    real = security_baseline.check_root_user

    def _spy(is_root, multi_user_mode, allow_root_multi_user):
        seen.append(multi_user_mode)
        return real(is_root, multi_user_mode, allow_root_multi_user)

    monkeypatch.setenv("WORKSPACE_ISOLATION_BACKEND", backend)
    monkeypatch.setattr(security_baseline, "check_root_user", _spy)
    security_baseline.check_all()
    assert seen == [os_accounts]
