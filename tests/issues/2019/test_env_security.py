"""Tests for the autonomous-agent env security policy (Issue #2019).

``build_secure_agent_env`` is the pure policy shared by
``agent_runner._build_agent_env`` (local autonomous) and ``executor._build_env``
(remote autonomous). It guarantees an agent subprocess never inherits raw
provider/GitHub/SSH credentials: sensitive keys are scrubbed before the proxy
token is injected, and a proxy-token failure fail-closes (raises) in production
and in dev unless an explicit opt-in raw-key fallback is set.
"""

import sys
from pathlib import Path

import pytest

_REMOTE_AGENT = Path(__file__).resolve().parents[3] / "remote-agent"
if str(_REMOTE_AGENT) not in sys.path:
    sys.path.insert(0, str(_REMOTE_AGENT))

from env_security import build_secure_agent_env  # noqa: E402


def _base_env_with_raw_keys() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/openace",
        "LANG": "C.UTF-8",
        "OPENAI_API_KEY": "sk-raw-openai",
        "ANTHROPIC_API_KEY": "sk-raw-anthropic",
        "GEMINI_API_KEY": "ya29-raw-gemini",
        "BAILIAN_CODING_PLAN_API_KEY": "raw-bailian",
        "ZAI_API_KEY": "raw-zai",  # dynamic custom envKey
        "GH_TOKEN": "ghs_raw_github",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
    }


LLM_KEYS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "BAILIAN_CODING_PLAN_API_KEY",
    "ZAI_API_KEY",
}
NON_LLM_KEYS = {"GH_TOKEN", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY"}
SENSITIVE = LLM_KEYS | NON_LLM_KEYS

PROXY_VARS = {
    "ANTHROPIC_API_KEY": "proxy-token",
    "OPENACE_PROXY_URL": "http://localhost:5000/api/remote/llm-proxy",
    "OPENACE_PROXY_TOKEN": "proxy-token",
}


class TestBuildSecureAgentEnv:
    def test_proxy_ok_no_raw_credential_value_leaks(self):
        # A sensitive key may legitimately reappear holding the proxy token
        # (e.g. ANTHROPIC_API_KEY is what claude reads the token from), so the
        # invariant is "no raw VALUE leaks", not "the var name is absent".
        raw = _base_env_with_raw_keys()
        env = build_secure_agent_env(
            base_env=raw,
            sensitive_keys=SENSITIVE,
            llm_provider_keys=LLM_KEYS,
            proxy_env_vars=PROXY_VARS,
            proxy_ok=True,
            is_production=True,
            raw_fallback_allowed=False,
        )
        for key in SENSITIVE:
            assert env.get(key) != raw[key], f"raw value of {key} leaked into agent env"
        # Keys that are NOT proxy-bearing vars must be fully absent.
        for absent in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            assert absent not in env

    def test_proxy_ok_keeps_non_sensitive_env(self):
        env = build_secure_agent_env(
            base_env=_base_env_with_raw_keys(),
            sensitive_keys=SENSITIVE,
            llm_provider_keys=LLM_KEYS,
            proxy_env_vars=PROXY_VARS,
            proxy_ok=True,
            is_production=True,
            raw_fallback_allowed=False,
        )
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/openace"
        assert env["LANG"] == "C.UTF-8"

    def test_proxy_ok_injects_proxy_token_vars(self):
        env = build_secure_agent_env(
            base_env=_base_env_with_raw_keys(),
            sensitive_keys=SENSITIVE,
            llm_provider_keys=LLM_KEYS,
            proxy_env_vars=PROXY_VARS,
            proxy_ok=True,
            is_production=True,
            raw_fallback_allowed=False,
        )
        # The adapter's proxy-bearing var is set to the token, NOT the raw key.
        assert env["ANTHROPIC_API_KEY"] == "proxy-token"
        assert env["OPENACE_PROXY_TOKEN"] == "proxy-token"

    def test_proxy_ok_dynamic_custom_envkey_scrubbed(self):
        env = build_secure_agent_env(
            base_env={"ZAI_API_KEY": "raw", "PATH": "/x"},
            sensitive_keys={"ZAI_API_KEY"},
            llm_provider_keys={"ZAI_API_KEY"},
            proxy_env_vars={},
            proxy_ok=True,
            is_production=False,
            raw_fallback_allowed=False,
        )
        assert "ZAI_API_KEY" not in env

    def test_proxy_fail_in_production_raises(self):
        with pytest.raises(RuntimeError, match="(?i)proxy|refus|launch"):
            build_secure_agent_env(
                base_env=_base_env_with_raw_keys(),
                sensitive_keys=SENSITIVE,
                llm_provider_keys=LLM_KEYS,
                proxy_env_vars={},
                proxy_ok=False,
                is_production=True,
                raw_fallback_allowed=True,  # even with opt-in, prod must refuse
            )

    def test_proxy_fail_in_dev_without_opt_in_raises(self):
        with pytest.raises(RuntimeError):
            build_secure_agent_env(
                base_env=_base_env_with_raw_keys(),
                sensitive_keys=SENSITIVE,
                llm_provider_keys=LLM_KEYS,
                proxy_env_vars={},
                proxy_ok=False,
                is_production=False,
                raw_fallback_allowed=False,
            )

    def test_proxy_fail_dev_opt_in_keeps_llm_scrubs_non_llm(self):
        env = build_secure_agent_env(
            base_env=_base_env_with_raw_keys(),
            sensitive_keys=SENSITIVE,
            llm_provider_keys=LLM_KEYS,
            proxy_env_vars={},
            proxy_ok=False,
            is_production=False,
            raw_fallback_allowed=True,
        )
        # The named "raw key fallback" retains only the LLM provider keys…
        assert env["OPENAI_API_KEY"] == "sk-raw-openai"
        assert env["ZAI_API_KEY"] == "raw-zai"
        # …and STILL scrubs non-LLM creds (GitHub/SSH/cloud) — the agent never
        # needs those.
        for key in NON_LLM_KEYS:
            assert key not in env, f"non-LLM credential {key} leaked via opt-in fallback"

    def test_proxy_fail_never_returns_proxy_token(self):
        # On failure there is no token; the returned env must not carry one.
        env = build_secure_agent_env(
            base_env=_base_env_with_raw_keys(),
            sensitive_keys=SENSITIVE,
            llm_provider_keys=LLM_KEYS,
            proxy_env_vars={},
            proxy_ok=False,
            is_production=False,
            raw_fallback_allowed=True,
        )
        assert "OPENACE_PROXY_TOKEN" not in env

    def test_allow_empty_token_returns_scrubbed_env_without_raising(self):
        # Crash-recovery restore path: rebuild a token-less env without failing
        # closed. It must still carry NO raw credential (a fresh token is minted
        # before the agent uses it).
        env = build_secure_agent_env(
            base_env=_base_env_with_raw_keys(),
            sensitive_keys=SENSITIVE,
            llm_provider_keys=LLM_KEYS,
            proxy_env_vars={},
            proxy_ok=False,
            is_production=True,  # even in production, restore must not raise
            raw_fallback_allowed=False,
            allow_empty_token=True,
        )
        for key in SENSITIVE:
            assert key not in env, f"{key} leaked into restored env"
        assert "OPENACE_PROXY_TOKEN" not in env
        assert env["PATH"] == "/usr/bin:/bin"
