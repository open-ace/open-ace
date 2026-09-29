"""Guard test: frontend audit-action fallback lists must mirror the backend enum.

Issue #3362: ``AUDIT_ACTION_OPTIONS_FALLBACK`` and
``AUDIT_CATEGORIES_FALLBACK`` in ``frontend/src/hooks/useAuditActions.ts`` are
the hardcoded fallbacks used when ``GET /api/audit-actions`` fails. They
previously drifted far behind the backend ``AuditAction`` enum (31 of the
current entries), so fallback-mode audit filtering silently lost actions — and
AuditCenter groups actions by category, so a missing category silently drops
every action in it. These tests parse the TypeScript file and assert both
fallbacks cover the backend exactly — no missing and no extra entries, with
metadata fields mirrored — so any future drift fails CI instead of shipping.
"""

import re
from pathlib import Path

import pytest

from app.modules.governance.audit_logger import AuditAction, get_action_categories

pytestmark = [pytest.mark.unit, pytest.mark.issue(3362)]

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "frontend" / "src" / "hooks" / "useAuditActions.ts"


def extract_fallback_action_values() -> list[str]:
    """Pull every ``value: '...'`` literal out of useAuditActions.ts.

    In that file only ``AUDIT_ACTION_OPTIONS_FALLBACK`` entries use a ``value:``
    key (categories use ``key:``), so this regex isolates exactly the fallback
    action list without needing a TypeScript parser.
    """
    source = HOOK_PATH.read_text(encoding="utf-8")
    return re.findall(r"^\s*value: '([a-z0-9_]+)',\s*$", source, re.MULTILINE)


def test_fallback_actions_match_backend_enum_exactly() -> None:
    fallback_values = extract_fallback_action_values()
    enum_values = {action.value for action in AuditAction}
    fallback_set = set(fallback_values)

    missing = sorted(enum_values - fallback_set)
    extra = sorted(fallback_set - enum_values)
    assert not missing, (
        "AUDIT_ACTION_OPTIONS_FALLBACK is missing backend AuditAction values "
        f"(add them to frontend/src/hooks/useAuditActions.ts): {missing}"
    )
    assert not extra, (
        "AUDIT_ACTION_OPTIONS_FALLBACK contains values that no longer exist in "
        f"the backend AuditAction enum (remove them): {extra}"
    )


def test_fallback_actions_have_no_duplicates() -> None:
    fallback_values = extract_fallback_action_values()
    assert len(fallback_values) == len(
        set(fallback_values)
    ), "AUDIT_ACTION_OPTIONS_FALLBACK contains duplicate values"


def test_fallback_action_metadata_mirrors_backend_mapping() -> None:
    """Fallback label/i18n_key/category must match get_action_categories().

    The /audit-actions endpoint derives value/label/category/i18n_key from
    get_action_categories(); the fallback must return the same shape so the
    AuditCenter UI behaves identically in fallback mode.
    """
    source = HOOK_PATH.read_text(encoding="utf-8")
    entries = re.findall(
        r"\{\s*value: '([a-z0-9_]+)',\s*"
        r"label: '([^']+)',\s*"
        r"category: '([a-z_]+)',\s*"
        r"i18n_key: '([A-Za-z0-9]+)',\s*\},",
        source,
    )
    fallback_by_value = {
        value: (label, category, i18n_key) for value, label, category, i18n_key in entries
    }

    backend_by_value = {
        action["value"]: (action["label"], category_key, action["i18n_key"])
        for category_key, category_data in get_action_categories().items()
        for action in category_data["actions"]
    }

    assert set(fallback_by_value) == set(
        backend_by_value
    ), "fallback/backend action value sets differ — see the enum sync test above"
    mismatches = {
        value: {"fallback": fallback_by_value[value], "backend": backend_by_value[value]}
        for value in backend_by_value
        if fallback_by_value[value] != backend_by_value[value]
    }
    assert not mismatches, (
        "Fallback label/category/i18n_key drifted from backend "
        f"get_action_categories(): {mismatches}"
    )


def extract_fallback_categories() -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """Pull every ``AUDIT_CATEGORIES_FALLBACK`` entry out of useAuditActions.ts.

    Entries use ``key:`` (actions use ``value:``), so the field names isolate
    the category list without a TypeScript parser. ``resource_types`` arrays
    hold only string literals, so a non-greedy bracket scan is exact.
    """
    source = HOOK_PATH.read_text(encoding="utf-8")
    entries = re.findall(
        r"\{\s*key: '([a-z_]+)',\s*"
        r"label: '([^']+)',\s*"
        r"i18n_key: '([A-Za-z0-9]+)',\s*"
        r"resource_types: \[([^\]]*)\]\s*,?\s*\}",
        source,
    )
    keys = [key for key, _, _, _ in entries]
    assert len(keys) == len(set(keys)), (
        "AUDIT_CATEGORIES_FALLBACK contains duplicate keys "
        "(fallback mode would render these categories twice): "
        f"{sorted(k for k in set(keys) if keys.count(k) > 1)}"
    )
    return {
        key: (
            label,
            i18n_key,
            tuple(item.strip().strip("'") for item in raw.split(",") if item.strip()),
        )
        for key, label, i18n_key, raw in entries
    }


def test_fallback_categories_mirror_backend_mapping() -> None:
    """Fallback categories must match get_action_categories() key-for-key.

    AuditCenter renders fallback actions grouped by category and drops actions
    whose category is absent, and ``actionToResourceTypes`` derives from
    ``resource_types`` — so a missing category or a drifted field silently
    changes fallback behavior even when the action list itself is in sync.
    """
    fallback_by_key = extract_fallback_categories()

    backend_by_key = {
        key: (
            data["label"],
            data["i18n_key"],
            tuple(data.get("resource_types", [])),
        )
        for key, data in get_action_categories().items()
    }

    missing = sorted(set(backend_by_key) - set(fallback_by_key))
    extra = sorted(set(fallback_by_key) - set(backend_by_key))
    assert not missing, (
        "AUDIT_CATEGORIES_FALLBACK is missing backend categories "
        "(fallback-mode AuditCenter would drop every action in them): "
        f"{missing}"
    )
    assert not extra, (
        "AUDIT_CATEGORIES_FALLBACK contains categories that no longer exist "
        f"in backend get_action_categories() (remove them): {extra}"
    )

    mismatches = {
        key: {"fallback": fallback_by_key[key], "backend": backend_by_key[key]}
        for key in backend_by_key
        if fallback_by_key[key] != backend_by_key[key]
    }
    assert not mismatches, (
        "Fallback category label/i18n_key/resource_types drifted from backend "
        f"get_action_categories(): {mismatches}"
    )
