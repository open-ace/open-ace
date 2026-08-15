"""Regression: SQL identifier whitelist in the SQLite→PostgreSQL migration script.

``scripts/utils/migrate_to_postgres.py`` interpolates table/column names into
SQL statements because identifiers cannot use query parameters. Bandit flags
those sites as B608 (possible SQL injection); the fix for #2482 routes every
interpolated identifier through ``validate_identifier`` and annotates the
interpolation sites with ``# nosec B608``. These tests pin the whitelist so
the nosec annotations keep a real runtime guard behind them.

The script lives outside any package, so it is loaded by file path.
Refs #2482.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "utils" / "migrate_to_postgres.py"
)

_spec = importlib.util.spec_from_file_location("migrate_to_postgres", _SCRIPT)
assert _spec is not None and _spec.loader is not None
migrate_to_postgres = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("migrate_to_postgres", migrate_to_postgres)
_spec.loader.exec_module(migrate_to_postgres)

validate_identifier = migrate_to_postgres.validate_identifier

pytestmark = [pytest.mark.regression, pytest.mark.issue(2482)]


@pytest.mark.parametrize(
    "name",
    [
        "users",
        "tenant_usage",
        "daily_messages_2026",
        "_private",
        "a" * 63,
        "TENANTS",
    ],
)
def test_valid_identifiers_pass_through(name):
    assert validate_identifier(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "users; DROP TABLE users; --",
        "users --comment",
        "bad name",
        "users;DELETE",
        '"quoted"',
        "'quoted'",
        "`quoted`",
        "1starts_with_digit",
        "table-name",
        "table.name",
        "users\n",
        "users\t",
        "中文表名",
        "user$(whoami)",
        "%s",
        "a" * 64 + "!",
    ],
)
def test_malicious_or_malformed_identifiers_rejected(name):
    with pytest.raises(ValueError):
        validate_identifier(name)


@pytest.mark.parametrize("name", [None, 42, 3.14, b"users", ["users"], {"t": 1}])
def test_non_string_input_rejected(name):
    with pytest.raises(ValueError):
        validate_identifier(name)
