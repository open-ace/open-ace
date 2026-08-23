import pytest

from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict, aggregate_verdicts
from app.modules.workspace.autonomous.evidence import Verdict

pytestmark = [
    pytest.mark.regression,
    pytest.mark.issue(2335),
    pytest.mark.usefixtures("_enable_acceptance_verification"),
]


def _item(v, item="x"):
    return ItemVerdict(item=item, verdict=v, evidence=[], rationale="")


def test_all_confirmed():
    assert aggregate_verdicts([_item(Verdict.CONFIRMED), _item(Verdict.CONFIRMED)]) == "confirmed"


def test_any_rejected():
    assert aggregate_verdicts([_item(Verdict.CONFIRMED), _item(Verdict.REJECTED)]) == "rejected"


def test_any_indeterminate_without_rejected():
    assert (
        aggregate_verdicts([_item(Verdict.CONFIRMED), _item(Verdict.INDETERMINATE)])
        == "indeterminate"
    )


def test_empty_is_indeterminate():
    assert aggregate_verdicts([]) == "indeterminate"
