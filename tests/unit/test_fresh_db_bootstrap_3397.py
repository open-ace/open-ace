"""Fresh-database self-initialization in the Docker entrypoint (Issue #3397).

A fresh install in production used to be REFUSED by the entrypoint: with no
application schema and no ``alembic_version`` table,
``scripts/check_min_revision.py`` exits 1 in production mode ("Fresh database
detected in production"), so the documented DEPLOYMENT.md compose path could
not boot an empty database at all. The multi-user acceptance run had to work
around it with a one-shot ``alembic upgrade head && python3 scripts/init_db.py``
container, recorded as a DECLARED DEVIATION (#3397).

The fix makes the entrypoint SELF-initialize exactly that state: no
application schema (the sentinel-tables probe) AND no ``alembic_version``
table — the same two commands the deviation used, run by the normal boot
flow. Any database with existing schema OR a recorded revision is never
touched by the branch: it keeps the minimum-revision refusal and the regular
upgrade path, and ``init_db.py`` still seeds only when no application schema
existed at boot.

Coverage follows the #3379/#3390 entrypoint-harness convention: the decision
block is extracted from docker-entrypoint.sh between its markers and executed
under real bash with ``set -e`` and stubbed ``python3``/``alembic``
(recorded via PATH-independent shell functions), then the executed command
sequences are asserted per scenario — pure text assertions cannot see which
branch actually ran the two commands.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

pytestmark = [pytest.mark.regression, pytest.mark.issue(3397), pytest.mark.security]

ENTRYPOINT = ROOT / "docker-entrypoint.sh"

START_MARKER = 'echo "Checking database initialization status..."'
END_MARKER = 'echo "Database migration completed."\n    fi'

# Runs the extracted block under `set -e` with stubbed commands. The fake
# python3 answers the two probes from env (SCHEMA_ANSWER for the -c
# sentinel-tables probe, ALEMBIC_ANSWER for the heredoc alembic_version
# probe — the function drains the heredoc from stdin) and routes the two
# script invocations by their argv; CHECK_RC/ALEMBIC_RC inject failures.
_HARNESS = """
CALLS_FILE="$TEST_TMP/calls"
touch "$CALLS_FILE"
python3() {
  echo "python3 $*" >> "$CALLS_FILE"
  case "$1" in
    scripts/check_min_revision.py) return "$CHECK_RC" ;;
    scripts/init_db.py) return 0 ;;
    -c) printf '%s' "$SCHEMA_ANSWER"; return 0 ;;
    -) cat > /dev/null; printf '%s' "$ALEMBIC_ANSWER"; return 0 ;;
  esac
  return 0
}
alembic() { echo "alembic $*" >> "$CALLS_FILE"; return "$ALEMBIC_RC"; }
set -e
"""


def _extract_block() -> str:
    content = ENTRYPOINT.read_text(encoding="utf-8")
    start = content.index(START_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    block = content[start:end]
    assert "PY_FRESH_DB_PROBE_EOF" in block, "the #3397 fresh-DB probe must be in the block"
    return block


def _run_block(
    *,
    schema_answer: str,
    alembic_answer: str,
    check_rc: int = 0,
    alembic_rc: int = 0,
) -> tuple[int, str, str]:
    """Run the extracted decision block; returns (rc, call log, stdout)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = _HARNESS + _extract_block()
        env = {
            "PATH": "/usr/bin:/bin",
            "TEST_TMP": tmp,
            "DATABASE_URL": "postgresql://stub",
            "SCHEMA_ANSWER": schema_answer,
            "ALEMBIC_ANSWER": alembic_answer,
            "CHECK_RC": str(check_rc),
            "ALEMBIC_RC": str(alembic_rc),
        }
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        calls_path = Path(tmp) / "calls"
        calls = calls_path.read_text() if calls_path.exists() else ""
        return proc.returncode, calls, proc.stdout


class TestFreshDbBootstrap:
    def test_fresh_db_runs_the_two_bootstrap_commands_and_skips_the_refusal(self):
        """No schema AND no alembic_version — the #3397 shape. The entrypoint
        must self-initialize (the deviation's exact two commands) instead of
        letting check_min_revision refuse the boot; the refusal is not even
        consulted (a fresh DB cannot be below the baseline)."""
        rc, calls, out = _run_block(schema_answer="no", alembic_answer="no", check_rc=1)
        assert rc == 0, "a genuinely fresh database must boot, not refuse"
        assert "Fresh database detected — self-initializing schema and seed (was #3397)" in out
        assert "alembic upgrade head" in calls, "schema bootstrap must run"
        assert "python3 scripts/init_db.py" in calls, "seed (admin/tenant) must run"
        assert (
            "python3 scripts/check_min_revision.py" not in calls
        ), "the minimum-revision refusal must be bypassed for a genuinely fresh DB"

    def test_existing_schema_migrates_but_never_seeds(self):
        """An existing application schema keeps the regular path: revision
        check + upgrade, and init_db.py must NOT run (re-seeding an existing
        deployment is the pre-#3397 behavior the sentinel probe guards)."""
        rc, calls, out = _run_block(schema_answer="yes", alembic_answer="yes")
        assert rc == 0
        assert "Existing application schema detected." in out
        assert "python3 scripts/check_min_revision.py" in calls
        assert "alembic upgrade head" in calls
        assert "python3 scripts/init_db.py" not in calls, "existing schema must not be re-seeded"
        assert "was #3397" not in out

    def test_existing_revision_without_schema_is_still_refused(self):
        """No sentinel tables but an alembic_version row is NOT a fresh DB —
        a migrated-but-unseeded/legacy state. The minimum-revision refusal
        must keep protecting it (below-baseline lineages are refused)."""
        rc, calls, out = _run_block(schema_answer="no", alembic_answer="yes", check_rc=1)
        assert rc == 1, "the below-baseline refusal must stay in charge"
        assert "below the minimum supported starting point" in out
        assert "alembic upgrade head" not in calls
        assert "python3 scripts/init_db.py" not in calls

    def test_probe_failure_is_not_treated_as_fresh(self):
        """An unreadable database (schema probe 'unknown') must fail STRICT:
        the self-initialization branch requires a positive 'no' on BOTH
        probes, so an ambiguous state keeps the refusal — never bootstrap
        blind over a database that might have data."""
        rc, _calls, out = _run_block(schema_answer="unknown", alembic_answer="no", check_rc=1)
        assert rc == 1
        assert "was #3397" not in out

    def test_fresh_db_bootstrap_failure_uses_the_existing_error_path(self):
        """`alembic upgrade head` failing on a fresh DB must surface the
        existing migration-failure error and exit 1 — self-initialization
        degrades to the normal error paths, never boots on a half-built
        schema."""
        rc, calls, out = _run_block(schema_answer="no", alembic_answer="no", alembic_rc=1)
        assert rc == 1
        assert "ERROR: alembic upgrade head failed" in out
        assert "python3 scripts/init_db.py" not in calls, "no seed on a failed migration"
