#!/usr/bin/env python3
"""Check database schema compatibility using Alembic revision graph.

Issue #2330: Refactored to use SchemaCompatibilityService with Alembic graph validation.

This checker is invoked by the package/Docker install scripts immediately
before ``alembic upgrade head``. It exits non-zero when the database schema
is incompatible with the application requirements.

Fresh databases (no ``alembic_version`` table) are allowed through in development
mode; production mode requires explicit migration before startup.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from migrations.baseline import BASELINE_REVISION
from scripts.shared.db import _get_db_url

# Import new SchemaCompatibilityService
from app.repositories.schema_guard import get_environment_mode
from app.services.schema_compatibility_service import get_schema_compatibility_service
from app.services.schema_compatibility_types import CompatibilityPolicy, SchemaErrorCategory

# Keep these functions for backward compatibility with existing tests
_REVISION_RE = re.compile(r"^revision\s*:\s*str\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_REVISION_RE_FALLBACK = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def collect_active_revision_ids() -> set[str]:
    """Collect revision identifiers from the active post-baseline lineage.

    DEPRECATED: This function is kept for backward compatibility.
    Use SchemaCompatibilityService instead.

    The baseline migration pins its ``revision`` to the
    ``BASELINE_REVISION`` symbol rather than a literal, so its identifier is
    not picked up here; the caller unions the baseline in explicitly. This
    keeps the allowlist self-maintaining as new post-baseline migrations ship.
    """
    from migrations.baseline import ACTIVE_MIGRATIONS_DIR

    revision_ids: set[str] = set()
    if not ACTIVE_MIGRATIONS_DIR.exists():
        return revision_ids

    for path in ACTIVE_MIGRATIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = _REVISION_RE.search(text) or _REVISION_RE_FALLBACK.search(text)
        if match:
            revision_ids.add(match.group(1))

    return revision_ids


def is_supported_revision(current: str | None, supported: set[str]) -> bool:
    """Return whether ``current`` is on the supported (post-baseline) lineage.

    DEPRECATED: This function is kept for backward compatibility.
    Use SchemaCompatibilityService instead.
    """
    return current is not None and current in supported


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check database schema compatibility using Alembic revision graph"
    )
    parser.add_argument("--database-url", help="override the configured DATABASE_URL")
    parser.add_argument(
        "--policy",
        choices=["require_head", "support_n_1", "support_ancestry"],
        default="require_head",
        help="Compatibility policy (default: require_head)",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Print detailed diagnostic information",
    )
    return parser


def main() -> int:
    """Main entry point for schema compatibility check.

    Returns:
        0 if database is compatible
        1 if database is incompatible
    """
    args = build_parser().parse_args()
    database_url = args.database_url or _get_db_url()

    # Map policy string to enum
    policy_map = {
        "require_head": CompatibilityPolicy.REQUIRE_HEAD,
        "support_n_1": CompatibilityPolicy.SUPPORT_N_1,
        "support_ancestry": CompatibilityPolicy.SUPPORT_ANCESTRY,
    }

    # For backward compatibility, use SUPPORT_ANCESTRY in development mode
    # This allows any revision in the baseline lineage (not just head)
    env_mode = get_environment_mode()
    if args.policy == "require_head" and env_mode == "development":
        policy = CompatibilityPolicy.SUPPORT_ANCESTRY
    else:
        policy = policy_map[args.policy]

    # Determine environment mode
    env_mode = get_environment_mode()

    # Create database engine
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            # Use SchemaCompatibilityService
            service = get_schema_compatibility_service()
            result = service.check_database_compatibility(connection, policy)

            if result.bypass_active:
                print(
                    f"WARNING: Schema compatibility BYPASSED for emergency\n"
                    f"Reason: {result.bypass_reason}\n"
                    f"Database may be incompatible. Proceeding due to emergency bypass.",
                    file=sys.stderr,
                )
                return 0

            if result.is_compatible:
                if result.current_heads:
                    print(
                        f"Database schema compatible. "
                        f"Current revision: {result.current_heads[0]}\n"
                        f"Expected head: {result.expected_head}\n"
                        f"Policy: {policy.value}"
                    )
                else:
                    print(
                        f"Database schema compatible (fresh database in {env_mode} mode)\n"
                        f"Policy: {policy.value}"
                    )
                return 0

            # Database is incompatible - print detailed error
            if args.diagnostic:
                print("\n" + "=" * 70, file=sys.stderr)
                print("SCHEMA COMPATIBILITY CHECK FAILED", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                print(file=sys.stderr)

                if result.current_heads:
                    print(f"Current database revisions: {', '.join(result.current_heads)}", file=sys.stderr)
                else:
                    print("Current database revisions: (none)", file=sys.stderr)

                print(f"Expected head revision: {result.expected_head}", file=sys.stderr)
                print(f"Error category: {result.error_category.value if result.error_category else 'unknown'}", file=sys.stderr)
                print(f"Policy: {policy.value}", file=sys.stderr)

                if result.missing_migrations:
                    print(f"\nMissing migrations ({len(result.missing_migrations)}):", file=sys.stderr)
                    for migration in result.missing_migrations:
                        print(f"  - {migration}", file=sys.stderr)

                print(f"\nCheck duration: {result.check_duration_ms:.2f}ms", file=sys.stderr)
                print(file=sys.stderr)
                print(result.diagnostic_message, file=sys.stderr)
                print("=" * 70, file=sys.stderr)
            else:
                print(f"\nERROR: {result.diagnostic_message}", file=sys.stderr)

                if result.error_category == SchemaErrorCategory.FRESH_DATABASE:
                    print(
                        "\nFresh database detected in production. "
                        "Run migration job first: alembic upgrade head",
                        file=sys.stderr,
                    )
                elif result.error_category == SchemaErrorCategory.BEHIND_HEAD:
                    print(
                        f"\nMissing {len(result.missing_migrations)} migrations. "
                        "Run: alembic upgrade head",
                        file=sys.stderr,
                    )
                elif result.error_category == SchemaErrorCategory.MULTIPLE_HEADS:
                    print(
                        "\nForked migration chain detected. "
                        "Create merge migration and run: alembic upgrade head",
                        file=sys.stderr,
                    )

            return 1

    except Exception as e:
        print(f"\nERROR: Schema compatibility check failed: {e}", file=sys.stderr)
        print("\nThis could indicate:", file=sys.stderr)
        print("  - Database connection issues", file=sys.stderr)
        print("  - Missing or corrupted Alembic configuration", file=sys.stderr)
        print("  - Invalid migration files", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
