"""Centralized schema initialization for all modules.

Called once at app startup via create_app() to ensure all tables exist,
replacing the per-request _ensure_tables() pattern that caused ShareLock
contention on PostgreSQL.
"""

import logging

logger = logging.getLogger(__name__)


def ensure_all_tables() -> None:
    """Ensure all application tables and indexes exist.

    Executes all DDL statements from each module's get_ddl_statements().
    Called once at startup from create_app(). Each statement is wrapped in
    try/except so that ALTER TABLE "column already exists" errors are silently
    handled.
    """
    from app.modules.workspace.session_manager import get_ddl_statements as sm_ddl
    from app.modules.workspace.collaboration import get_ddl_statements as collab_ddl
    from app.modules.workspace.prompt_library import get_ddl_statements as pl_ddl
    from app.modules.workspace.api_key_proxy import get_ddl_statements as akp_ddl
    from app.modules.workspace.remote_agent_manager import get_ddl_statements as ram_ddl
    from app.modules.sso.manager import get_ddl_statements as sso_ddl
    from app.modules.compliance.retention import get_ddl_statements as ret_ddl
    from app.services.permission_service import get_ddl_statements as ps_ddl
    from app.modules.compliance.report import get_ddl_statements as report_ddl
    from app.repositories.database import Database

    all_ddl = []
    for ddl_fn in [
        sm_ddl,
        collab_ddl,
        pl_ddl,
        akp_ddl,
        ram_ddl,
        sso_ddl,
        ret_ddl,
        ps_ddl,
        report_ddl,
    ]:
        try:
            all_ddl.extend(ddl_fn())
        except Exception as e:
            logger.warning("Failed to get DDL from %s: %s", ddl_fn.__module__, e)

    if not all_ddl:
        return

    db = Database()
    with db.connection() as conn:
        cursor = conn.cursor()
        for sql in all_ddl:
            try:
                cursor.execute(sql)
            except Exception as e:
                logger.debug("DDL skipped (expected for ALTER TABLE): %s — %s", sql[:80].strip(), e)
        conn.commit()

    logger.info("Schema initialization complete (%d DDL statements executed)", len(all_ddl))
