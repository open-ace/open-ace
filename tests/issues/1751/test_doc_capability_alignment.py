"""Regression tests for public capability-claim alignment from issue #1751."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_marks_saml_as_planned_not_supported() -> None:
    readme = read_doc("README.md")

    unsupported_claims = [
        "支持 SAML 2.0、OIDC、OAuth2",
        "Enterprise single sign-on via SAML 2.0, OIDC, and OAuth2",
        "Supports OAuth2, OIDC, and SAML providers",
    ]

    for claim in unsupported_claims:
        assert claim not in readme

    assert "OIDC/OAuth2" in readme
    assert "#1784" in readme
    assert "SAML 2.0 Provider 尚未实现" in readme
    assert "SAML 2.0 Provider is not implemented yet" in readme


def test_dingtalk_docs_limit_scope_to_import_resolution() -> None:
    readme = read_doc("README.md")
    en_config = read_doc("docs/en/DINGTALK_CONFIG.md")
    cn_config = read_doc("docs/cn/DINGTALK_CONFIG.md")
    config_guide = read_doc("config/CONFIG_GUIDE.md")

    assert "Feishu/DingTalk" not in readme
    assert "飞书/钉钉" not in readme
    assert "DingTalk import resolution" in readme
    assert "钉钉导入解析" in readme

    assert "Current scope is limited to OpenClaw import name resolution" in en_config
    assert "Out of scope today" in en_config
    assert "#1785" in en_config

    assert "当前范围仅限 OpenClaw 导入链路中的名称解析" in cn_config
    assert "当前不包含" in cn_config
    assert "#1785" in cn_config
    assert "当前钉钉能力仅用于 OpenClaw 导入链路" in config_guide


def test_terminal_docs_describe_windows_piped_subprocess() -> None:
    readme = read_doc("README.md")
    remote_agent_en = read_doc("docs/en/REMOTE-AGENT.md")
    remote_agent_cn = read_doc("docs/cn/REMOTE-AGENT.md")
    marketing = read_doc("docs/marketing/REMOTE_AGENT_API_KEY_PROXY_ARTICLE.md")

    assert "WebSocket PTY" not in readme
    assert "piped subprocess on Windows" in readme
    assert "Windows 使用管道子进程" in readme

    assert "persistent piped subprocess on Windows" in remote_agent_en
    assert "Windows 使用持久的管道子进程" in remote_agent_cn
    assert "piped subprocess on Windows" in marketing


def test_sso_module_docstring_does_not_advertise_saml_support() -> None:
    module_init = read_doc("app/modules/sso/__init__.py")

    assert "Supports OAuth2, OIDC, and SAML providers" not in module_init
    assert "Supports OAuth2 and OIDC providers" in module_init
    assert "#1784" in module_init
