# GLM 5h 用量窗 429 暂停自愈 Implementation Plan（#2709）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GLM 上游 5 小时用量窗 429（报文自带 reset 时间）不再烧 transient 重试后 terminal-fail，而是暂停并在报文所述重置时间后由调度器自动恢复。

**Architecture:** 沿用现有 hard-quota 暂停管道（检测→不重试→`_pause_for_upstream_quota`→`UpstreamQuotaPaused`），新增 usage-window 检测（含 UTC+8 reset 时间解析），暂停消息在既有前缀下追加机器可解析的 `resets at <ts> +0800; auto-resume scheduled` 标记；调度器在既有 `_auto_resume_quota_paused` 旁新增第二个扫描，到期即恢复。零 schema 变更。

**Tech Stack:** Python/Flask，`re` + `datetime`（无新依赖）。测试 pytest + unittest.mock，沿用 `tests/unit/test_upstream_quota_pause.py` 的 `AutonomousOrchestrator.__new__` + MagicMock 模式。

**主证据（2026-08-15 两次实测，#2667）：**
```
API Error: Request rejected (429) · [1308][Usage limit reached for 5 hour. Your limit will reset at 2026-08-15 20:59:28][202608151957560f15c8075ec04dd4]
```
报文时间为 **UTC+8**（request-id `20260815195756` 与本机 local 吻合）。注意 `for 5 hour` 是单数、无 's'。

---

### Task 1: orchestrator 检测层（正则 + 两个 helper）

**Files:**
- Modify: `app/modules/workspace/autonomous/orchestrator.py`（常量区 ~L2057 之后；方法区 ~L6985 `_is_upstream_hard_quota_exhausted` 旁）
- Test: `tests/unit/test_upstream_quota_pause.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**（追加到 `tests/unit/test_upstream_quota_pause.py` 末尾）

```python
# --- #2709: GLM 5h usage-window 429 (has reset time -> pause, not retry) ---

GLM_WINDOW_429 = (
    "API Error: Request rejected (429) · [1308][Usage limit reached for 5 hour. "
    "Your limit will reset at 2026-08-15 20:59:28][202608151957560f15c8075ec04dd4]"
)


def test_glm_usage_window_429_is_not_transient():
    result = _result(error=GLM_WINDOW_429)

    assert AutonomousOrchestrator._is_upstream_usage_window_quota(result)
    assert not AutonomousOrchestrator._should_retry_transient_api_failure(result)


def test_glm_usage_window_reset_parses_utc8_as_utc():
    reset = AutonomousOrchestrator._upstream_usage_window_reset(_result(error=GLM_WINDOW_429))

    assert reset is not None
    assert reset.utcoffset().total_seconds() == 0
    assert reset.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-15 12:59:28"


def test_glm_usage_window_without_reset_time_is_detected_but_unparseable():
    result = _result(
        error="API Error: Request rejected (429) · [1308][Usage limit reached for 5 hour.]"
    )

    assert AutonomousOrchestrator._upstream_usage_window_reset(result) is None
    assert AutonomousOrchestrator._is_upstream_usage_window_quota(result)
    assert not AutonomousOrchestrator._should_retry_transient_api_failure(result)


def test_token_bearing_window_text_does_not_match():
    result = _result(
        success=True,
        error=None,
        total_tokens=120,
        response_text=(
            "If you see 'Usage limit reached for 5 hour. Your limit will "
            "reset at 2026-08-15 20:59:28' in logs, back off."
        ),
    )

    assert not AutonomousOrchestrator._is_upstream_usage_window_quota(result)
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/unit/test_upstream_quota_pause.py -k glm_usage_window -v`
Expected: 4 FAILED（`AttributeError: ... has no attribute '_is_upstream_usage_window_quota'`）

- [ ] **Step 3: 最小实现**

3a. 常量区（`_UPSTREAM_HARD_QUOTA_EXHAUSTED_RE` 定义之后、`_CONTEXT_OVERFLOW_RE` 之前）加：

```python
# Provider usage-window quota (GLM/Zhipu [1308]): a rolling window that
# rejects with 429 AND carries the reset timestamp — self-healing, unlike
# the hard platform quota (unknown recovery). Timestamps are provider-local
# UTC+8 (verified 2026-08-15 against request-id stamps on both occurrences).
_UPSTREAM_USAGE_WINDOW_QUOTA_RE = re.compile(
    r"usage\s+limit\s+reached\s+for\s+\d+\s+hours?\b"
    r"(?:\.|\s)*your\s+limit\s+will\s+reset\s+at\s+"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)
