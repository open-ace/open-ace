# Verifier glm-5 prose-not-JSON — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop glm-5 acceptance-verifier runs from stranding workflows at `acceptance_verification` with `infra_error: output was not valid JSON`, by (a) making the prompt force JSON-only output with terse evidence and (b) recovering truncated JSON in the extractor.

**Architecture:** Two surgical edits in `app/modules/workspace/autonomous/orchestrator.py`. No schema, no new module.

**Tech Stack:** Python, pytest.

## Root cause (confirmed via prod scheduler logs, 2026-08-12)

`journalctl -u openace-scheduler.service` shows the failing verifier runs. Of 4 sampled `raw_output`s:
- **3 had ZERO JSON braces** — glm-5 emitted a markdown verification report (`## 验收总结`, `✅9/10 ❌...`) and never produced the mandated fenced JSON block. `_extract_verifier_json` returns `None` → `infra_error: output was not valid JSON` → 3 identical retries → pause.
- **1 started JSON but was truncated** mid-object (open 11 / close 8) — `_first_balanced` finds no closing brace → `None` → same infra_error.

Affects `2d0c317d` (#2328), `cd939cbf` (#2349), `b48179df` (#2394) — infra_retry 3/3/7. Prior fixes #29 (trailing-comma tolerance) and #30 (demand snapshot extraction) addressed adjacent symptoms but NOT the core "model emits prose instead of JSON" failure.

## File Structure

- Modify: `app/modules/workspace/autonomous/orchestrator.py`
  - `_extract_verifier_json` (L2115) — add truncated-JSON recovery.
  - `_build_verification_prompt` (L7786) — JSON-only contract first, cap evidence.
- Test: `tests/unit/test_verifier_json_parse.py` (add truncation-recovery cases).
- Test: `tests/unit/test_verifier_snapshot_prompt.py` (add JSON-only-contract cases).

## Tasks

### Task 1: Extractor recovers truncated fenced JSON

**Files:**
- Modify: `orchestrator.py:_extract_verifier_json` (add `_repair_truncated_json_object` helper)
- Test: `tests/unit/test_verifier_json_parse.py`

- [ ] **Step 1: Write failing tests** — truncated fenced block recovers the complete leading verdicts; truncated unfenced object recovers; mid-evidence-array truncation drops the incomplete verdict (conservative).
- [ ] **Step 2: Run → RED** (`pytest tests/unit/test_verifier_json_parse.py -k truncated -v`)
- [ ] **Step 3: Implement** `_repair_truncated_json_object` (string-aware stack walk; try progressively shorter cut points back to the last complete `}`/`]`, append reverse of open stack, strip trailing commas, `json.loads`; return longest parseable dict or None). Call it from `_extract_verifier_json` after the balanced attempts fail, before returning None.
- [ ] **Step 4: Run → GREEN** (all extractor tests pass).
- [ ] **Step 5: Commit.**

### Task 2: Prompt forces JSON-only, caps evidence

**Files:**
- Modify: `orchestrator.py:_build_verification_prompt`
- Test: `tests/unit/test_verifier_snapshot_prompt.py`

- [ ] **Step 1: Write failing tests** — prompt leads with a JSON-only/no-preamble contract that appears BEFORE the snapshot dump; prompt caps evidence (≤2 refs, short notes); existing `_MARKER` extraction + snapshot-token tests stay green.
- [ ] **Step 2: Run → RED.**
- [ ] **Step 3: Implement** — move the format contract to the top of the prompt; add the evidence cap; reinforce "ONLY JSON, no prose" at the end; keep `_MARKER` and snapshot-token logic intact.
- [ ] **Step 4: Run → GREEN** (all prompt tests pass).
- [ ] **Step 5: Commit.**

### Task 3: Review, PR, deploy, reset, verify

- [ ] Independent code review (superpowers:requesting-code-review) on `gh pr diff`.
- [ ] CI green (lint / test(3.10-3.12) / build).
- [ ] Merge + deploy hotpatch (cp app/ + restart scheduler + curl :9091/livez).
- [ ] Reset 2d0c317d/cd939cbf/b48179df → status=verification_pending, clear verification_report/status/attempt + retry fields, restore worktree_path per skill COALESCE.
- [ ] Monitor → confirm the verifier produces parseable JSON and the workflows advance past acceptance_verification (no `not valid JSON` infra_error).

## Safety

Truncation recovery cannot false-confirm: any checklist item whose verdict was truncated is simply absent from the recovered `verdicts`, and `acceptance_verification.handle` (L464-483) forces absent checklist items to `indeterminate`, so the aggregate never becomes `confirmed` on incomplete evidence. A recovered malformed trailing verdict is dropped by the phase handler's per-verdict validation (L388-433). Both paths are conservative.
