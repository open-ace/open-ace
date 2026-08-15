"""Regression (#2675): paraphrased verifier verdicts must not fail coverage.

The acceptance verifier (glm-5) sometimes paraphrases a checklist item
slightly (e.g. drops the word "and"). The coverage check in
``acceptance_verification`` only did exact matching after whitespace/case
normalization, so a fully-confirmed delivery got a synthetic INDETERMINATE
"The verifier did not cover this checklist item" and the aggregate went
indeterminate (prod evidence: #2565 adb351ec, #2331 67241d8d).

The fix adds a conservative fuzzy fallback (substring or token-Jaccard >= 0.8,
one-to-one greedy assignment). The verdict's own status stays authoritative —
only the identity matching is fuzzy.
"""

import pytest

from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict, aggregate_verdicts
from app.modules.workspace.autonomous.evidence import Verdict

pytestmark = [pytest.mark.regression, pytest.mark.issue(2675)]

# Real prod pair (#2565 adb351ec / #2331 67241d8d): glm-5 dropped the "and".
PROD_CHECKLIST_ITEM = "OS type normalization handles Linux, Windows, and Darwin variants"
PROD_VERIFIER_ITEM = "OS type normalization handles Linux, Windows, Darwin variants"


def _verdict(item: str, verdict: Verdict, evidence_ref: str = "file:1") -> ItemVerdict:
    return ItemVerdict(
        item=item,
        verdict=verdict,
        evidence=[{"ref": evidence_ref, "note": "n"}],
        rationale="r",
    )


class TestItemsMatchFuzzy:
    def test_prod_paraphrase_pair_matches_with_margin(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        score = _items_match_fuzzy(PROD_CHECKLIST_ITEM, PROD_VERIFIER_ITEM)
        assert score >= 0.8, f"prod paraphrase pair must match, got {score}"

    def test_exact_normalized_match_scores_one(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert _items_match_fuzzy("  Rate  limiter\tcaps at 10 ", "rate limiter caps at 10") == 1.0

    def test_substring_matches(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy(
                "Rate limiter caps at 10", "rate limiter caps at 10 requests per minute"
            )
            >= 0.8
        )

    def test_different_requirements_do_not_cross_match(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        # Genuinely different requirements that share many tokens: one exports
        # CSV, the other exports XLSX. Token Jaccard is 6/8 = 0.75 < 0.8.
        assert (
            _items_match_fuzzy(
                "Export button downloads CSV with all columns",
                "Export button downloads XLSX with all columns",
            )
            < 0.8
        )

    def test_disjoint_items_do_not_match(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy(
                "Rate limiter returns 429 when quota is exceeded",
                "Login page renders the SSO button",
            )
            < 0.8
        )

    def test_empty_string_never_matches(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert _items_match_fuzzy("", "anything") == 0.0
        assert _items_match_fuzzy("anything", "") == 0.0


class TestCoverMissingChecklistItems:
    def _run(self, verdicts, checklist):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _cover_missing_checklist_items,
        )

        return _cover_missing_checklist_items(verdicts, checklist)

    def test_exact_match_primary_no_extra_verdicts(self):
        verdicts = [_verdict("Rate limiter caps at 10", Verdict.CONFIRMED)]
        result = self._run(verdicts, ["Rate limiter  caps at 10"])
        assert len(result) == 1

    def test_prod_paraphrase_confirmed_stays_confirmed(self):
        verdicts = [_verdict(PROD_VERIFIER_ITEM, Verdict.CONFIRMED, evidence_ref="os.py:12")]
        result = self._run(verdicts, [PROD_CHECKLIST_ITEM])

        assert len(result) == 2
        checklist_verdict = result[-1]
        assert checklist_verdict.item == PROD_CHECKLIST_ITEM
        assert checklist_verdict.verdict is Verdict.CONFIRMED
        assert checklist_verdict.evidence == [{"ref": "os.py:12", "note": "n"}]
        # Aggregate over the covered checklist must be confirmed, not indeterminate.
        assert aggregate_verdicts(result) == "confirmed"

    def test_prod_paraphrase_rejected_stays_rejected(self):
        verdicts = [_verdict(PROD_VERIFIER_ITEM, Verdict.REJECTED, evidence_ref="os.py:12")]
        result = self._run(verdicts, [PROD_CHECKLIST_ITEM])

        checklist_verdict = result[-1]
        assert checklist_verdict.item == PROD_CHECKLIST_ITEM
        assert checklist_verdict.verdict is Verdict.REJECTED
        assert aggregate_verdicts(result) == "rejected"

    def test_uncovered_item_stays_indeterminate(self):
        verdicts = [_verdict("Export button downloads CSV with all columns", Verdict.CONFIRMED)]
        result = self._run(
            verdicts,
            [
                "Export button downloads CSV with all columns",
                "Export button downloads XLSX with all columns",
            ],
        )

        assert len(result) == 2
        uncovered = result[-1]
        assert uncovered.item == "Export button downloads XLSX with all columns"
        assert uncovered.verdict is Verdict.INDETERMINATE
        assert uncovered.evidence[0]["ref"] == "verifier:missing-item"
        assert aggregate_verdicts(result) == "indeterminate"

    def test_fuzzy_matching_is_one_to_one(self):
        # Two checklist items both similar to one returned verdict: the greedy
        # matcher must assign it to at most one; the other stays indeterminate.
        sole_verdict = _verdict(
            "OS type normalization handles Linux, Windows, Darwin variants",
            Verdict.CONFIRMED,
        )
        result = self._run(
            [sole_verdict],
            [
                "OS type normalization handles Linux, Windows, and Darwin variants",
                "OS type normalization handles Linux, Windows, and Darwin variant names",
            ],
        )

        checklist_verdicts = result[1:]
        statuses = {v.verdict for v in checklist_verdicts}
        assert Verdict.CONFIRMED in statuses
        assert Verdict.INDETERMINATE in statuses
        confirmed = [v for v in checklist_verdicts if v.verdict is Verdict.CONFIRMED]
        assert len(confirmed) == 1

    def test_duplicate_checklist_entries_get_one_synthetic(self):
        # Preserves the pre-#2675 dedup: the second identical checklist entry
        # is skipped once the first has been covered.
        verdicts = [_verdict("Rate limiter caps at 10", Verdict.CONFIRMED)]
        result = self._run(
            verdicts,
            ["Login page renders the SSO button", "Login page renders the SSO button"],
        )
        assert len(result) == 2
        assert result[1].verdict is Verdict.INDETERMINATE

    def test_empty_checklist_and_verdicts(self):
        assert self._run([], []) == []
