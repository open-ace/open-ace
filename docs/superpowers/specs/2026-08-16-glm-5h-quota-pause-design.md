# Design: GLM 5h 用量窗 429 纳入上游配额暂停并自愈（#2709）

## 问题（根因，已用主证据核实）

GLM 上游按 5 小时滚动窗口计用量，耗尽时返回：

```
API Error: Request rejected (429) · [1308][Usage limit reached for 5 hour.
Your limit will reset at 2026-08-15 20:59:28][<request-id>]
```

- 报文时间戳为 **UTC+8**（实证：request-id `20260815195756` 与本机 local 时间吻合，08-15 两次发生 05:12/11:16 UTC）。
- 现有分类把它判成 transient（`_TRANSIENT_API_ERROR_RE` 宽匹配 429/quota），`_is_upstream_hard_quota_exhausted` 只认 `platform quota exceeded|upstream ... quota exhausted` 措辞 → backoff 重试全部打墙 → `Transient API error not resolved after retries` → 工作流 failed（#2667 两次实证）。
- 窗口 ≤5h 自然重置且报文自带 reset 时间——正确语义是「暂停、到期自愈」，而非烧重试后 terminal-fail。

## 方案（已选 A；B=加 paused_until 列、C=仅暂停不自愈，均否）

### 1. 检测（orchestrator.py）

- 新常量 `_UPSTREAM_USAGE_WINDOW_QUOTA_RE`：匹配 `usage limit reached for <N> hour(s)` 且捕获 `your limit will reset at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})`。不要求 `[1308]`（措辞即契约），不匹配不带 reset ts 的变体（fail-closed：无恢复时间不自愈）。
- 新函数 `_upstream_usage_window_reset(result) -> datetime | None`：按上述防御取值——`result.error` 权威；`result.response_text` 仅在 `total_tokens==0` 时采信（沿用 `_is_upstream_hard_quota_exhausted` 的防 prose 误伤）。ts 解析为 **UTC+8**（`timezone(timedelta(hours=8))`）转 UTC 返回。
- `_should_retry_transient_api_failure`：窗口命中 → False（首个 429 即停，不烧 backoff）。
- run-agent wrapper（现 L7768 处）：检测顺序 窗口 → hard quota；窗口命中调 `_pause_for_upstream_quota(result, milestone_id, resume_at=ts)`。

### 2. 暂停消息（orchestrator.py）

`_pause_for_upstream_quota(..., resume_at: datetime | None = None)`：

- 消息保持既有前缀 `Upstream provider quota exhausted:`（`routes/autonomous.py::_is_recoverable_system_pause_reason` 继续识别，操作员手动恢复不受影响）。
- `resume_at` 非空时追加机器可解析标记：`... ; auto-resume at 2026-08-15 20:59:28 +0800`（UTC+8 原文呈现，解析方按 +8 转 UTC）。
- 其余行为不变：milestone failed + workflow paused + `paused_at` + `UpstreamQuotaPaused`。

### 3. 调度器自愈（autonomous_scheduler.py `_resume_quota_paused_workflows`）

同函数扩展第二个扫描：`get_paused_workflows(UPSTREAM_QUOTA_PAUSE_REASON_PREFIX)`，对每条：

- `error_message` 解析 `auto-resume at <ts> +0800`；解析不出（含既有 hard-quota 暂停，无该标记）→ **跳过**（fail-closed，操作员兜底）。
- `datetime.now(timezone.utc) >= resume_at_utc + 60s` 才恢复；恢复动作与既有路径一致（`PHASE_TO_STATUS` 映射 status、清 `paused_at`/`error_message`、emit status_change）。
- 提前误恢复（时钟偏差/措辞变化）→ agent 再撞 429 → 再暂停（自愈闭环，无热循环：每轮暂停至少隔一个调度周期）。

### 4. 错误处理汇总

| 情形 | 行为 |
|---|---|
| ts 解析失败/缺失 | 退化为现有 hard-quota 暂停，操作员恢复 |
| 到期前 | 不恢复（每周期重扫，纯比较，零成本） |
| 恢复后仍 429 | 再暂停，重新带新 ts |
| prose 提及限流措辞 | zero-token 门防误伤（沿用现有防御） |

## 测试（canonical：tests/unit/test_upstream_quota_pause.py + scheduler 测试）

1. GLM 窗口 429 → `_should_retry_transient_api_failure` False（不烧重试）
2. GLM 窗口 429 → 暂停，error 前缀正确且含 `auto-resume at ... +0800`，workflow status=paused
3. reset ts 解析：UTC+8 → UTC 正确；无 ts → None
4. 调度器：到期（含 +60s margin）恢复、未到期不恢复、无标记不恢复
5. token-bearing 文本提及措辞不触发（防误伤）
6. 既有用例全绿（Bailian 临时分配仍 transient、hard platform quota 仍暂停）

## 不做

- 不加 schema 列、不加迁移（error_message 作协议载体，#2673 先例）
- 不改 `_TRANSIENT_API_ERROR_RE` / `_UPSTREAM_HARD_QUOTA_EXHAUSTED_RE` 既有语义
- 不做泛化的「任意 provider 窗口配额」框架（只认带 reset ts 的 usage-window 措辞）
