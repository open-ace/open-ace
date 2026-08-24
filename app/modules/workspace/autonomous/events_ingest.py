"""Cross-process SSE ingest plumbing (scheduler → web, same-host deployments).

#2187 split the scheduler into its own process, but ``AutonomousEventEmitter``
is an in-process singleton: events emitted by the scheduler never reach the
web process's SSE subscribers, so the AI activity panel shows an eternal
"waiting" state in split-process production. The scheduler-side emitter
forwards events over a loopback HTTP POST to the web process's ingest route,
which re-broadcasts through its own emitter.

Resolution helpers live here so both processes derive the SAME values from the
SAME shared ``~/.open-ace/config.json`` (both services run as one user on one
host and read one deployed tree).

Delivery semantics: at-least-once. A POST that times out after the web side
already broadcast produces a duplicate; this is tolerated because the frontend
dedups ``agent_activity`` by ``activity_id``, ``emit()``'s ``setdefault`` is
idempotent, and a duplicate ``milestone_updated`` only invalidates a query
cache one extra time.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from functools import lru_cache

from app.utils.config import get_config_value

logger = logging.getLogger(__name__)

INGEST_SECRET_HEADER = "X-OpenACE-Events-Key"
DEFAULT_WEB_PORT = 19888  # mirrors scripts/shared/config.py's fallback

# remote_addr values that are always trusted. Under an nginx reverse proxy
# every external request arrives as 127.0.0.1, which is why the shared secret
# — not this check — is the primary control for the ingest route.
_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def resolve_ingest_secret() -> str:
    """Resolve the shared ingest secret identically in both processes.

    Priority: dedicated ``server.events_ingest_key`` > ``SECRET_KEY`` env >
    root-level ``secret_key`` in config.json. ``get_secret_key()`` from
    scripts.shared.config is deliberately NOT used: it returns None when the
    SECRET_KEY env var is set ("don't override the operator"), which would
    make the two sides resolve different values and 403 forever. An empty
    result must disable forwarding / return 503 from the route (fail-closed).
    """
    dedicated = str(get_config_value("server", "events_ingest_key", "") or "").strip()
    if dedicated:
        return dedicated
    env_secret = (os.environ.get("SECRET_KEY") or "").strip()
    if env_secret:
        return env_secret
    try:
        from scripts.shared.config import _load_user_config

        root_secret = str(_load_user_config().get("secret_key") or "").strip()
    except Exception:  # pragma: no cover - scripts/ always deployed with app
        root_secret = ""
    return root_secret


def resolve_ingest_url() -> str | None:
    """Resolve the ingest endpoint URL: explicit override > loopback + web port.

    The port reuses the exact derivation the web server binds to, so the two
    can never drift: both processes call the same function on the same config
    file. No port is ever hardcoded at a call site.
    """
    explicit = str(get_config_value("server", "events_ingest_url", "") or "").strip()
    if explicit:
        return explicit
    port = _configured_web_port()
    if port is None:
        return None
    return f"http://127.0.0.1:{port}"


def _configured_web_port() -> int | None:
    """The web port exactly as server.py resolves it (config > default)."""
    try:
        from scripts.shared.config import _get_web_port

        return int(_get_web_port())
    except Exception:  # pragma: no cover - defensive; scripts/ ships with app
        raw = get_config_value("server", "web_port", DEFAULT_WEB_PORT)
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.error("Invalid server.web_port value: %r", raw)
            return None


@lru_cache(maxsize=8)
def _parse_trusted_sources(raw: tuple) -> tuple:
    """Parse IP/CIDR entries; malformed entries are logged and skipped."""
    networks = []
    for entry in raw:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.error(
                "Ignoring malformed server.events_ingest_trusted_sources entry: %r",
                entry,
            )
    return tuple(networks)


def trusted_source_networks() -> tuple:
    """Configured extra trusted sources (for split-host/container topologies)."""
    raw = get_config_value("server", "events_ingest_trusted_sources", [])
    if raw is None:
        return ()
    if isinstance(raw, str):
        entries = tuple(part.strip() for part in raw.split(",") if part.strip())
    elif isinstance(raw, list):
        entries = tuple(str(part).strip() for part in raw if str(part).strip())
    else:
        logger.error("server.events_ingest_trusted_sources must be a list, ignoring")
        return ()
    return _parse_trusted_sources(entries)


def is_trusted_source(remote_addr: str | None) -> bool:
    """Loopback addresses plus any configured trusted networks.

    ``remote_addr`` is the socket-level peer address; X-Forwarded-For is
    intentionally ignored (it is client-controlled).
    """
    if not remote_addr:
        return False
    if remote_addr in _LOOPBACK_ADDRS:
        return True
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(addr in network for network in trusted_source_networks())