_UPSTREAM_WINDOW_TZINFO = timezone(timedelta(hours=8))
```

（确认文件顶部 `from datetime import datetime, timedelta, timezone` 已含 `timedelta`/`timezone`，缺则补。）

3b. `_is_upstream_hard_quota_exhausted`（~L6984）之后加两个新方法：

```python
    @staticmethod
    def _upstream_window_bodies(result: AgentTaskResult) -> list[str]:
        """Candidate bodies for usage-window matching, same evidence rules as
        ``_is_upstream_hard_quota_exhausted``: the error field is authoritative;
        assistant text only for a zero-token envelope (#1891 false-retry guard).
        """
        bodies = [result.error or ""]
        if (result.total_tokens or 0) == 0:
            bodies.append(result.response_text or "")
        return bodies

    @classmethod
    def _is_upstream_usage_window_quota(cls, result: AgentTaskResult) -> bool:
        """Whether a provider reports a rolling usage-window quota (GLM 5h).

        Distinct from the hard platform quota: the window resets on its own,
        so the workflow pauses instead of retrying and auto-resumes at the
        stated time (see scheduler). No-reset-time variants still match the
        wording and pause (operator-resumed) — fail-closed, never transient.
        """
        return any(
            _UPSTREAM_USAGE_WINDOW_QUOTA_RE.search(body)
            for body in cls._upstream_window_bodies(result)
        )

    @classmethod
    def _upstream_usage_window_reset(cls, result: AgentTaskResult) -> datetime | None:
        """Parse the reset timestamp of a usage-window 429 into aware UTC.

        Returns None when absent/unparseable — callers then pause without the
        auto-resume marker (operator resume). Provider times are UTC+8.
        """
        for body in cls._upstream_window_bodies(result):
            match = _UPSTREAM_USAGE_WINDOW_QUOTA_RE.search(body)
            if match:
                try:
                    naive = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return None
                return naive.replace(tzinfo=_UPSTREAM_WINDOW_TZINFO).astimezone(timezone.utc)
        return None
```

3c. `_should_retry_transient_api_failure`（~L6967）的 hard-quota 行后加一行：

```python
        if cls._is_upstream_hard_quota_exhausted(result):
            return False
        if cls._is_upstream_usage_window_quota(result):
            return False
```

- [ ] **Step 4: 跑测试确认绿 + 既有用例不回归**

Run: `python -m pytest tests/unit/test_upstream_quota_pause.py -v`
Expected: 全 PASS（新增 4 + 既有 9）

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/orchestrator.py tests/unit/test_upstream_quota_pause.py
git commit -m "feat: detect GLM usage-window 429 as non-transient (Implements #2709)"
```

---

### Task 2: 暂停消息带 reset 标记 + `_run_agent` 接线

**Files:**
- Modify: `app/modules/workspace/autonomous/orchestrator.py`（`_pause_for_upstream_quota` ~L6999；`_run_agent` 尾部 ~L7768-7783）
- Test: `tests/unit/test_upstream_quota_pause.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_run_agent_pauses_for_glm_usage_window_with_resume_marker():
    orchestrator = _orchestrator_for_run(_result(error=GLM_WINDOW_429))

    with pytest.raises(UpstreamQuotaPaused):
        orchestrator._run_agent(
            wf={"user_id": 1, "content_language": "en"},
            session_line="main",
            milestone_id="milestone-1",
            workspace_type="remote",
            project_path="/tmp/worktree",
            prompt="do work",
        )

    orchestrator._runner.run_agent_task.assert_called_once()
    update = next(
        call.args[1]
        for call in orchestrator.repo.update_workflow.call_args_list
        if call.args[1].get("status") == "paused"
    )
    assert update["error_message"].startswith("Upstream provider quota exhausted:")
    assert "resets at 2026-08-15 20:59:28 +0800" in update["error_message"]
    assert "auto-resume scheduled" in update["error_message"]


def test_run_agent_pauses_glm_window_without_reset_time_as_operator_resume():
    orchestrator = _orchestrator_for_run(
        _result(error="API Error: Request rejected (429) · [1308][Usage limit reached for 5 hour.]")
    )

    with pytest.raises(UpstreamQuotaPaused):
        orchestrator._run_agent(
            wf={"user_id": 1, "content_language": "en"},
            session_line="main",
            milestone_id="milestone-1",
            workspace_type="remote",
            project_path="/tmp/worktree",
            prompt="do work",
        )

    update = next(
        call.args[1]
        for call in orchestrator.repo.update_workflow.call_args_list
        if call.args[1].get("status") == "paused"
    )
    assert update["error_message"].startswith("Upstream provider quota exhausted:")
    assert "auto-resume scheduled" not in update["error_message"]
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/unit/test_upstream_quota_pause.py -k run_agent_pauses_glm -v`
Expected: 2 FAILED（暂停消息不含标记——现为通用 hard-quota 文案，且 `_run_agent` 不检测窗口）

