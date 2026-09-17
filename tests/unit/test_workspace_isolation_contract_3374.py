"""Unit tests for the local workspace isolation capability contract (Issue #3374)."""

import contextlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]


class _StubConfig:
    def __init__(self, enabled=True, multi_user_mode=True):
        self.enabled = enabled
        self.multi_user_mode = multi_user_mode


class _StubManager:
    def __init__(self, enabled=True, multi_user_mode=True, launch_ok=True, launch_reason=None):
        self.config = _StubConfig(enabled, multi_user_mode)
        self._launch_ok = launch_ok
        self._launch_reason = launch_reason

    def supports_per_user_launch(self, system_account):
        return self._launch_ok, self._launch_reason


def _patch_deployment(monkeypatch, *, platform="linux", docker_multi_user=True):
    monkeypatch.setattr(wic, "_current_platform", lambda: platform)


def _reason_codes(snapshot):
    return [r.code for r in snapshot.reasons]


def _patch_sandbox_probe(monkeypatch, *, ok, tier="kata", reason_code="webui_image_missing"):
    """Stub the #3378 sandboxed probe without touching the sandbox config."""
    reason = (
        None
        if ok
        else wic.IsolationReason(reason_code, f"stubbed sandbox probe failure {reason_code}")
    )
    monkeypatch.setattr(wic, "_sandboxed_readiness", lambda config: (ok, tier, reason))


def test_webui_disabled_reports_unsupported(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(enabled=False))
    assert snap.supported is False
    assert snap.isolation_level == wic.ISOLATION_LEVEL_NONE
    assert snap.backend == wic.BACKEND_SHARED
    assert _reason_codes(snap) == ["webui_disabled"]
    assert snap.enforced == ()
    assert set(snap.unsupported) == set(wic.ALL_DIMENSIONS)


