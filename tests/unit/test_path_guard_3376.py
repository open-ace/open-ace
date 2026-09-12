"""Unit tests for app.utils.path_guard (Issue #3376 relocation)."""

import shutil
from pathlib import Path

import pytest

pytestmark = [pytest.mark.issue(3376)]


@pytest.fixture
def ws_base():
    """Throwaway base dir under the real home (non-blacklisted).

    macOS maps tmp_path under /private/var (blacklisted by is_valid_path),
    which would reject every shared-path candidate for environment reasons.
    """
    base = Path.home() / ".ace_shared_err_test_3376"
    base.mkdir(parents=True, exist_ok=True)
    yield str(base)
    shutil.rmtree(base, ignore_errors=True)


def test_is_valid_path_rejects_traversal_and_relative():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice/../../etc") is False
    assert is_valid_path("relative/path") is False
    assert is_valid_path("") is False


def test_is_valid_path_rejects_blacklisted_resolved():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/etc/passwd") is False
    assert is_valid_path("/var/log/x") is False


def test_is_valid_path_accepts_normal_absolute():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice/project") is True


def test_is_valid_path_prefix_boundary():
    """Prefix boundary semantics (Issue #3376).

    Uses the real home directory as the prefix: macOS maps /home to an
    autofs symlink, so hardcoded /home/alice paths realpath elsewhere and
    would fail for environment reasons, not boundary reasons.
    """
    from pathlib import Path

    from app.utils.path_guard import is_valid_path

    home = str(Path.home())
    assert is_valid_path(home, allowed_prefixes=[home]) is True
    assert is_valid_path(home + "_evil", allowed_prefixes=[home]) is False


def test_fs_module_reexports_shared_validator():
    """fs.py 保留模块级名字,既有 patch/引用不断(workspace.py:39 等)。"""
    import app.routes.fs as fs_mod
    import app.utils.path_guard as guard_mod

    assert fs_mod.is_valid_path is guard_mod.is_valid_path


# --- is_valid_remote_path (review round 1, items 6+7) ----------------------


def test_is_valid_remote_path_accepts_frontend_defaults():
    """契约测试:NewSessionModal.getDefaultPath 的三个默认值必须通过服务端远端校验。

    字面量与 frontend/src/components/work/NewSessionModal.tsx 的
    getDefaultPath() 钉死同步——改前端默认值时同步改这里。
    """
    from app.utils.path_guard import is_valid_remote_path

    assert is_valid_remote_path("/root/workspace") is True  # linux 默认
    assert is_valid_remote_path("~/workspace") is True  # darwin/mac 默认
    assert is_valid_remote_path("C:\\workspace") is True  # windows 默认


def test_is_valid_remote_path_accepts_remote_policy_paths():
    """/etc 等位置策略留给 agent:后端只做结构校验。"""
    from app.utils.path_guard import is_valid_remote_path

    assert is_valid_remote_path("/etc") is True
    assert is_valid_remote_path("/root") is True
    assert is_valid_remote_path("/opt/tool") is True
    assert is_valid_remote_path("/tmp") is True
    assert is_valid_remote_path("/home/alice/proj") is True
    assert is_valid_remote_path("C:/workspace") is True
    assert is_valid_remote_path("~/") is True


def test_is_valid_remote_path_rejects_bad_shapes():
    from app.utils.path_guard import is_valid_remote_path

    # 相对路径 / 无锚点
    assert is_valid_remote_path("../x") is False
    assert is_valid_remote_path("a/b") is False
    assert is_valid_remote_path("~") is False
    assert is_valid_remote_path("relative") is False
    # 空与 NUL
    assert is_valid_remote_path("") is False
    assert is_valid_remote_path("/a\x00b") is False
    # '..' 段(两种分隔符、嵌在中间)
    assert is_valid_remote_path("/a/../b") is False
    assert is_valid_remote_path("/a/..") is False
    assert is_valid_remote_path("..\\x") is False
    assert is_valid_remote_path("C:\\ws\\..\\x") is False
    # 非字符串
    assert is_valid_remote_path(None) is False
    assert is_valid_remote_path(5) is False
    assert is_valid_remote_path(["/abs"]) is False
    assert is_valid_remote_path({}) is False


