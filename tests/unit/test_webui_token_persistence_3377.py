"""Unit tests for WebUI token secret persistence (Issue #3377)."""

import logging
import os
import stat

import pytest

from app.services.webui_manager import WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.issue(3377)]

SECRET_FILENAME = "webui_token_secret"


def _config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.repositories.database.CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_config_json(config_dir, token_secret=None, workspace=None):
    import json

    ws = workspace if workspace is not None else {"enabled": True}
    if token_secret is not None:
        ws["token_secret"] = token_secret
    (config_dir / "config.json").write_text(json.dumps({"workspace": ws}))


@pytest.mark.regression
def test_config_json_secret_wins_and_no_file_written(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd, token_secret="a" * 64)
    mgr = WebUIManager()
    assert mgr.config.token_secret == "a" * 64
    assert not (cd / SECRET_FILENAME).exists()
    # 注入路径同口径:显式 secret 直接使用,不落盘
    injected = WebUIManager(WorkspaceConfig(enabled=True, token_secret="d" * 64))
    assert injected.config.token_secret == "d" * 64
    assert not (cd / SECRET_FILENAME).exists()


def test_generated_secret_is_persisted_with_0600(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)  # enabled but no token_secret
    mgr = WebUIManager()
    secret = mgr.config.token_secret
    assert len(secret) >= 64
    secret_file = cd / SECRET_FILENAME
    assert secret_file.exists()
    assert secret_file.read_text().strip() == secret
    assert stat.S_IMODE(secret_file.stat().st_mode) & 0o777 == 0o600


@pytest.mark.regression
def test_second_manager_reads_same_secret(tmp_path, monkeypatch):
    # 重启/按请求构造实例一致性(session_access.py 每次新建)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    first = WebUIManager().config.token_secret
    second = WebUIManager().config.token_secret
    assert first == second


def test_existing_secret_file_is_used_not_overwritten(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_text("b" * 64)
    mgr = WebUIManager()
    assert mgr.config.token_secret == "b" * 64


def test_corrupt_secret_file_is_regenerated(tmp_path, monkeypatch):
    # 非空损坏文件必须被重建(而非停留在内存态,否则每个实例各自随机,
    # 复现 #3377 原始 bug)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_text("not-hex!!")
    mgr = WebUIManager()
    assert mgr.config.token_secret != "not-hex!!"
    assert len(mgr.config.token_secret) >= 64
    # 文件被重写为合法值,后续实例收敛到同一 secret
    assert (cd / SECRET_FILENAME).read_text().strip() == mgr.config.token_secret
    assert WebUIManager().config.token_secret == mgr.config.token_secret


def test_binary_corrupt_secret_file_is_regenerated(tmp_path, monkeypatch):
    # 二进制垃圾(不可解码)与文本损坏同语义:unlink 重建而非抛
    # UnicodeDecodeError(按请求构造路径不能因损坏文件持续崩溃)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_bytes(b"\xff\xfe\x00\x81B")
    mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    assert (cd / SECRET_FILENAME).read_text().strip() == mgr.config.token_secret
    assert WebUIManager().config.token_secret == mgr.config.token_secret


def test_makedirs_failure_degrades_to_memory(tmp_path, monkeypatch, caplog):
    # CONFIG_DIR 无法创建(os.makedirs 抛 OSError)→ 同一降级路径:内存态 + 告警
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)

    def _failing_makedirs(path, *a, **kw):
        raise OSError("permission denied")

    monkeypatch.setattr("app.services.webui_manager.os.makedirs", _failing_makedirs)
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    assert any("token secret" in r.message.lower() for r in caplog.records)


def test_empty_secret_file_degrades_to_memory_without_deleting(tmp_path, monkeypatch, caplog):
    # 空文件 = 胜者在途写入/崩溃残留:保守处理,不 unlink(拆在途胜者会造成双
    # secret),内存态 + 告警,文件保留待运维清理
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    (cd / SECRET_FILENAME).write_text("")
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    assert (cd / SECRET_FILENAME).read_text() == ""
    assert any("token secret" in r.message.lower() for r in caplog.records)


@pytest.mark.regression
def test_injected_config_never_touches_disk(tmp_path, monkeypatch):
    cd = _config_dir(tmp_path, monkeypatch)
    mgr = WebUIManager(WorkspaceConfig(enabled=True))
    assert len(mgr.config.token_secret) >= 64
    assert not (cd / SECRET_FILENAME).exists()
    assert not (cd / "config.json").exists()