@pytest.mark.parametrize("platform", ["windows", "darwin"])
def test_non_linux_platform_reports_unsupported(monkeypatch, platform):
    _patch_deployment(monkeypatch, platform=platform, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert snap.supported is False
    assert _reason_codes(snap) == ["platform_unsupported"]
    # Issue #3378 form-scoping: the platform gate is os_user-specific — a
    # passing sandboxed probe overrides it (pods live on a remote cluster).
    _patch_sandbox_probe(monkeypatch, ok=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED


def test_platform_reason_message_has_no_deployment_details(monkeypatch):
    _patch_deployment(monkeypatch, platform="darwin", docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    message = snap.reasons[0].message
    assert "/workspace" not in message and "root" not in message.lower()


def test_single_user_mode_reports_unsupported(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(multi_user_mode=False))
    assert snap.supported is False
    assert _reason_codes(snap) == ["multi_user_mode_disabled"]
    # Issue #3378 form-scoping: single-user + sandboxed is a legitimate
    # hardening deployment; the mode gate only governs the os_user chain.
    _patch_sandbox_probe(monkeypatch, ok=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(multi_user_mode=False))
    assert snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED


def test_root_single_user_reports_mode_not_mapping(monkeypatch):
    # 顺序保证:multi_user_mode 关闭优先于 docker 多用户判定(root 单用户部署
    # 不误报 identity_mapping_unverified)。
    _patch_deployment(monkeypatch, docker_multi_user=False)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(multi_user_mode=False))
    assert _reason_codes(snap) == ["multi_user_mode_disabled"]


def test_package_mode_multi_user_with_healthy_probe_reports_os_user(monkeypatch):
    # PR review round 2 (hard regression): a package-method multi-user host
    # (non-Docker layout, sudo+wrapper present) really runs per-user WebUIs;
    # the probe is the verification, the Docker layout is not.
    _patch_deployment(monkeypatch, docker_multi_user=False)
    snap = wic.build_workspace_isolation_snapshot(_ReadyStubManager(readiness=None))
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_OS_USER


def test_package_mode_multi_user_degraded_probe_reports_unsupported(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=False)
    snap = wic.build_workspace_isolation_snapshot(
        _ReadyStubManager(readiness="launch_wrapper_missing")
    )
    assert snap.supported is False
    assert _reason_codes(snap) == ["launch_path_degraded"]


def test_docker_multi_user_reports_os_user(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_ReadyStubManager(readiness=None))
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_OS_USER
    assert snap.backend == wic.BACKEND_PER_USER
    assert snap.enforced == (
        wic.DIMENSION_IDENTITY,
        wic.DIMENSION_FILESYSTEM,
        wic.DIMENSION_ENVIRONMENT,
        wic.DIMENSION_PROCESS,
    )
    # Issue #3378: kernel joined the dimension vocabulary; os_user shares the
    # host kernel by definition, so it lands in unsupported (3-tuple).
    assert snap.unsupported == (
        wic.DIMENSION_RESOURCES,
        wic.DIMENSION_NETWORK_EGRESS,
        wic.DIMENSION_KERNEL,
    )
    assert snap.reasons == ()


def test_policy_revision_always_present(monkeypatch):
    _patch_deployment(monkeypatch)
    snap = wic.build_workspace_isolation_snapshot(_StubManager(enabled=False))
    assert snap.policy_revision == wic.POLICY_REVISION


def test_public_dict_shape(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    data = wic.build_workspace_isolation_snapshot(_StubManager()).public_dict()
    assert set(data.keys()) == {
        "local_workspace_multi_user",
        "backend",
        "isolation_level",
        "enforced",
        "unsupported",
        "reasons",
        "entry_points",
        "entry_point_details",
        "policy_revision",
    }
    assert data["local_workspace_multi_user"] == "supported"
    assert data["entry_points"]["autonomous"] == "separate_contract"


def test_level_ordering_and_validation():
    assert wic.isolation_level_at_least("os_user", "none")
    assert wic.isolation_level_at_least("os_user", "os_user")
    assert not wic.isolation_level_at_least("none", "os_user")
    assert wic.is_valid_isolation_level("sandboxed")
    assert not wic.is_valid_isolation_level("strong")


def _supported_snapshot(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    return wic.build_workspace_isolation_snapshot(_StubManager())


def test_gate_rejects_invalid_level(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        "strong", snapshot=snap, system_account="alice_acct", manager=_StubManager()
    )
    assert reason.code == "invalid_isolation_level"


def test_gate_rejects_level_above_supported(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        wic.ISOLATION_LEVEL_SANDBOXED,
        snapshot=snap,
        system_account="alice_acct",
        manager=_StubManager(),
    )
    assert reason.code == "isolation_level_unsupported"


def test_gate_allows_none_without_further_checks(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    assert (
        wic.evaluate_isolation_requirement(
            wic.ISOLATION_LEVEL_NONE,
            snapshot=snap,
            system_account=None,
            manager=_StubManager(launch_ok=False),
        )
        is None
    )


def test_gate_rejects_missing_identity_mapping(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        wic.ISOLATION_LEVEL_OS_USER,
        snapshot=snap,
        system_account=None,
        manager=_StubManager(),
    )
    assert reason.code == "identity_mapping_missing"


def test_gate_rejects_shared_account_launch(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    reason = wic.evaluate_isolation_requirement(
        wic.ISOLATION_LEVEL_OS_USER,
        snapshot=snap,
        system_account="alice_acct",
        manager=_StubManager(launch_ok=False, launch_reason="dev_directory_mode_shared_account"),
    )
    assert reason.code == "per_user_launch_unavailable"
    assert "dev_directory_mode_shared_account" in reason.message


def test_gate_passes_when_all_conditions_hold(monkeypatch):
    snap = _supported_snapshot(monkeypatch)
    assert (
        wic.evaluate_isolation_requirement(
            wic.ISOLATION_LEVEL_OS_USER,
            snapshot=snap,
            system_account="alice_acct",
            manager=_StubManager(),
        )
        is None
    )


class _ReadyStubManager(_StubManager):
    def __init__(self, readiness):
        super().__init__()
        self._readiness = readiness

    def per_user_launch_readiness(self):
        return self._readiness


def test_snapshot_consults_launch_readiness(monkeypatch):
    # 评审 #4:快照必须与 /user-url 闸门看同一个启动路径——dev 目录模式等
    # 降级形态下契约不再宣称 os_user
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(
        _ReadyStubManager(readiness="dev_directory_mode_shared_account")
    )
    assert snap.supported is False
    assert _reason_codes(snap) == ["launch_path_degraded"]
    assert "dev_directory_mode_shared_account" in snap.reasons[0].message


def test_snapshot_ok_when_readiness_clean(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    snap = wic.build_workspace_isolation_snapshot(_ReadyStubManager(readiness=None))
    assert snap.supported is True


def test_snapshot_without_manager_reads_disk_config(monkeypatch):
    # 评审 #13:无 manager 单例时按磁盘配置推导,不构造 manager、不探测启动路径
    _patch_deployment(monkeypatch, docker_multi_user=True)

    class _DiskConfig:
        enabled = True
        multi_user_mode = True

    monkeypatch.setattr("app.services.webui_manager.peek_webui_manager", lambda: None)
    monkeypatch.setattr(
        "app.services.webui_manager.read_workspace_config",
        lambda: _DiskConfig(),
    )
    snap = wic.build_workspace_isolation_snapshot(None)
    assert snap.supported is True  # 无法探测启动路径:provisional + 显式 reason
    assert _reason_codes(snap) == ["launch_path_unverified"]


def test_unsupported_snapshot_omits_entry_points(monkeypatch):
    # 评审 #11:unsupported 快照不再输出"webui: enforced"式入口矩阵
    _patch_deployment(monkeypatch)
    data = wic.build_workspace_isolation_snapshot(_StubManager(enabled=False)).public_dict()
    assert "entry_points" not in data


def test_supported_snapshot_keeps_entry_points(monkeypatch):
    _patch_deployment(monkeypatch, docker_multi_user=True)
    data = wic.build_workspace_isolation_snapshot(_ReadyStubManager(readiness=None)).public_dict()
    assert data["entry_points"]["webui"] == "enforced"


def test_cold_worker_snapshot_is_provisional(monkeypatch):
    # PR review round 2: no manager (cold worker) cannot probe the launch
    # path — report the level as provisional via an explicit reason instead
    # of silently claiming a verified one.
    _patch_deployment(monkeypatch)

    class _DiskConfig:
        enabled = True
        multi_user_mode = True

    monkeypatch.setattr("app.services.webui_manager.peek_webui_manager", lambda: None)
    monkeypatch.setattr(
        "app.services.webui_manager.read_workspace_config",
        lambda: _DiskConfig(),
    )
    snap = wic.build_workspace_isolation_snapshot(None)
    assert snap.supported is True
    assert _reason_codes(snap) == ["launch_path_unverified"]
    assert "provisional" in snap.reasons[0].message


# --- resolve_required_floor (PR review round 2) ---


def _floor_config(explicit=""):
    return type("C", (), {"required_isolation_level": explicit})()


def _floor_snapshot(level="os_user"):
    return wic.IsolationCapabilitySnapshot(
        supported=level != "none",
        backend=wic.BACKEND_PER_USER if level != "none" else wic.BACKEND_SHARED,
        isolation_level=level,
        enforced=("identity",) if level != "none" else (),
        unsupported=(),
        reasons=(),
    )


def test_floor_defaults_to_verified_snapshot_level():
    assert wic.resolve_required_floor(_floor_config(""), _floor_snapshot("os_user")) == "os_user"
    assert wic.resolve_required_floor(_floor_config(""), _floor_snapshot("none")) == "none"


def test_floor_explicit_valid_wins():
    assert wic.resolve_required_floor(_floor_config("none"), _floor_snapshot("os_user")) == "none"
    assert (
        wic.resolve_required_floor(_floor_config("os_user"), _floor_snapshot("none")) == "os_user"
    )


def test_floor_invalid_explicit_falls_back_fail_closed(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert (
            wic.resolve_required_floor(_floor_config("strong"), _floor_snapshot("os_user"))
            == "os_user"
        )
    assert any("required_isolation_level" in r.message for r in caplog.records)


def _degraded_snapshot():
    return wic.IsolationCapabilitySnapshot(
        supported=False,
        backend=wic.BACKEND_SHARED,
        isolation_level=wic.ISOLATION_LEVEL_NONE,
        enforced=(),
        unsupported=(),
        reasons=(wic.IsolationReason("launch_path_degraded", "wrapper gone"),),
    )


def test_degraded_multi_user_floor_logs_warning(caplog):
    # PR review round 3:派生下限只会跟着降——多用户 + 探针降级时必须打
    # WARNING,让运维动作引发的降级在日志里可见
    import logging

    degraded = _degraded_snapshot()
    cfg = type("C", (), {"multi_user_mode": True, "required_isolation_level": ""})()
    with caplog.at_level(logging.WARNING, logger="app.services.workspace_isolation_contract"):
        assert wic.resolve_required_floor(cfg, degraded) == "none"
    assert any("launch path degraded" in r.message for r in caplog.records)


def test_pinned_floor_above_degraded_snapshot_warns_reject_all(caplog):
    # PR review round 5:pin os_user + 探针降级 → 运行期全线 400,这一侧必须
    # 打 WARNING;不能只给结果良性的派生路径(弱隔离继续服务)告警
    import logging

    degraded = _degraded_snapshot()
    cfg = type("C", (), {"multi_user_mode": True, "required_isolation_level": "os_user"})()
    with caplog.at_level(logging.WARNING, logger="app.services.workspace_isolation_contract"):
        assert wic.resolve_required_floor(cfg, degraded) == "os_user"
    msgs = [r.message for r in caplog.records]
    assert any("REJECT all launches" in m for m in msgs)
    # 拒绝路径不应再收到"将继续弱隔离服务"的旧文案
    assert not any("will NOT be per-user isolated" in m for m in msgs)

    # 运行期结论(钉住 install.sh 审查场景):默认请求 = floor os_user,
    # 快照只能验证 none → 门闸拒绝 isolation_level_unsupported
    rejection = wic.evaluate_isolation_requirement(
        "os_user", snapshot=degraded, system_account="alice", manager=None
    )
    assert rejection is not None
    assert rejection.code == "isolation_level_unsupported"


def test_invalid_pin_with_degraded_falls_to_derived_warning(caplog):
    # PR review round 6:invalid pin 不得打 REJECT 告警——实际 floor 落到
    # none、默认请求 200 放行,谎称"全线拒绝"比静默更危险
    import logging

    degraded = _degraded_snapshot()
    cfg = type("C", (), {"multi_user_mode": True, "required_isolation_level": "strong"})()
    with caplog.at_level(logging.WARNING, logger="app.services.workspace_isolation_contract"):
        assert wic.resolve_required_floor(cfg, degraded) == "none"
    msgs = [r.message for r in caplog.records]
    assert not any("REJECT all launches" in m for m in msgs)
    # 无效值落到派生分支:弱隔离告警 + invalid 告警各一条
    assert any("will NOT be per-user isolated" in m for m in msgs)
    assert any("Invalid workspace.required_isolation_level" in m for m in msgs)


def test_pinned_floor_on_healthy_snapshot_stays_silent(caplog):
    # 安装器正常形态(wrapper 已装):pin 不产生任何告警
    import logging

    cfg = type("C", (), {"multi_user_mode": True, "required_isolation_level": "os_user"})()
    with caplog.at_level(logging.WARNING, logger="app.services.workspace_isolation_contract"):
        assert wic.resolve_required_floor(cfg, _floor_snapshot("os_user")) == "os_user"
    assert not caplog.records


def test_healthy_multi_user_floor_is_silent(caplog):
    import logging

    healthy = _floor_snapshot("os_user")
    cfg = type("C", (), {"multi_user_mode": True, "required_isolation_level": ""})()
    with caplog.at_level(logging.WARNING, logger="app.services.workspace_isolation_contract"):
        assert wic.resolve_required_floor(cfg, healthy) == "os_user"
    assert not caplog.records


def test_single_user_degraded_floor_is_silent(caplog):
    # 单用户形态派生 none 是预期而非降级,不告警
    import logging

    degraded = wic.IsolationCapabilitySnapshot(
        supported=False,
        backend=wic.BACKEND_SHARED,
        isolation_level=wic.ISOLATION_LEVEL_NONE,
        enforced=(),
        unsupported=(),
        reasons=(wic.IsolationReason("multi_user_mode_disabled", "by design"),),
    )
    cfg = type("C", (), {"multi_user_mode": False, "required_isolation_level": ""})()
    with caplog.at_level(logging.WARNING, logger="app.services.workspace_isolation_contract"):
        assert wic.resolve_required_floor(cfg, degraded) == "none"
    assert not caplog.records


# ---------------------------------------------------------------------------
# Issue #3410: the machine-readable entry-point contract.
# ---------------------------------------------------------------------------


def _supported(monkeypatch):
    """An os_user (supported) snapshot payload."""
    _patch_deployment(monkeypatch, docker_multi_user=True)
    return wic.build_workspace_isolation_snapshot(_ReadyStubManager(readiness=None)).public_dict()


def _unsupported(monkeypatch):
    _patch_deployment(monkeypatch)
    return wic.build_workspace_isolation_snapshot(_StubManager(enabled=False)).public_dict()


def _sandboxed():
    """A sandboxed snapshot payload, built directly (no deployment patching).

    Mirrors the construction in test_workspace_isolation_sandboxed_3378.py: the
    sandboxed READINESS machinery is irrelevant here — what is under test is
    how the snapshot renders its per-level entry matrix.
    """
    return wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=wic._SANDBOXED_ENFORCED,
        unsupported=wic._SANDBOXED_UNSUPPORTED,
        reasons=(wic.IsolationReason("sandbox_runtime_unverified", "stub"),),
    ).public_dict()


@pytest.mark.issue(3410)
class TestEntryPointDetails:
    def test_every_entry_has_details_and_statuses_agree(self, monkeypatch):
        data = _supported(monkeypatch)
        assert set(data["entry_points"]) == set(data["entry_point_details"])
        for name, status in data["entry_points"].items():
            d = data["entry_point_details"][name]
            assert d["status"] == status, name
            assert d["scope"] in wic.ENTRY_POINT_SCOPES, name
            assert d["operations"] and isinstance(d["operations"], list), name
            for op in d["operations"]:
                assert set(op) == {"name", "roots", "symlink_policy"}, (name, op)
            for bucket in ("limitations", "residuals"):
                for entry in d[bucket]:
                    assert set(entry) == {"code", "message"}, (name, bucket)

    def test_enforced_entries_declare_no_limitations_but_may_declare_residuals(self, monkeypatch):
        data = _supported(monkeypatch)
        for name, d in data["entry_point_details"].items():
            if d["status"] == "enforced":
                assert d["limitations"] == [], name
                assert d["covered_by_isolation_level"] is True, name
        # residuals are informational and MUST NOT gate admission
        assert data["entry_point_details"]["filesystem_api"]["residuals"]

    def test_filesystem_api_is_enforced_and_local(self, monkeypatch):
        d = _supported(monkeypatch)["entry_point_details"]["filesystem_api"]
        assert d["status"] == "enforced"
        assert d["scope"] == "local_workspace"
        names = {op["name"] for op in d["operations"]}
        assert names == {
            "browse",
            "check-path",
            "create-directory",
            "home",
            "upload",
            "download",
            "delete-file",
            "search",
        }
        by_name = {op["name"]: op for op in d["operations"]}
        # #3410 review: the per-file operations never follow a symlink with
        # more privilege than the account; the rest resolve and reject.
        for name in ("upload", "download", "delete-file", "search"):
            assert by_name[name]["roots"] == ["home"], name
            assert by_name[name]["symlink_policy"] == "never_followed_with_elevated_privilege"
        for name in ("browse", "check-path", "create-directory"):
            assert by_name[name]["symlink_policy"] == "resolved_then_rejected_if_outside"
        assert by_name["browse"]["roots"] == ["home", "shared_projects"]
        assert "workspace_root_first_level_non_home" in by_name["create-directory"]["roots"]
        assert [r["code"] for r in d["residuals"]] == [
            "shared_project_roots_are_cross_user_by_design",
            "account_scoped_wrappers_required_for_package_non_root",
            "unmapped_users_act_as_the_web_process",
        ]

    def test_remote_entries_are_scoped_not_partial(self, monkeypatch):
        data = _supported(monkeypatch)
        for name in ("terminal", "vscode"):
            d = data["entry_point_details"][name]
            assert d["status"] == "remote_machine_scope"
            assert d["scope"] == "remote_machine"
            assert d["covered_by_isolation_level"] is False
            assert "machine_assignment_acl" in d["access_control"]
        # The operation list must track the REAL route surface: cli-start is
        # POST /api/remote/terminal/cli/start (same machine ACL + session
        # ownership as the web terminal) and drifted out once already.
        term_ops = {op["name"] for op in data["entry_point_details"]["terminal"]["operations"]}
        assert term_ops == {"start", "cli-start", "attach", "status", "stop", "ws"}

    def test_no_snapshot_emits_the_reserved_disabled_status(self, monkeypatch):
        """`disabled` is reserved for a kill switch Open ACE does not have yet."""
        for data in (_supported(monkeypatch), _sandboxed()):
            assert wic.ENTRY_POINT_STATUS_DISABLED not in data["entry_points"].values()

    def test_sandboxed_entries_are_reported_unwired(self, monkeypatch):
        data = _sandboxed()
        os_user = _supported(monkeypatch)["entry_point_details"]
        for name in ("filesystem_api", "terminal", "vscode"):
            d = data["entry_point_details"][name]
            assert d["status"] == "sandboxed_entry_not_wired"
            assert d["covered_by_isolation_level"] is False
            # The entry's own gaps survive; the sandbox gap is appended (#3410 review).
            assert [x["code"] for x in d["limitations"]] == [
                *(x["code"] for x in os_user[name]["limitations"]),
                "sandboxed_entry_not_wired",
            ]
        remote = data["entry_point_details"]["terminal"]["limitations"]
        assert "remote_execution_not_isolated_by_this_level" in [x["code"] for x in remote]
        # The host /fs text does not describe a sandboxed user's files.
        fs = data["entry_point_details"]["filesystem_api"]
        assert fs["boundary"] != os_user["filesystem_api"]["boundary"]
        assert "pod" in fs["boundary"]
        assert fs["residuals"] == []

    def test_public_payload_cannot_corrupt_the_module_constants(self, monkeypatch):
        data = _supported(monkeypatch)
        data["entry_point_details"]["filesystem_api"]["operations"][0]["roots"].append("evil")
        data["entry_point_details"]["filesystem_api"]["limitations"].append({"code": "x"})
        fresh = _supported(monkeypatch)
        assert (
            "evil" not in fresh["entry_point_details"]["filesystem_api"]["operations"][0]["roots"]
        )
        assert fresh["entry_point_details"]["filesystem_api"]["limitations"] == []

    def test_policy_revision(self):
        assert wic.POLICY_REVISION == "2026-09-16.1"

    def test_unsupported_snapshot_omits_both_maps(self, monkeypatch):
        data = _unsupported(monkeypatch)
        assert "entry_points" not in data
        assert "entry_point_details" not in data


@pytest.mark.issue(3410)
class TestDocumentedAdmissionPredicate:
    """The §7 doc example must be executable, not prose that rots on a bump.

    These tests RUN the fenced snippet from
    docs/WORKSPACE_ISOLATION_CAPABILITIES.md §7 (#3410 review: a re-implemented
    copy could drift from the doc unnoticed).
    """

    DOC = Path(__file__).resolve().parents[2] / "docs" / "WORKSPACE_ISOLATION_CAPABILITIES.md"

    @classmethod
    def _snippet(cls) -> str:
        match = re.search(
            r"python3 - caps\.json <<'PY'\n(.*?)\nPY\n", cls.DOC.read_text(encoding="utf-8"), re.S
        )
        assert match, "the §7 admission snippet is missing from the doc"
        return match.group(1)

    def _accept(self, caps, tmp_path, monkeypatch) -> bool:
        path = tmp_path / "caps.json"
        path.write_text(json.dumps(caps), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["-", str(path)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(self._snippet(), "docs §7 snippet", "exec"), {"__name__": "__main__"})
        verdict = out.getvalue().strip()
        assert verdict in ("ACCEPT", "REJECT"), verdict
        return verdict == "ACCEPT"

    def test_os_user_snapshot_is_accepted(self, tmp_path, monkeypatch):
        assert self._accept(_supported(monkeypatch), tmp_path, monkeypatch) is True

    def test_sandboxed_snapshot_is_rejected(self, tmp_path, monkeypatch):
        assert self._accept(_sandboxed(), tmp_path, monkeypatch) is False

    def test_unsupported_snapshot_is_rejected(self, tmp_path, monkeypatch):
        assert self._accept(_unsupported(monkeypatch), tmp_path, monkeypatch) is False

    def test_unknown_revision_is_rejected(self, tmp_path, monkeypatch):
        data = dict(_supported(monkeypatch), policy_revision="9999-01-01.9")
        assert self._accept(data, tmp_path, monkeypatch) is False

    def test_unknown_entry_status_is_rejected(self, tmp_path, monkeypatch):
        data = _supported(monkeypatch)
        data["entry_points"] = dict(data["entry_points"], terminal="brand_new_token")
        assert self._accept(data, tmp_path, monkeypatch) is False

    def test_a_limitation_on_a_needed_entry_is_rejected(self, tmp_path, monkeypatch):
        data = _supported(monkeypatch)
        data["entry_point_details"]["filesystem_api"]["limitations"] = [
            {"code": "x", "message": "a real gap"}
        ]
        assert self._accept(data, tmp_path, monkeypatch) is False

    def test_residuals_do_not_gate_admission(self, tmp_path, monkeypatch):
        data = _supported(monkeypatch)
        assert data["entry_point_details"]["filesystem_api"]["residuals"]
        assert self._accept(data, tmp_path, monkeypatch) is True
