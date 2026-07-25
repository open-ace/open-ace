"""Autonomous-agent environment security policy (Issue #2019).

Both autonomous paths — ``agent_runner._build_agent_env`` (local) and
``executor._build_env`` (remote) — funnel through ``build_secure_agent_env`` so
an agent subprocess never inherits raw provider/GitHub/SSH credentials from the
service process. Sensitive keys are scrubbed BEFORE the proxy token is injected,
and a proxy-token minting failure fail-closes (raises) unless an explicit,
development-only opt-in allows the raw-key fallback.

This module is pure (no app imports) so remote-agent can use it standalone on a
remote host and the policy is unit-testable without a DB or proxy service.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_secure_agent_env(
    *,
    base_env: dict[str, str],
    sensitive_keys: set[str],
    proxy_env_vars: dict[str, str],
    proxy_ok: bool,
    is_production: bool,
    raw_fallback_allowed: bool,
) -> dict[str, str]:
    """Return an agent env that carries only proxy-token credentials.

    Args:
        base_env: ``dict(os.environ)`` snapshot of the service process. Still
            holds raw credentials when the caller has not scrubbed it.
        sensitive_keys: static ∪ dynamic credential env-key names to strip.
        proxy_env_vars: adapter proxy vars (``ANTHROPIC_API_KEY=token``,
            ``OPENACE_PROXY_TOKEN``, …). Injected only when ``proxy_ok``.
        proxy_ok: True iff a short-lived proxy token was minted successfully.
        is_production: True iff ``FLASK_ENV=production``.
        raw_fallback_allowed: True iff the dev-only
            ``OPENACE_ALLOW_RAW_KEY_FALLBACK=1`` opt-in is set.

    Returns:
        A scrubbed env with proxy vars injected on success.

    Raises:
        RuntimeError: if ``proxy_ok`` is False and either ``is_production`` or
            not ``raw_fallback_allowed`` — the agent must not launch with raw
            credentials inherited from the service env.
    """
    if not proxy_ok:
        if is_production or not raw_fallback_allowed:
            raise RuntimeError(
                "LLM proxy setup failed; refusing to launch autonomous agent "
                "with inherited credentials (set OPENACE_ALLOW_RAW_KEY_FALLBACK=1 "
                "only in development)."
            )
        logger.error(
            "SECURITY: OPENACE_ALLOW_RAW_KEY_FALLBACK=1 set — autonomous agent "
            "is inheriting raw provider/GitHub credentials from the service env."
        )
        env = dict(base_env)
        env.update(proxy_env_vars)
        return env

    env = {key: value for key, value in base_env.items() if key not in sensitive_keys}
    env.update(proxy_env_vars)
    return env
