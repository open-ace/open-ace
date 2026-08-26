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

    def test_bare_word_prefix_does_not_cross_match(self):
        # Review round 1 (#2677): character-level containment let a bare
        # short word match any string merely containing it as a character
        # substring ("Auth" inside "OAuth2"/"Author"). Containment must be
        # token-boundary aware, so these fall through to Jaccard 0.
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert _items_match_fuzzy("Auth", "OAuth2 token refresh flow works") == 0.0
        assert _items_match_fuzzy("Auth", "Author page shows avatar") == 0.0

    def test_containment_requires_at_least_three_tokens(self):
        # Even a token-boundary-aligned containment with fewer than 3 tokens
        # on the short side is not evidence of the same requirement.
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy("Rate limiter", "Rate limiter caps at 10 requests per minute") < 0.8
        )

    def test_containment_requires_half_the_long_token_count(self):
        # A 3-token item buried in a 10-token elaboration is a different
        # requirement; containment is denied and Jaccard stays below 0.8.
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy(
                "Login page renders",
                "Login page renders the SSO button with animation and focus trap",
            )
            < 0.8
        )

    def test_negation_flip_never_matches(self):
        # A negation flip is a semantic OPPOSITE, never a paraphrase: matching
        # it could auto-confirm a criterion the delivery violates.
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy(
                "The retention job must not delete records older than 30 days",
                "The retention job must delete records older than 30 days",
            )
            == 0.0
        )

    def test_negation_flip_with_contraction_never_matches(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy(
                "The export mustn't overwrite existing files",
                "The export must overwrite existing files",
            )
            == 0.0
        )

    def test_negation_preserving_paraphrase_still_matches(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        assert (
            _items_match_fuzzy(
                "The retention job must not delete records older than 30 days",
                "The retention job must not delete records older than thirty days",
            )
            >= 0.8
        )


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

    def test_short_word_prefix_stays_indeterminate_fail_closed(self):
        # "Auth" must not inherit the CONFIRMED verdict of an unrelated
        # "OAuth2 token refresh flow works" item.
        verdicts = [_verdict("OAuth2 token refresh flow works", Verdict.CONFIRMED)]
        result = self._run(verdicts, ["Auth"])

        assert len(result) == 2
        assert result[-1].item == "Auth"
        assert result[-1].verdict is Verdict.INDETERMINATE
        assert result[-1].evidence[0]["ref"] == "verifier:missing-item"
        assert aggregate_verdicts(result) == "indeterminate"

    def test_negation_flip_does_not_inherit_verdict_fail_closed(self):
        # The verifier confirmed the OPPOSITE of the checklist item ("must
        # delete" vs "must not delete"). Reusing that verdict would
        # auto-close an issue the delivery demonstrably violates; the item
        # must stay indeterminate for human follow-up.
        verdicts = [
            _verdict(
                "The retention job must delete records older than 30 days",
                Verdict.CONFIRMED,
            )
        ]
        result = self._run(
            verdicts, ["The retention job must not delete records older than 30 days"]
        )

        assert len(result) == 2
        assert result[-1].verdict is Verdict.INDETERMINATE
        assert result[-1].evidence[0]["ref"] == "verifier:missing-item"
        assert aggregate_verdicts(result) == "indeterminate"

    def test_cjk_paraphrase_without_spaces_stays_indeterminate_fail_closed(self):
        # Known boundary: Chinese acceptance items written without spaces
        # tokenize as a single token, so even a close paraphrase scores
        # Jaccard 0 and must stay fail-closed (INDETERMINATE), never
        # silently confirmed or dropped.
        verdicts = [_verdict("登录页面上展示SSO按钮", Verdict.CONFIRMED)]
        result = self._run(verdicts, ["登录页面显示单点登录按钮"])

        assert len(result) == 2
        assert result[-1].item == "登录页面显示单点登录按钮"
        assert result[-1].verdict is Verdict.INDETERMINATE
        assert result[-1].evidence[0]["ref"] == "verifier:missing-item"
        assert aggregate_verdicts(result) == "indeterminate"

    def test_fuzzy_matched_verdict_copies_evidence_list(self):
        verdicts = [_verdict(PROD_VERIFIER_ITEM, Verdict.CONFIRMED, evidence_ref="os.py:12")]
        result = self._run(verdicts, [PROD_CHECKLIST_ITEM])

        checklist_verdict = result[-1]
        assert checklist_verdict.evidence is not verdicts[0].evidence
        assert checklist_verdict.evidence == verdicts[0].evidence

    def test_duplicate_checklist_entries_do_not_consume_verdicts(self):
        # Dedup happens before fuzzy-candidate generation: a duplicate
        # checklist entry (discarded by the extension loop anyway) must not
        # consume the only verdict that could cover a distinct later item.
        verdicts = [
            _verdict("The rate limiter caps at 10 requests per minute", Verdict.CONFIRMED),
            _verdict("Rate limiter caps at 10 requests per minute strictly", Verdict.REJECTED),
        ]
        result = self._run(
            verdicts,
            [
                "Rate limiter caps at 10 requests per minute",
                "Rate limiter caps at 10 requests per minute",
                "Rate limiter caps at 10 requests per minute strictly enforced",
            ],
        )
        appended = result[2:]
        by_item = {v.item: v.verdict for v in appended}
        assert by_item["Rate limiter caps at 10 requests per minute"] is Verdict.CONFIRMED
        # Without dedup, the duplicate at index 1 greedily consumed the
        # "strictly" verdict and this item fell to INDETERMINATE.
        assert (
            by_item["Rate limiter caps at 10 requests per minute strictly enforced"]
            is Verdict.REJECTED
        )


# -- CJK-aware matching (#2982) ------------------------------------------------
# Whitespace tokenization made a whole Chinese phrase a single token, so a
# verbatim-but-shortened verifier item shared ZERO tokens with its checklist
# elaboration and every similarity path degenerated to 0 (prod evidence:
# #2537/#2828 fully-confirmed deliveries went indeterminate). CJK runs now
# tokenize as character bigrams; the pairs below are the real prod strings.


class TestCjkFuzzyMatching:
    pytestmark = [pytest.mark.regression, pytest.mark.issue(2982)]

    # Real prod pair (#2537): checklist is the verifier item plus an
    # identifier enumeration. Short side is a contiguous bigram prefix of the
    # long side (7 bigrams + 4 latin tokens, ratio 7/11 >= 0.5) -> 1.0.
    PROD_2537_VERIFIER = "定义机器状态常量"
    PROD_2537_CHECKLIST = (
        "定义机器状态常量（_STATUS_ONLINE, _STATUS_IDLE, _STATUS_BUSY, _STATUS_OFFLINE）"
    )

    # Real prod pair (#2828): checklist inserts （YYYYMMDD） mid-phrase. The
    # short side is ONE 17-char run (16 bigrams incl. the 期不 bridge); the
    # long side is two runs (15 bigrams) + 1 latin token. Weighted Jaccard
    # (bigrams 1x, latin token 2x): 15 / (15+1+2) = 0.833 — margin over the
    # 0.8 bar is only 0.033; assert >= 0.8 and keep the exact value in sync
    # if weights ever change.
    PROD_2828_VERIFIER = "紧凑日期不再被误识别为国际电话号码"
    PROD_2828_CHECKLIST = "紧凑日期（YYYYMMDD）不再被误识别为国际电话号码"

    # Real prod pair (#2239): the parenthetical elaboration is long relative
    # to the base (9 vs 23 tokens), below both the 0.5 containment ratio and
    # the 0.8 Jaccard bar -> intentionally NOT matched (conservative; the
    # verifier verbatim-echo prompt is the primary fix for this shape).
    PROD_2239_VERIFIER = "创建远程目录API接受'busy'状态"
    PROD_2239_CHECKLIST = "创建远程目录API接受'busy'状态(有活跃会话但仍然连接的机器)"

    def _fuzzy(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _items_match_fuzzy,
        )

        return _items_match_fuzzy

    def test_prod_2537_verbatim_shortened_item_matches(self):
        assert self._fuzzy()(self.PROD_2537_VERIFIER, self.PROD_2537_CHECKLIST) == 1.0

    def test_prod_2828_infix_elaboration_item_matches(self):
        score = self._fuzzy()(self.PROD_2828_VERIFIER, self.PROD_2828_CHECKLIST)
        assert score >= 0.8, f"prod #2828 pair must match, got {score}"

    def test_prod_2239_long_elaboration_stays_below_threshold(self):
        assert self._fuzzy()(self.PROD_2239_VERIFIER, self.PROD_2239_CHECKLIST) < 0.8

    def test_fuzzy_tokens_split_cjk_runs_into_bigrams(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import _fuzzy_tokens

        # CJK runs become in-run bigrams; non-CJK segments stay whitespace
        # tokens, so English sequences are unchanged token-for-token.
        assert _fuzzy_tokens("定义机器状态常量") == [
            "定义",
            "义机",
            "机器",
            "器状",
            "状态",
            "态常",
            "常量",
        ]
        assert _fuzzy_tokens("rate limiter caps at 10") == [
            "rate",
            "limiter",
            "caps",
            "at",
            "10",
        ]
        # A 1-char CJK run yields the char itself; a 2-char run one bigram.
        assert _fuzzy_tokens("重试 retry") == ["重试", "retry"]
        assert _fuzzy_tokens("点 retry") == ["点", "retry"]

    def test_cjk_negation_flip_never_matches(self):
        # "不再删除旧记录" vs "再删除旧记录" differ ONLY by the negation head
        # (Jaccard 5/6 = 0.833 would match without the guard) — the flip must
        # force 0.0.
        assert self._fuzzy()("不再删除旧记录", "再删除旧记录") == 0.0

    def test_cjk_single_char_negation_head_flips(self):
        # 不+verb is the most common Chinese negation; the head-char rule must
        # catch it even though neither phrase contains a multi-char marker.
        assert self._fuzzy()("支持重试且不删除数据", "支持重试并删除数据") == 0.0

    def test_cjk_negation_on_both_sides_still_matches(self):
        # Same 不再 on both sides (marker sets equal): a one-char CJK tail
        # elaboration keeps the weighted Jaccard at 6/7 = 0.857.
        assert self._fuzzy()("不再删除旧记录", "不再删除旧记录了") >= 0.8

    def test_cjk_negation_head_false_positive_is_conservative(self):
        # 未来 opens with the negation head 未 but is NOT a negation. A
        # one-sided 未来 only makes the pair NOT match (conservative loss of
        # coverage), never a false confirmation — and when 未来 appears on
        # both sides the markers cancel and matching is unaffected.
        assert self._fuzzy()("未来版本支持该参数", "版本支持该参数") == 0.0
        assert self._fuzzy()("未来版本支持该参数", "未来版本支持该参数。") >= 0.8

    def test_isolated_single_char_negation_segment_flips(self):
        # An isolated 无 between runs is a single-char token; its head must
        # still flip (without the guard this pair would score 4/5 = 0.8).
        assert self._fuzzy()("发送 无限制通知", "发送 限制通知") == 0.0

    def test_distinct_cjk_requirements_sharing_vocabulary_do_not_match(self):
        # Genuinely different Chinese requirements that share many characters:
        # token (bigram) Jaccard must stay below 0.8.
        assert (
            self._fuzzy()(
                "导出按钮下载CSV包含所有列",
                "导出按钮下载XLSX包含所有列",
            )
            < 0.8
        )

    def test_english_behavior_unchanged_token_for_token(self):
        # The pre-#2982 English outcomes must hold identically.
        assert self._fuzzy()("Auth", "OAuth2 token refresh flow works") == 0.0
        assert (
            self._fuzzy()(
                "OS type normalization handles Linux, Windows, and Darwin variants",
                "OS type normalization handles Linux, Windows, Darwin variants",
            )
            >= 0.8
        )


class TestCjkCoverageIntegration:
    pytestmark = [pytest.mark.regression, pytest.mark.issue(2982)]

    def test_prod_2537_confirmed_verdict_covers_checklist(self):
        from app.modules.workspace.autonomous.phases.acceptance_verification import (
            _cover_missing_checklist_items,
        )

        verdicts = [_verdict("定义机器状态常量", Verdict.CONFIRMED, evidence_ref="manager.py:20")]
        result = _cover_missing_checklist_items(
            verdicts,
            ["定义机器状态常量（_STATUS_ONLINE, _STATUS_IDLE, _STATUS_BUSY, _STATUS_OFFLINE）"],
        )
        assert len(result) == 2
        assert result[-1].verdict is Verdict.CONFIRMED
        assert aggregate_verdicts(result) == "confirmed"
