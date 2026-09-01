"""Synthetic negative-evidence probe for #3186 Phase A.

Deliberately contains an un-asserted test so the false-positive scanner lane
must fail. This file exists only to prove the CI gate blocks new debt; the
branch is deleted right after the evidence run is recorded.
"""


def test_probe_missing_assertion_3186():
    sum([1, 2, 3])
