"""Open ACE - Model Gateway module (POC).

Optional, fully-pluggable routing of LLM proxy traffic through a LiteLLM-compatible
model gateway while preserving Open ACE quota, usage recording, attribution, and
direct-provider behavior.

Mirrors the ``run_timeline`` pluggable shape so this whole feature is easy to
remove later (it may be re-implemented behind an external API):

- A config flag ``model_gateway.enabled`` (see ``app.utils.config``), with an
  env-override layer (``OPENACE_MODEL_GATEWAY_MODE``).
- A Null/real planner pair with an ``is_noop`` short-circuit flag.
- A single integration seam in ``llm_proxy_handler.handle_llm_proxy_request``.

Removal checklist (mirrors run_timeline): ``git rm`` this package; delete the
``if not _gateway.is_noop`` seam + import in ``llm_proxy_handler.py``; remove
``is_model_gateway_enabled`` from ``app/utils/config.py``; unregister the admin
blueprint in ``app/__init__.py:register_blueprints``; delete the admin page +
routes + i18n keys; delete ``docs/model-gateway.md``; drop the
``model_gateway_config`` table migration. See docs/model-gateway.md for setup.
"""

from app.modules.workspace.model_gateway.config import GatewayConfig, get_gateway_config, is_enabled
from app.modules.workspace.model_gateway.planner import (
    GatewayPlan,
    GatewayPlanner,
    LitellmGatewayPlanner,
    NullGatewayPlanner,
    get_gateway_planner,
    reset_gateway_planner_for_tests,
)
from app.repositories.database import is_postgresql


def get_ddl_statements():
    """Return DDL for the model_gateway_config table (runtime mirror).

    The canonical schema lives in the Alembic migration
    ``migrations/versions/<ts>_add_model_gateway_config.py``; this is a runtime
    mirror kept for parity with ``run_timeline.get_ddl_statements``. Per the
    schema_init transaction-cascade caveat, tables must be created via alembic
    (not ensure_all_tables). Snapshots regenerate via scripts/rebuild_schema_snapshots.py.
    """
    use_pg = is_postgresql()
    pk_type = "SERIAL PRIMARY KEY" if use_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN DEFAULT FALSE" if use_pg else "INTEGER DEFAULT 0"

    return [
        f"""
        CREATE TABLE IF NOT EXISTS model_gateway_config (
            id {pk_type},
            mode TEXT DEFAULT 'direct',
            base_url TEXT,
            encrypted_api_key TEXT,
            encryption_version INTEGER DEFAULT 1,
            model_prefix_mode {bool_type},
            model_prefix TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]


__all__ = [
    "GatewayConfig",
    "GatewayPlan",
    "GatewayPlanner",
    "LitellmGatewayPlanner",
    "NullGatewayPlanner",
    "get_gateway_config",
    "get_gateway_planner",
    "get_ddl_statements",
    "is_enabled",
    "reset_gateway_planner_for_tests",
]
