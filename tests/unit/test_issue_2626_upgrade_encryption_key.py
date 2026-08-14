"""Regression coverage for encryption-key compatibility during package upgrades."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = PROJECT_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"

pytestmark = [pytest.mark.issue(2626), pytest.mark.regression]


def _upgrade_key_migration_block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("# Check if systemd service is missing SECRET_KEY")
    end = text.index("# -- Phase 2: Update service config", start)
    return text[start:end]


def test_upgrade_reuses_legacy_secret_for_missing_encryption_key() -> None:
    block = _upgrade_key_migration_block()

    assert 'local enc_key="${OPENACE_ENCRYPTION_KEY:-$current_secret}"' in block
    assert 'current_secret="$secret_key"' in block


def test_upgrade_does_not_generate_an_unrelated_encryption_key() -> None:
    block = _upgrade_key_migration_block()
    encryption_key_section = block[block.index("# Check if OPENACE_ENCRYPTION_KEY is missing") :]

    assert "openssl rand" not in encryption_key_section


def test_upgrade_reads_the_complete_secret_value() -> None:
    block = _upgrade_key_migration_block()
    secret_assignment = next(
        line for line in block.splitlines() if "local current_secret=" in line
    )

    assert "sed -n 's/^Environment=SECRET_KEY=//p'" in secret_assignment
    assert "cut -d'=' -f3" not in secret_assignment