- [ ] **Step 3: 最小实现**

3a. `_pause_for_upstream_quota` 签名与消息改为：

```python
    def _pause_for_upstream_quota(
        self,
        result: AgentTaskResult,
        milestone_id: str = "",
        resume_at: datetime | None = None,
    ) -> None:
        """Persist a non-spinning hard-quota pause that an operator may resume.

        ``resume_at`` (provider usage-window 429 with a stated reset time)
        additionally writes the scheduler-parseable auto-resume marker: the
        timestamp is rendered back in provider-local UTC+8 so the message
        reads correctly for operators too.
        """
        if resume_at is not None:
            local = resume_at.astimezone(_UPSTREAM_WINDOW_TZINFO).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            detail = (
                "provider usage window exhausted; "
                f"limit resets at {local} +0800; auto-resume scheduled"
            )
        else:
            detail = (
                "the configured model provider rejected requests; resume after "
                "provider allocation is restored"
            )
        message = f"{UPSTREAM_QUOTA_PAUSE_REASON_PREFIX} {detail}"
```

（方法其余部分不动。）

3b. `_run_agent` 尾部（`upstream_hard_quota_exhausted = ...` 行，~L7768）改为：

```python
        upstream_hard_quota_exhausted = self._is_upstream_hard_quota_exhausted(result)
        upstream_window_quota = self._is_upstream_usage_window_quota(result)
        upstream_window_reset = (
            self._upstream_usage_window_reset(result) if upstream_window_quota else None
        )
```

对应暂停分支（~L7780）改为：

```python
        if upstream_window_quota:
            self._pause_for_upstream_quota(result, milestone_id, resume_at=upstream_window_reset)
            raise UpstreamQuotaPaused(result.error)
        if upstream_hard_quota_exhausted:
            self._pause_for_upstream_quota(result, milestone_id)
            raise UpstreamQuotaPaused(result.error)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/unit/test_upstream_quota_pause.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/orchestrator.py tests/unit/test_upstream_quota_pause.py
git commit -m "feat: pause GLM usage-window 429 with auto-resume marker (Implements #2709)"
```

---

### Task 3: 调度器到期自愈扫描

**Files:**
- Modify: `app/services/autonomous_scheduler.py`（imports L19；常量区 `_is_quota_paused` 后 ~L159；新方法 `_auto_resume_quota_paused` 后 ~L823；调用点 ~L926）
- Test: Create `tests/unit/test_scheduler_upstream_window_resume.py`

- [ ] **Step 1: 写失败测试**（新文件）

```python
"""Scheduler auto-resume of usage-window (GLM 5h) upstream quota pauses (#2709)."""

from datetime import datetime, timedelta, timezone

from unittest.mock import MagicMock, patch

from app.services.autonomous_scheduler import AutonomousScheduler


UTC8 = timezone(timedelta(hours=8))

PAUSED_WINDOW = {
    "workflow_id": "wf-window-1",
    "status": "paused",
    "current_phase": "pr_review",
    "error_message": (
        "Upstream provider quota exhausted: provider usage window exhausted; "
        "limit resets at 2026-08-15 20:59:28 +0800; auto-resume scheduled"
    ),
}


def _scheduler() -> AutonomousScheduler:
    return AutonomousScheduler.__new__(AutonomousScheduler)


def _repo(*workflows) -> MagicMock:
    repo = MagicMock()
    repo.get_paused_workflows.return_value = list(workflows)
    return repo


def test_resumes_after_stated_reset_time():
    repo = _repo(PAUSED_WINDOW)

    with patch("app.routes.autonomous._emit_event_safe") as emit:
        _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_called_once()
    args = repo.update_workflow.call_args.args
    assert args[0] == "wf-window-1"
    assert args[1] == {
        "status": "pr_review",
        "paused_at": None,
        "error_message": "",
    }
    emit.assert_called_once_with("wf-window-1", "status_change", {"status": "pr_review"})


def test_does_not_resume_before_reset_time():
    future_local = (datetime.now(timezone.utc) + timedelta(hours=2)).astimezone(UTC8)
    wf = dict(
        PAUSED_WINDOW,
        error_message=PAUSED_WINDOW["error_message"].replace(
            "2026-08-15 20:59:28", future_local.strftime("%Y-%m-%d %H:%M:%S")
        ),
    )
    repo = _repo(wf)

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()


def test_hard_quota_pause_without_marker_is_left_for_operator():
    repo = _repo(
        {
            "workflow_id": "wf-hard-1",
            "status": "paused",
            "current_phase": "development",
            "error_message": (
                "Upstream provider quota exhausted: the configured model provider "
                "rejected requests; resume after provider allocation is restored"
            ),
        }
    )

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()


def test_unparseable_timestamp_is_left_for_operator():
    repo = _repo(
        {
            "workflow_id": "wf-bad-1",
            "status": "paused",
            "current_phase": "development",
            "error_message": (
                "Upstream provider quota exhausted: provider usage window exhausted; "
                "limit resets at not-a-time +0800; auto-resume scheduled"
            ),
        }
    )

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/unit/test_scheduler_upstream_window_resume.py -v`
Expected: 4 ERROR/FAILED（`AttributeError: no attribute '_auto_resume_upstream_window_paused'`）