def test_persist_failure_degrades_to_memory_with_warning(tmp_path, monkeypatch, caplog):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)

    real_open = open

    def _failing_open(path, *a, **kw):
        if str(path).endswith(SECRET_FILENAME):
            raise OSError("read-only filesystem")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", _failing_open)
    # os.open(O_EXCL) 路径也要挡住
    real_os_open = os.open

    def _failing_os_open(path, flags, *a, **kw):
        if str(path).endswith(SECRET_FILENAME):
            raise OSError("read-only filesystem")
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr(os, "open", _failing_os_open)
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    assert any("token secret" in r.message.lower() for r in caplog.records)


def test_generation_logs_warning(tmp_path, monkeypatch, caplog):
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        WebUIManager()
    assert any("token secret" in r.message.lower() for r in caplog.records)


def test_race_loser_reads_winner_secret(tmp_path, monkeypatch):
    # 真实执行 FileExistsError → 重读胜者分支:不预置文件,在拦截器内动态制造
    # 胜者(先写入胜者内容再抛 FileExistsError)
    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    winner = "c" * 64
    real_os_open = os.open

    def _race_open(path, flags, *a, **kw):
        if str(path).endswith(SECRET_FILENAME) and flags & os.O_EXCL:
            fd = real_os_open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, winner.encode())
            os.close(fd)
            raise FileExistsError(str(path))
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr(os, "open", _race_open)
    mgr = WebUIManager()
    assert mgr.config.token_secret == winner


def test_write_failure_after_create_leaves_no_residual_file(tmp_path, monkeypatch):
    # Issue #3377 review: ENOSPC/EIO on os.write after O_EXCL succeeded must
    # not leave a 0-byte file behind — that residual would make every later
    # boot take the conservative empty-file path and permanently disable
    # persistence (the bug this PR fixes).
    import app.services.webui_manager as wm

    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)

    def _failing_write(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", _failing_write)
    ok = wm._persist_secret_file(str(cd / SECRET_FILENAME), "a" * 64)
    assert ok is False
    assert not (cd / SECRET_FILENAME).exists()


def test_unreadable_secret_file_logs_read_error_not_not_configured(tmp_path, monkeypatch, caplog):
    # Issue #3377 review: a present-but-unreadable file (owner/permission
    # drift) must not be reported as "not configured" — the opposite of the
    # truth and a dead end for operators.
    import app.services.webui_manager as wm

    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)
    secret_file = cd / SECRET_FILENAME
    secret_file.write_text("b" * 64)
    secret_file.chmod(0o000)

    try:
        value, nonempty_invalid, unreadable = wm._read_secret_file(str(secret_file))
        assert value is None
        assert nonempty_invalid is False
        assert unreadable is True

        with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
            mgr = WebUIManager()
        assert len(mgr.config.token_secret) >= 64
        messages = [r.message for r in caplog.records]
        assert any("cannot be read" in m for m in messages)
        assert not any("not configured; generated an in-memory" in m for m in messages)
    finally:
        secret_file.chmod(0o600)


def test_absent_file_on_unwritable_fs_is_not_reported_unreadable(tmp_path, monkeypatch, caplog):
    # Issue #3377 review round 2: FileNotFoundError must not be classified as
    # unreadable — on an unwritable-but-readable filesystem the secret file is
    # simply absent, and the final message must be the accurate "not
    # configured", never "exists but cannot be read".
    import logging

    cd = _config_dir(tmp_path, monkeypatch)
    _write_config_json(cd)

    real_open = open

    def _absent_open(path, *a, **kw):
        if str(path).endswith(SECRET_FILENAME):
            raise FileNotFoundError(str(path))
        return real_open(path, *a, **kw)

    real_os_open = os.open

    def _ero_fs_open(path, flags, *a, **kw):
        if str(path).endswith(SECRET_FILENAME):
            raise OSError(30, "Read-only file system")
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr("builtins.open", _absent_open)
    monkeypatch.setattr(os, "open", _ero_fs_open)
    with caplog.at_level(logging.WARNING, logger="app.services.webui_manager"):
        mgr = WebUIManager()
    assert len(mgr.config.token_secret) >= 64
    messages = [r.message for r in caplog.records]
    # Absent is the normal first-boot state: no read-side alarm at all.
    assert not any("Cannot read WebUI token secret file" in m for m in messages)
    # The final message names the real cause (nothing persisted), not the
    # opposite-of-truth "exists but cannot be read".
    assert not any("exists but cannot be read" in m for m in messages)
    assert any("not configured; generated an in-memory" in m for m in messages)
