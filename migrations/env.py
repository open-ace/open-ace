"""
Open ACE - Alembic Migration Environment

This module configures Alembic for database migrations.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from migrations.version_table import install_wide_version_table

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

install_wide_version_table()

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url():
    """
    Resolve the database URL for the migration run.

    A caller may pin the target database directly on the Alembic ``Config``
    (e.g. ``cfg.set_main_option("sqlalchemy.url", ...)`` or the
    ``sqlalchemy.url`` key in ``alembic.ini``). When set, that value wins so
    tooling and tests can target a throwaway database explicitly — without it,
    ``alembic upgrade`` would silently hit the operator's configured project
    database (see issue #2101, where a stale ``alembic_version`` row on the
    local DB masqueraded as a broken migration graph).

    When no URL is pinned on the ``Config``, fall back to the project's
    canonical resolver ``scripts.shared.db._get_db_url``, which reads
    ``DATABASE_URL`` / ``~/.open-ace/config.json`` and adds
    ``gssencmode=disable`` for PostgreSQL under sudo to prevent the
    GSSAPI/Kerberos crash (SIGSEGV).
    """
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured

    from scripts.shared.db import _get_db_url

    return _get_db_url()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