- [ ] **Step 3: 最小实现**

3a. import 行（L19）改为含 `timedelta`：

```python
from datetime import datetime, timedelta, timezone
```

3b. `_is_quota_paused`（~L158）之后加常量：

```python
# Marker written by the orchestrator's usage-window pause (GLM 5h quota,
# #2709). Both tokens are required so the hard platform-quota pause (no
# reset time, operator-resumed) can never match. The orchestrator renders
# the timestamp in provider-local UTC+8; the parser applies the same fixed
# offset back to UTC.
_UPSTREAM_WINDOW_RESUME_RE = re.compile(
    r"resets at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \+0800[;,]?\s*auto-resume scheduled",
    re.IGNORECASE,
)
_UPSTREAM_WINDOW_TZINFO = timezone(timedelta(hours=8))
# Resume a minute after the stated reset so a boundary-eager window (or
# small clock skew) does not 429 the first resumed request.
_UPSTREAM_WINDOW_RESUME_MARGIN = timedelta(seconds=60)
```

3c. `_auto_resume_quota_paused` 方法之后（~L823）加新方法：

```python
    def _auto_resume_upstream_window_paused(self, repo) -> None:
        """Resume usage-window (GLM 5h) upstream-quota pauses at the stated reset time.

        Only pauses whose error_message carries both a parseable reset
        timestamp and the auto-resume marker are resumed; the hard platform
        quota (no reset time) stays operator-resumed, as do unparseable
        messages. Fail-closed: query/parse/update errors leave the workflow
        paused for the next cycle.
        """
        from app.routes.autonomous import PHASE_TO_STATUS, _emit_event_safe

        try:
            paused = repo.get_paused_workflows(UPSTREAM_QUOTA_PAUSE_REASON_PREFIX)
        except Exception as e:
            logger.error(
                "Failed to query upstream-paused workflows for window resume: %s", e
            )
            return

        for wf in paused:
            match = _UPSTREAM_WINDOW_RESUME_RE.search(wf.get("error_message") or "")
            if not match:
                continue
            try:
                reset_at = (
                    datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    .replace(tzinfo=_UPSTREAM_WINDOW_TZINFO)
                    .astimezone(timezone.utc)
                )
            except ValueError:
                continue
            if datetime.now(timezone.utc) < reset_at + _UPSTREAM_WINDOW_RESUME_MARGIN:
                continue

            status = PHASE_TO_STATUS.get(wf.get("current_phase", "preparation"), "pending")
            try:
                repo.update_workflow(
                    wf["workflow_id"],
                    {"status": status, "paused_at": None, "error_message": ""},
                )
                _emit_event_safe(wf["workflow_id"], "status_change", {"status": status})
                logger.info(
                    "Auto-resumed usage-window-paused workflow %s (window reset at %s)",
                    wf["workflow_id"][:8],
                    match.group(1),
                )
            except Exception as e:
                logger.error(
                    "Failed to auto-resume usage-window-paused workflow %s: %s",
                    wf["workflow_id"][:8],
                    e,
                )
```

（`UPSTREAM_QUOTA_PAUSE_REASON_PREFIX` 在方法内 `from app.modules.workspace.autonomous.orchestrator import UPSTREAM_QUOTA_PAUSE_REASON_PREFIX` —— 与既有函数内 import 风格一致。）

3d. 调用点（`self._auto_resume_quota_paused(repo)` 之后，~L926）加：

