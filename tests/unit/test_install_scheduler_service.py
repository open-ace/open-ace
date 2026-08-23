"""install.sh must manage openace-scheduler.service (#2293, #2187 follow-up).

Since the scheduler split (#2187), background schedulers run in a separate
``openace-scheduler.service`` systemd unit. install.sh was never updated to
manage it, so every deploy redeployed the code + restarted ``open-ace.service``
(web) but left the scheduler running OLD code (Python doesn't hot-reload), and
a fresh install never installed/enabled the scheduler unit at all.

These tests pin that install.sh manages the scheduler unit the same way it
manages ``open-ace.service``: install from template, enable, restart on both
fresh install and upgrade.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2293)]


INSTALL_SH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "install-central"
    / "package-method"
    / "install.sh"
)


def test_install_sh_manages_scheduler_service_unit():
    """install.sh must reference openace-scheduler.service (install/enable/restart)."""
    text = INSTALL_SH.read_text()
    # The unit name must appear (not zero references as before #2293).
    assert "openace-scheduler.service" in text, (
        "install.sh has no references to openace-scheduler.service — the "
        "scheduler unit is unmanaged (no install/enable/restart). #2293"
    )


def test_install_sh_installs_scheduler_from_template():
    """Scheduler unit is written from scripts/openace-scheduler.service template."""
    text = INSTALL_SH.read_text()
    assert "scripts/openace-scheduler.service" in text, (
        "install.sh must install the scheduler unit from its template "
        "(scripts/openace-scheduler.service)."
    )
    # And it writes the rendered unit to the systemd location.
    assert re.search(
        r"/etc/systemd/system/openace-scheduler\.service", text
    ), "install.sh must render the scheduler unit to /etc/systemd/system/"


def test_install_sh_restarts_scheduler_on_deploy():
    """A deploy/upgrade must restart openace-scheduler.service to load new code.

    This is the core fix: without it the scheduler keeps running pre-deploy
    code after every install.sh run (#2293 root cause).
    """
    text = INSTALL_SH.read_text()
    # systemctl restart OR try-restart of the scheduler unit.
    assert re.search(
        r"systemctl\s+(restart|try-restart)\s+openace-scheduler\.service", text
    ), "install.sh must restart openace-scheduler.service on install/upgrade"


def test_install_sh_enables_scheduler_service():
    """The scheduler unit must be enabled (start on boot) like open-ace.service."""
    text = INSTALL_SH.read_text()
    assert re.search(
        r"systemctl\s+enable\s+openace-scheduler\.service", text
    ), "install.sh must enable openace-scheduler.service"