def test_is_valid_remote_path_independent_of_backend_platform():
    """纯结构校验:不触盘、不看 platform/blacklist(对比 is_valid_path)。"""
    from unittest.mock import patch

    from app.utils.path_guard import is_valid_remote_path

    with (
        patch("app.utils.path_guard.os.path.realpath", side_effect=AssertionError("touched fs")),
        patch("app.utils.path_guard.platform.system", side_effect=AssertionError("platform")),
    ):
        assert is_valid_remote_path("/root/workspace") is True
        assert is_valid_remote_path("relative") is False


# --- shared_project_path_error (review round 1, item 1) --------------------


def _shared_err(path, bases, homes, creator_roots=None):
    from app.utils.path_guard import shared_project_path_error

    return shared_project_path_error(path, bases, homes, creator_roots=creator_roots)


def test_shared_path_rejects_base_dir_and_foreign_and_own_home(ws_base):
    base = ws_base
    homes = [f"{base}/alice", f"{base}/bob"]

    assert _shared_err(base, [base], homes) is not None  # base dir 本身
    assert _shared_err(f"{base}/alice", [base], homes) is not None  # 他人 home
    assert _shared_err(f"{base}/bob", [base], homes) is not None  # 自己 home
    # base 的祖先(不在任何 base 下)
    assert _shared_err(str(Path(base).parent), [base], homes) is not None


def test_shared_path_rejects_home_descendants_any_depth(ws_base):
    """Review round 2 [3994613216]: home 后代不再豁免。

    读取侧(creator_roots=None)一律拒绝任意深度的 home 子树;
    创建侧(传 creator_roots)只有创建者自己的 home 子树豁免。
    """
    base = ws_base
    homes = [f"{base}/alice", f"{base}/bob"]

    # 读取侧:他人 home 后代 → 拒绝(之前漏掉的洞)
    assert _shared_err(f"{base}/alice/.ssh", [base], homes) is not None
    assert _shared_err(f"{base}/alice/secrets", [base], homes) is not None
    assert _shared_err(f"{base}/alice/deep/nested/team", [base], homes) is not None
    # 读取侧:创建者自己 home 后代同样拒绝(纵深防御,不依赖 creator 关联)
    assert _shared_err(f"{base}/bob/team", [base], homes) is not None

    # 创建侧(alice):自己 home 后代豁免,他人 home 后代仍拒
    assert _shared_err(f"{base}/alice/subproj", [base], homes, [f"{base}/alice"]) is None
    assert _shared_err(f"{base}/alice/.ssh", [base], homes, [f"{base}/alice"]) is None
    assert _shared_err(f"{base}/bob/subproj", [base], homes, [f"{base}/alice"]) is not None


def test_shared_path_creator_roots_ownership(ws_base):
    """Review round 2 [3994613216]: 共享路径必须落在创建者根内。"""
    base = ws_base
    homes = [f"{base}/alice", f"{base}/bob"]

    # <base>/team-proj 不在创建者任何根内 → 拒(归属规则)
    err = _shared_err(f"{base}/team-proj", [base], homes, [f"{base}/alice"])
    assert err is not None
    assert "own workspace roots" in err
    # 已开放共享根内的子路径 → 允许
    assert (
        _shared_err(f"{base}/team-area/sub", [base], homes, [f"{base}/alice", f"{base}/team-area"])
        is None
    )
    # 空 creator_roots(无身份创建者)→ 一律拒绝(fail closed)
    assert _shared_err(f"{base}/alice/subproj", [base], homes, []) is not None
    assert _shared_err(f"{base}/team-proj", [base], homes, []) is not None


def test_shared_path_accepts_deep_and_first_level_non_home(ws_base):
    base = ws_base
    homes = [f"{base}/alice", f"{base}/bob"]

    assert _shared_err(f"{base}/team-proj", [base], homes) is None
    assert _shared_err(f"{base}/team/deep/proj", [base], homes) is None


def test_shared_path_rejects_traversal_and_outside(ws_base):
    base = ws_base
    assert _shared_err(f"{base}/../escape", [base], []) is not None
    assert _shared_err("/etc/team", [base], []) is not None