```python
        # Resume workflows paused by a provider usage-window 429 (GLM 5h,
        # carries its own reset time) once that time has passed (#2709).
        self._auto_resume_upstream_window_paused(repo)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/unit/test_scheduler_upstream_window_resume.py tests/unit/test_upstream_quota_pause.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/autonomous_scheduler.py tests/unit/test_scheduler_upstream_window_resume.py
git commit -m "feat: scheduler auto-resumes usage-window quota pauses at reset time (Implements #2709)"
```

---

### Task 4: 本地全量验证 + PR

- [ ] **Step 1: 相关全量测试**（含 legacy 配额门测试，CI 不跑 tests/issues 故本地必须跑）

Run:
```bash
python -m pytest tests/unit/test_upstream_quota_pause.py \
                 tests/unit/test_scheduler_upstream_window_resume.py \
                 tests/issues/716/test_autonomous_quota_gate.py \
                 tests/issues/2295/test_scheduler_per_user_concurrency.py -v
```
Expected: 全 PASS（716/2295 若因本地环境 fail，对照 origin/main worktree 判定是否 pre-existing）

- [ ] **Step 2: pre-commit（只跑改动文件，防 --all-files 级联）**

Run: `pre-commit run --files app/modules/workspace/autonomous/orchestrator.py app/services/autonomous_scheduler.py tests/unit/test_upstream_quota_pause.py tests/unit/test_scheduler_upstream_window_resume.py`
Expected: 全过

- [ ] **Step 3: push + 建 PR**（⚠️ 标题禁用 closes/fixes/resolves + #编号——auto-close 陷阱；用 Implements）

```bash
git push -u origin worktree-fix-2709-glm-quota-pause
gh pr create --title "fix: pause + auto-resume GLM 5h usage-window 429 instead of burning transient retries (Implements #2709)" --body-file - <<'EOF'
## 问题（#2709）

GLM 上游 5 小时用量窗耗尽时返回 `429 [1308] Usage limit reached for 5 hour. Your limit will reset at <ts>`（ts 为 UTC+8）。现有分类把它当 transient（宽匹配 429/quota）→ backoff 重试全部打墙 → `Transient API error not resolved after retries` → 工作流 failed（#2667 于 08-15 两次实证）。窗口 ≤5h 自然重置且报文自带重置时间——正确语义是暂停、到期自愈。

## 改动

1. **检测**（orchestrator）：新正则匹配 usage-window 措辞并捕获 reset ts（error 权威 / response_text 仅 zero-token 采信，沿用 hard-quota 防误伤）；`_should_retry_transient_api_failure` 对窗口 429 返回 False（首个 429 即停）。
2. **暂停**（orchestrator）：`_pause_for_upstream_quota(resume_at=)` 消息保持 `Upstream provider quota exhausted:` 前缀（resume API 继续识别）+ 追加 `resets at <ts> +0800; auto-resume scheduled`；无 ts 退化为既有操作员恢复文案。
3. **自愈**（scheduler）：`_auto_resume_upstream_window_paused` 扫描该前缀 paused，双 token 标记 + ts 解析（UTC+8→UTC），`now ≥ reset+60s` 才恢复（PHASE_TO_STATUS 映射）；解析失败/无标记 fail-closed 留给操作员。

设计 spec：docs/superpowers/specs/2026-08-16-glm-5h-quota-pause-design.md

## 不做

零 schema 变更（error_message 作协议载体，#2673 先例）；不改既有 transient/hard-quota 正则语义；不泛化任意 provider 窗口框架。
EOF
```

- [ ] **Step 4: 确认 commit 是最新**（pre-commit 中断陷阱：`git log -1` + status clean 再 push 已在 Step 3 前确认）

- [ ] **Step 5: 等 CI 绿后通知用户审查**（不自行合并）

---

## Self-Review 记录

- Spec 覆盖：检测（Task 1）/不重试（Task 1.3c）/暂停标记（Task 2）/调度器自愈+margin+fail-closed（Task 3）/测试矩阵 6 项（Task 1×4 + Task 2×2 + Task 3×4）/不做清单 ✅
- 类型一致：`_upstream_window_bodies`/`_is_upstream_usage_window_quota`/`_upstream_usage_window_reset`/`_pause_for_upstream_quota(resume_at:)`/`_auto_resume_upstream_window_paused` 名称全文一致；`_UPSTREAM_WINDOW_TZINFO` 双文件同名同值（各文件独立常量，非跨模块导入）✅
- 无占位符：所有步骤含完整代码/命令/期望输出 ✅
- 与 spec 的偏差（已核对为等价实现）：spec 说"同函数扩展第二个扫描"，实现为新方法 + 同一调用点（`_auto_resume_quota_paused` 的恢复判定是 QuotaManager 用户配额，与窗口时间判定无共享逻辑，拆开更清晰）。
