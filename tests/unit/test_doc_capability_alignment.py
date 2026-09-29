"""Regression tests for public capability-claim alignment from issue #1751.

Migrated from tests/issues/1751/test_doc_capability_alignment.py (path depth
adjusted from parents[3] to parents[2] for the tests/unit location).
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(1751)]

ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_and_docs_advertise_implemented_saml_support() -> None:
    readme = read_doc("README.md")
    sso_config = read_doc("docs/guide/SSO_CONFIG.md")
    api_doc = read_doc("docs/dev/API.md")
    saml_module = read_doc("app/modules/sso/saml.py")

    assert "#1784" not in readme
    assert "SAML 2.0 Provider 尚未实现" not in readme
    assert "SAML 2.0 Provider is not implemented yet" not in readme
    assert "OIDC/OAuth2/SAML" in readme
    assert "SAML 2.0 enterprise single sign-on" not in readme

    assert "Service Provider" in sso_config
    assert "POST /api/sso/acs/<provider_name>" in sso_config
    assert "XML Signature" in sso_config
    assert "SAML Service Provider metadata" in api_doc
    assert "class SAMLProvider" in saml_module


def test_dingtalk_docs_advertise_implemented_sync_and_bot_support() -> None:
    readme = read_doc("README.md")
    dingtalk_doc = read_doc("docs/guide/DINGTALK_CONFIG.md")

    assert "#1785" not in readme
    assert "#1785" not in dingtalk_doc
    assert "DingTalk Sync" in readme
    assert "钉钉同步" in readme

    assert "local org sync of DingTalk departments and users" in dingtalk_doc
    assert "alert delivery to DingTalk custom robot webhooks" in dingtalk_doc
    assert "POST /api/admin/dingtalk/sync" in dingtalk_doc

    assert "将钉钉组织架构同步到 Open ACE" in dingtalk_doc
    assert "钉钉自定义机器人 webhook" in dingtalk_doc


def test_terminal_docs_describe_windows_piped_subprocess() -> None:
    readme = read_doc("README.md")
    remote_agent_doc = read_doc("docs/guide/REMOTE_AGENT.md")

    assert "WebSocket PTY" not in readme
    assert "piped subprocess on Windows" in readme
    assert "Windows 使用管道子进程" in readme

    assert "persistent piped subprocess on Windows" in remote_agent_doc
    assert "Windows 使用持久的管道子进程" in remote_agent_doc


def test_sso_module_docstring_advertises_saml_support() -> None:
    module_init = read_doc("app/modules/sso/__init__.py")

    assert "Supports OAuth2, OIDC, and SAML 2.0 providers" in module_init
    assert "#1784" not in module_init
