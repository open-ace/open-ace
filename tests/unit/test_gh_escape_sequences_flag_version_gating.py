"""Version gating for gh's --allow-escape-sequences (Issue #2708).

gh only learned ``--allow-escape-sequences`` in 2.97. On older deployments
both CI-log fetch paths (``gh api .../jobs/<id>/logs`` and ``gh run view
--log-failed``) exit ``unknown flag``, every failure excerpt comes back
empty, and CI-repair exhausts with "0/N failed checks have logs" (see
#2482). GitHubOps must probe the gh version once, omit the flag below
2.97, and say so explicitly when a fetch dies on an unknown flag instead
of swallowing it as "no logs".
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError

pytestmark = [pytest.mark.regression, pytest.mark.issue(2708)]


def _gh_with_repo() -> GitHubOps:
    """A GitHubOps whose owner/repo is pre-resolved, like the existing
    log-fetch tests in test_autonomous_ci_guardrails.py."""
    gh = GitHubOps("/tmp/repo")
    gh._repo_slug = "open-ace/open-ace"
    gh._repo_host = "github.com"
    gh._owner_repo = "open-ace/open-ace"
    gh._owner_repo_resolved = True
    return gh


def _result(returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_version_probe_parses_gh_version_and_caches():
    gh = GitHubOps("/tmp/repo")
    calls = []

    def fake_run(args, check=True, repo_scoped=True, api_only=False):
        calls.append((list(args), check, repo_scoped))
        return _result(stdout="gh version 2.92.0 (2026-05-14)\n")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        assert gh._supports_escape_sequences_flag() is False
        assert gh._supports_escape_sequences_flag() is False  # cached, no re-probe

    assert calls == [(["--version"], False, False)]


@pytest.mark.parametrize("version", ["2.97.0", "2.97.1", "2.98.0", "3.4.1"])
def test_version_probe_accepts_modern_gh(version):
    gh = GitHubOps("/tmp/repo")
    with patch.object(
        gh, "_run_gh", return_value=_result(stdout=f"gh version {version} (2026-07-31)\n")
    ):
        assert gh._supports_escape_sequences_flag() is True


@pytest.mark.parametrize(
    "probe_result",
    [
        GitHubOpsError("gh CLI not found. Please install and authenticate gh."),
        _result(returncode=127, stdout="", stderr="command not found"),
        _result(returncode=0, stdout="something completely unlike a version banner", stderr=""),
    ],
)
def test_version_probe_fails_open(probe_result):
    """Unprobeable gh keeps today's behavior (flag present): dropping the
    flag on a modern gh would re-break log fetches with ANSI-refusal
    (#2516-era), which is worse than the old-gh symptom this gate fixes."""
    gh = GitHubOps("/tmp/repo")
    if isinstance(probe_result, Exception):
        patcher = patch.object(gh, "_run_gh", side_effect=probe_result)
    else:
        patcher = patch.object(gh, "_run_gh", return_value=probe_result)
    with patcher:
        assert gh._supports_escape_sequences_flag() is True


def test_old_gh_omits_flag_on_rest_path():
    gh = _gh_with_repo()
    gh._escape_flag_supported = False  # probe result for gh 2.92
    calls = []

    def fake_run(args, check=True, repo_scoped=True, api_only=False):
        calls.append((list(args), repo_scoped))
        return _result(stdout="pytest failed\n1 failed\n")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "test",
                "link": "https://github.com/open-ace/open-ace/actions/runs/123/job/456",
            }
        )

    assert "1 failed" in excerpt
    assert calls == [(["api", "repos/open-ace/open-ace/actions/jobs/456/logs"], False)]


def test_old_gh_omits_flag_on_run_view_fallback():
    gh = _gh_with_repo()
    gh._escape_flag_supported = False
    calls = []

    def fake_run(args, check=True, repo_scoped=True, api_only=False):
        calls.append((list(args), repo_scoped))
        if args[0] == "api":
            return _result(returncode=1, stderr="HTTP 404: Not Found")
        return _result(stdout="LINT: failure\nblack...FAILED\n")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "lint",
                "link": "https://github.com/open-ace/open-ace/actions/runs/123/job/456",
            }
        )

    assert "FAILED" in excerpt
    assert calls[1] == (["run", "view", "123", "--job", "456", "--log-failed"], True)


def test_old_gh_omits_flag_on_run_list_fallback():
    gh = GitHubOps("/tmp/repo")
    gh._escape_flag_supported = False
    calls = []

    def fake_run(args, check=True, repo_scoped=True, api_only=False):
        calls.append(list(args))
        if args[0] == "run" and args[1] == "list":
            return _result(stdout=json.dumps([{"databaseId": 777, "name": "lint"}]))
        return _result(stdout="LINT: failure\nblack...FAILED\n")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "lint",
                # REST check-run html_url: not a parseable Actions job URL,
                # so this exercises _fetch_log_excerpt_via_run_list.
                "link": "https://github.com/open-ace/open-ace/runs/999",
                "head_sha": "a" * 40,
            }
        )

    assert "FAILED" in excerpt
    assert calls[-1] == ["run", "view", "777", "--log-failed"]


def test_modern_gh_keeps_flag_on_rest_and_run_view_paths():
    gh = _gh_with_repo()
    gh._escape_flag_supported = True  # probe result for gh >= 2.97
    calls = []

    def fake_run(args, check=True, repo_scoped=True, api_only=False):
        calls.append(list(args))
        if args[0] == "api":
            # Old-gh-style unknown flag must not happen here; simulate the
            # REST endpoint refusing so the run-view fallback is exercised.
            return _result(returncode=1, stderr="HTTP 404: Not Found")
        return _result(stdout="LINT: failure\nblack...FAILED\n")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "lint",
                "link": "https://github.com/open-ace/open-ace/actions/runs/123/job/456",
            }
        )

    assert "FAILED" in excerpt
    assert calls[0] == [
        "api",
        "repos/open-ace/open-ace/actions/jobs/456/logs",
        "--allow-escape-sequences",
    ]
    assert calls[1] == [
        "run",
        "view",
        "123",
        "--job",
        "456",
        "--log-failed",
        "--allow-escape-sequences",
    ]


def test_unknown_flag_failure_warns_with_version_hint(caplog):
    """When a fetch dies on 'unknown flag' (the fail-open corner: probe could
    not tell the version), the warning must name the gh-version symptom
    instead of letting it read as an ordinary 'no logs' (#2708, #2482)."""
    gh = _gh_with_repo()
    gh._escape_flag_supported = True

    def fake_run(args, check=True, repo_scoped=True, api_only=False):
        return _result(returncode=1, stderr="unknown flag: --allow-escape-sequences")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        with caplog.at_level(logging.WARNING):
            excerpt = gh.get_check_failure_excerpt(
                {
                    "name": "lint",
                    "link": "https://github.com/open-ace/open-ace/actions/runs/123/job/456",
                }
            )

    assert excerpt == ""
    assert any(
        "unknown flag" in record.message and "likely gh < 2.97" in record.message
        for record in caplog.records
    )
