"""Conftest for the legacy ``tests/issues`` quarantine.

Embeds the authoritative pytest nodeid into every JUnit report so the legacy
failure-baseline comparator (``scripts/legacy_issue_baseline.py``) has a stable,
path-based identity that does not depend on reconstructing it from xunit2
``classname``/``name`` (xunit2 carries no ``file`` attribute).

This is the MANDATORY identity source for baseline generation: a reference run
must include this conftest. It is inert without ``--junitxml``
(``record_property`` then only stores the value on the item).
"""

import pytest


@pytest.fixture(autouse=True)
def _openace_nodeid(request, record_property):
    record_property("openace_nodeid", request.node.nodeid)
