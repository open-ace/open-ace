"""scheduler_worker._check_schema_version must call check_min_revision.main() (#2277).

PR#2214's scheduler_worker shipped calling ``scripts.check_min_revision.
check_min_revision()`` — a function that does not exist (the module exposes
``main``/``collect_active_revision_ids``/``is_supported_revision``). The
scheduler crashed on its first prod start (AttributeError → ``sys.exit(1)`` →
restart loop) because the worker had never been exercised in CI or prod. These
tests pin the correct API: ``main()`` returns 0 on a supported/fresh DB and
non-zero when too old.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# scheduler_worker.py has a module-level guard that sys.exit(1)s unless
# SCHEDULER_MODE=="scheduler". Set it before the deferred import in _run_check.
os.environ["SCHEDULER_MODE"] = "scheduler"


def _run_check() -> None:
    """Invoke _check_schema_version without running SchedulerWorker.__init__.

    The method only imports the check module and logs/exits — it touches no
    instance state — so an un-initialized instance is sufficient and avoids
    the heavy app-context construction of the full worker.
    """
    from app.scheduler_worker import SchedulerWorker

    instance = SchedulerWorker.__new__(SchedulerWorker)
    SchedulerWorker._check_schema_version(instance)


def test_schema_check_passes_when_main_returns_zero():
    """main() == 0 (supported revision / fresh DB) → no exit, no raise."""
    with patch("scripts.check_min_revision.main", return_value=0):
        _run_check()  # should not raise


def test_schema_check_exits_when_main_returns_nonzero():
    """main() != 0 (DB too old / unsupported) → SystemExit(1)."""
    with patch("scripts.check_min_revision.main", return_value=1):
        with pytest.raises(SystemExit) as exc_info:
            _run_check()
        assert exc_info.value.code == 1
