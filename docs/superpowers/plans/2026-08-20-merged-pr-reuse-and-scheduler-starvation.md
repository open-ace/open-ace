# 修复方案：已合并 PR 复用导致 merge 空提交 + 调度器同步阻塞饥饿

- 日期：2026-08-20
- 分支：`fix/merged-pr-reuse-and-scheduler-starvation`（基于 `origin/main` @ 57a88aef）
- 生产环境：192.168.31.159（openace-scheduler.service，PostgreSQL `openace` 库）
- 涉及工作流：#331（merge 失败，bug 1 受害者）；批次 280f009c #340-349 / 批次 3fc727cb #350-355（bug 2 受害者，调度延迟 46 分钟）

## 一、Bug 1：pr_review 阶段复用已合并（MERGED）的 PR

### 1.1 生产证据（#331 事件时间线，autonomous_workflows + workflow_events）

| 时间 (UTC) | 事件 |
|---|---|
| 14:59 | PR #2851 创建（pr_review round 1） |
| 14:59–16:02 | review rounds 1–6（pr_reviewed / pr_updated 交替） |
| 16:38–16:54 | merge 阶段：冲突解决 → push → CI 失败 → CI repair attempt 1 |
| 17:14 | **PR #2851 merged**（milestone `PR #2851 merged`） |
| 17:19 | acceptance_verification 判定 **rejected**（verification_merge_sha=b7219a7a） |
| 18:22 | 重新进入 pr_review（`current_round` 重置为 0，`github_pr_number=2851` 保留） |
| 19:50–20:27 | 第二轮 review rounds 1–6：**无任何 `pr_created` milestone** —— 复用已合并的 #2851 |
| 20:29 | 进入 merge（auto_merge=true） |
| 20:48 | merge 失败：`Merge resolution made no commit; refusing unchanged push`（workflow status=failed） |

### 1.2 根因（代码级）

[pr_review.py](../../../app/modules/workspace/autonomous/phases/pr_review.py#L313-L316)：

```python
existing_pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
if round_num == 1 and not existing_pr_number:
    # 仅在此分支创建 PR
```

PR 创建仅在 `round_num == 1 and not existing_pr_number` 时发生。验收 rejected 后重新进入 pr_review 时 round 重置为 1，但 `github_pr_number` 仍指向上一轮**已合并**的 PR（#2851）。后续所有轮次、以及 merge 阶段，全部围绕这个已合并 PR 的 headRefOid 操作：merge 阶段 `resolve_merge_conflicts` 计算出的"解决结果"与原 PR head 相同（main 已包含这些提交），触发 [git_workspace.py](../../../app/modules/workspace/autonomous/git_workspace.py) 的防呆检查 `Merge resolution made no commit; refusing unchanged push`，工作流终态 failed。

### 1.3 修复设计

在计算 `existing_pr_number` 之后、进入创建/复用分支之前，增加 PR 状态活性检查：

以下为初始草案（**已被 1.4 节"修正后的插入位置"取代，实现以 1.4 为准**——本草案的活性检查缺少 round 1 守卫，会对 round>1 错误清空 `pr_number`；保留仅为说明设计意图）：

```python
existing_pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
# 新增：已记录的 PR 可能已 MERGED/CLOSED（验收 rejected 后 resume-with-
# feedback / cancel-with-feedback 重入等路径）。复用已合并 PR 的 headRefOid
# 会让 merge 阶段计算出空提交并失败（#331，"Merge resolution made no
# commit"）。非 OPEN 状态的已记录 PR 视为不存在，强制走新 PR 创建路径。
if existing_pr_number:
    try:
        recorded_pr = gh.get_pr(existing_pr_number)
        pr_state = (recorded_pr.get("state") or "").upper()
    except Exception:
        pr_state = ""  # 状态查询失败 → 同样强制新建（见下方论证）
    if pr_state != "OPEN":
        logger.warning(
            "Recorded PR #%s state=%r is not OPEN; creating a fresh PR for this cycle",
            existing_pr_number, pr_state or "unknown",
        )
        existing_pr_number = None
pr_number = existing_pr_number
```

要点论证：

1. **状态查询失败时保留已记录编号（review 修正）**：初始草案将探测异常与"确认非 OPEN"同等对待（fail-closed to create）。独立审查（PR #2906 review round 1, opinion 3）推翻了该论证：探测失败通常意味着 gh/API 瞬时故障，此时强制 `create_pr` 会经同一故障通道必然失败——"already exists" 恢复逻辑同样依赖 gh（`find_existing_pr`），也可能同时失败，导致工作流在"保留编号即可正常推进"的场景下终态 failed。因此**仅当 `get_pr` 成功返回且状态非 OPEN 时才清空编号**；探测异常时每轮保留编号并记 warning（"already exists" 恢复兜底极罕见的"新 PR 已存在"场景）。
2. **MERGED 后同分支新建 PR 语义正确**：branch_name 由 workflow UUID 派生（如 `auto-dev/21a26fe8-...`），重新进入的修复提交已 push 到同一分支；main 已包含旧提交，新 PR 的 diff 只含本轮修复增量（GitHub 以 merge-base 计算），正是期望行为。若合并时分支被远端删除，`_ensure_branch_and_push`（L287 调用、L102 定义）的 force push 会重建分支。
3. **不选择在 acceptance_verification rejected 转换点清空 `github_pr_number`**：入口防御（point-of-use）覆盖所有保留旧 PR 编号重入 pr_review 的路径——验收 rejected 后 resume-with-feedback / cancel-with-feedback（orchestrator 重置 `current_round=0` 后重新走 pr_review round 1，是 #331 的实际路径），以及未来新增的任何重入路径；单一埋点、无状态泄漏。转换点清理需要枚举所有路径，易漏。（merge 阶段的 transient retry 停留在 merge phase，不重入 pr_review，不在枚举内。）
4. **`pr_number` 局部变量**：保持现有 L314-L315 原值读取，活性检查仅影响 `existing_pr_number`（详见 1.4 的精确插入位置）——round>1 时 `pr_number` 永不为 None。
5. **失败里程碑语义**：新建路径的异常处理完全复用现有 except 分支（"already exists" 恢复、非瞬时错误 raise），不新增失败路径。
6. **`workflow_patch["github_pr_number"]` 持久化**：新 PR 编号会覆盖旧值（L340/L384 现有逻辑），merge 阶段读取的是新 PR —— 链路闭环。

### 1.4 影响范围

- 仅 `app/modules/workspace/autonomous/phases/pr_review.py` 单文件、单函数。
- 正常路径（OPEN PR 复用）行为不变：多一次 `gh pr view` 调用（**任何 round 且有已记录 PR 时**——活性检查对所有 round 执行，round>1 的查询结果仅用于决定是否记 warning，见 1.4 代码块；每轮 review 一次秒级调用，round>1+非 OPEN 场景 warning 每轮重复一条属可接受噪音）。

补充说明 round>1 路径：round>1 时现有代码本就不创建 PR（`round_num == 1` 条件），复用 `pr_number`。若 round>1 时已记录 PR 已 MERGED——主要可达路径是 review 轮进行中（round≥2）人工直接在 GitHub 合并了 PR——`pr_number` 置 None 会导致下游拿不到编号。因此**活性检查只在 `round_num == 1` 时执行清空，round>1 保持原值并记 warning 日志**（该场景属人工干预后的深层异常态，交由现有 merge/CI 失败路径暴露，不在本次扩大战线；warning 即排查线索）。

修正后的插入位置（精确，**实现以此为准**）：

```python
existing_pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
if existing_pr_number:
    probe_error = None
    pr_state = ""
    try:
        recorded_pr = gh.get_pr(existing_pr_number)
        pr_state = (recorded_pr.get("state") or "").upper()
    except Exception as e:
        probe_error = e
    if probe_error is not None:
        # 探测失败 ≠ 确认非 OPEN（review 修正）：保留编号，
        # 不经同一故障通道强行 create_pr
        logger.warning(
            "Recorded PR #%s state probe failed (%s); keeping PR id",
            existing_pr_number, probe_error,
        )
    elif pr_state != "OPEN":
        if round_num == 1:
            # 仅 round 1 创建窗口清空 → 走下方 create_pr；
            # "already exists" 恢复逻辑兜底防重复
            logger.warning(
                "Recorded PR #%s state=%r is not OPEN; creating a fresh PR",
                existing_pr_number, pr_state or "unknown",
            )
            existing_pr_number = None
        else:
            # round>1 不动编号（下游依赖非 None）；warning 即排查线索
            logger.warning(
                "Recorded PR #%s state=%r is not OPEN at round %d; keeping PR id",
                existing_pr_number, pr_state or "unknown", round_num,
            )
pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
if round_num == 1 and not existing_pr_number:
    ...创建...（现有 L316 起的逻辑不变）
```

### 1.5 测试计划

新增单测（挂在现有 pr_review 测试文件或 `tests/issues/` 新建目录）：

1. `test_round1_reuses_open_pr`：`github_pr_number=100`，`gh.get_pr` 返回 `state=OPEN` → 断言 `create_pr` 未被调用、`pr_number==100`（回归保护）。
2. `test_round1_creates_new_pr_when_recorded_pr_merged`：`github_pr_number=100`，`get_pr` 返回 `state=MERGED` → 断言 `create_pr` 被调用一次、`workflow_patch["github_pr_number"]` 为新编号。
3. `test_probe_failure_keeps_recorded_pr_id`（review 修正后语义）：`get_pr` 抛 `GitHubOpsError` → 断言 `create_pr` 未被调用、编号保留（探测失败 ≠ 确认非 OPEN，见 1.3 要点论证 #1）。
4. `test_round_gt1_keeps_recorded_pr_even_if_merged`：round=6、`state=MERGED` → 断言 `create_pr` 未被调用、`pr_number` 保持 100、有 warning 日志。

## 二、Bug 2：`_process_workflows` 同步等待导致调度循环饥饿

### 2.1 生产证据

journalctl（openace-scheduler.service，2026-08-20 CST）中 `Advancing workflow` 事件间隔：

```
17:48:51 preparation     ← 批次推进
17:49:06 planning
17:56:24 planning
18:06:49 development
18:19:39 pr_review       ← #329 进入 pr_review
（49 分钟空窗：#329 单次 advance 内含 agent 运行 + CI 轮询）
19:08:01 pr_review       ← #329 下一次 advance
19:08:01 preparation     ← #340（批次 280f009c，18:22 创建，等待 46 分钟）
19:08:01 preparation     ← #350（批次 3fc727cb）
```

批次 280f009c 创建于 18:22，#340 直到 19:08 才首次被推进（延迟 46 分钟）；期间 `_promote_queued_workflows`、`_auto_resume_quota_paused`、`_reclaim_paused_slots`、`_retry_pending_git_cleanups`、sandbox reap 全部停摆。

### 2.2 根因（代码级）

[autonomous_scheduler.py L615-L638](../../../app/services/autonomous_scheduler.py#L615-L638)：`_run_loop` 每 10 秒调用 `_process_workflows()` 并**同步等待其返回**。

[autonomous_scheduler.py L1277-L1294](../../../app/services/autonomous_scheduler.py#L1277-L1294)：

```python
with ThreadPoolExecutor(
    max_workers=min(get_max_concurrent_workflows(), len(to_process)),
    thread_name_prefix="auto-wf",
) as executor:
    futures = {executor.submit(self._advance_single, wf_id): wf for wf in to_process}
    for future in as_completed(futures):
        ...
```

`with` 块退出时 `shutdown(wait=True)`，且 `as_completed` 循环等待**所有** future 完成。`_advance_single` 的一次执行包含 agent 运行（10-40 分钟）与 CI 轮询（30s×N），因此 `_process_workflows` 单次调用可阻塞主循环近一小时。代码注释（L320-327）已自认此问题："A worker HUNG mid-cycle parks the whole cycle inside the executor's shutdown(wait=True) and this pass cannot run; a restart remains the remedy there."

### 2.3 修复设计

保持 `_process_workflows` 的默认同步语义（13 处现有测试直接调用并断言同步完成：tests/issues/716 ×9、tests/issues/2295 ×3、tests/unit/test_scheduler_batch_order_tiebreak.py ×1；生产代码唯一调用方是 `_run_loop`），仅让 `_run_loop` 走非阻塞路径：

1. **签名扩展**：`def _process_workflows(self, *, wait: bool = True)`。
   - `wait=True`（默认）：现有 `with ThreadPoolExecutor + as_completed` 行为逐字保留 —— 所有现有测试不变。
   - `wait=False`：向**持久化 executor** 提交后立即返回，不等待。
2. **持久化 executor**：
   - `__init__` 增加 `self._bg_executor: ThreadPoolExecutor | None = None`、`self._in_flight_futures: dict[Future, str] = {}`（future → workflow_id）、`self._futures_lock = threading.Lock()`。
   - 懒初始化：`_get_bg_executor()` 首次调用时创建 `ThreadPoolExecutor(max_workers=get_max_concurrent_workflows(), thread_name_prefix="auto-wf-bg")`。
   - **cap 运行时变化处理**：`get_max_concurrent_workflows()` 每次从配置解析、可变。`_get_bg_executor()` 检测当前 cap 与创建时不一致则重建 executor（旧的 `shutdown(wait=False)`，在途 worker 自然跑完当前任务后退出）。重建安全性：系统级并发上界由 `_in_progress_ids` 记账约束（旧 executor 在途 R 项均在集合内，新提交数 ≤ 新 cap − R），不依赖单个 executor 的 max_workers，因此无超发。
   - 提交量上界论证：选择逻辑在 `_in_progress_lock` 内计算 `slots_available = cap - len(self._in_progress_ids)`（L1201-L1240），随后在第二个锁块内将选中项写入 `_in_progress_ids`（L1246-L1275）。两块之间存在无锁窗口，但 `_process_workflows` 的 `wait=False` 路径只有 `_run_loop` 单线程调用（含现状与改造后），窗口内无其它选择者，"读到即登记"时序成立；**前提声明**：未来若出现第二个调用线程，须将两个锁块合并为单一锁区间，否则本上界论证失效。
3. **future 回收与错误日志**：新增 `_reap_completed_futures()`，在 `_run_loop` 每 tick 调用：在 `_futures_lock` 内取 `_in_flight_futures` 快照并 `pop` 已完成项，锁外对已完成 future 调 `f.result()` 捕获异常记 error 日志（等价于原 `as_completed` 循环的日志职责，不丢错误可见性；`f.done()` 为真后 `result()` 不阻塞）。
4. **`_run_loop` 改造**：

```python
while not self._stop_event.is_set():
    try:
        self._process_workflows(wait=False)   # 立即返回，主循环恢复 10s 心跳
        self._reap_completed_futures()         # 回收已完成 future + 错误日志
        ...sandbox reap 不变...
    except Exception as e:
        logger.error("Scheduler error: %s", e, exc_info=True)
    self._stop_event.wait(10)
```

5. **`stop()` 收尾**：在现有逻辑后增加 —— 若 `self._bg_executor` 存在，先 `_reap_completed_futures()` 记录已完成项的错误，再 `shutdown(wait=False)`（不等待在途 advance：与现有 stop 语义一致 —— 现有 stop 也只 join 主线程 20s 超时后放弃，在途 advance 由 `prepare_for_shutdown` 中断）。
6. **线程安全论证**：`_in_flight_futures` 的所有读写（提交、回收）都持有 `self._futures_lock`。正常路径下仅主循环线程触碰（`wait=False` 提交与 reap 都在 `_run_loop`），但 `stop()` 可能从其它线程调用 `_reap_completed_futures()`——即使 `_run_loop` 的 join 超时后主循环仍在跑（现有代码 L290-291 允许该路径，仅 warning），锁保证 stop 线程与主循环线程对 dict 的并发访问安全；reap 的迭代基于锁内快照，杜绝 `dictionary changed size during iteration`。`wait=True` 路径不触碰 `_in_flight_futures`（独立局部 executor），两条路径互不干扰。
7. **hang worker 行为变化论证（改善而非风险）**：worker 挂死场景下，现状是整个调度 pass 停摆（L320-327 注释自认，需重启）；修复后主循环恢复 10s 心跳。若挂死 worker 仍能续约 lease（heartbeat 60s，即"挂"在可被信号唤醒的等待上），`_reclaim_stale_in_progress` 判定 keep → slot 仍被占用、不会重复 advance 同一工作流，仅维护操作（promotion/auto-resume/cleanup/reap）恢复正常运转；若挂死到 heartbeat 也停止，lease 过期后（`_IN_PROGRESS_STALE_SECONDS`）该条目被 reap、工作流将被重新 advance——由 DB `acquire_lock` 的 break-stale-lease 兜底防并发写，与现状"重启后恢复"路径等价。两种子情形均不劣于现状；挂死 worker 的彻底恢复仍依赖重启或 lease 过期，但不再连带饿死其它批次。
8. **不选用的替代方案及理由**：
   - *每 tick 起新线程跑完整 `_process_workflows`*：会并发执行 `_promote_queued_workflows` 等维护操作，语义改变超出本次修复范围。
   - *gevent GreenletPool*：`_advance_single` 链路含原生阻塞子进程调用（agent、gh CLI），monkey-patch 兼容性风险大。
   - *as_completed 加 timeout*：治标不治本，超时后仍需处理未完成 future 的生命周期，复杂度更高。

### 2.4 影响范围

- 仅 `app/services/autonomous_scheduler.py`：`__init__`、`_run_loop`、`_process_workflows`（签名+尾部提交块）、`stop()`、新增 `_get_bg_executor` / `_reap_completed_futures`。
- 默认行为（`wait=True`）对所有现有调用方（13 处测试）完全兼容。

### 2.5 测试计划

1. 现有测试回归：`tests/issues/716/test_scheduler.py`、`tests/issues/1002/`、`tests/issues/2295/`、`tests/unit/test_scheduler_batch_order_tiebreak.py` 全部不改即通过（默认 `wait=True`）。
2. 新增 `test_run_loop_does_not_block_on_slow_advance`（tests/issues/716/ 或新目录）：
   - mock `_advance_single` 为 sleep(2)，并沿用 716 现有 fixture：patch `app.routes.autonomous._get_repo` 返回 MagicMock repo、`get_active_workflows` 返回 ≥1 条 active workflow（仅 mock `_advance_single` 不足以走到提交步骤；quota stub 因 `_advance_single` 被整体替换而不需要）；
   - **退出手法（注意：不可预设 `_stop_event` —— `start()` 会 `clear()`，且 `while not is_set()` 下循环体零次执行）**：测试线程内直接同步调用 `scheduler._run_loop()`，配合 `threading.Timer(0.5, scheduler._stop_event.set)`（在调用前 start 该 Timer）。循环体执行一轮（`_process_workflows(wait=False)` 立即返回），随后 `self._stop_event.wait(10)` 被 Timer 置位唤醒，循环退出；
   - 断言 `_run_loop()` 在 <1s 内返回（慢 advance 的 2s sleep 不阻塞主循环）；
   - `time.sleep(2.5)` 等待 advance 完成后，**显式调用** `scheduler._reap_completed_futures()`（循环退出后无人自动 reap——循环内那次 reap 发生在 future 完成前），断言 `_in_flight_futures` 为空且 `_advance_single` 确实被调用过（future 已执行）；
   - 测试收尾：`scheduler._stop_event.set()` + 若创建了 bg executor 则 `shutdown(wait=False)`，避免线程泄漏影响其它测试。
3. 新增 `test_reap_logs_future_exception`：提交一个抛异常的 `_advance_single`，等待完成后调用 `_reap_completed_futures`，断言 error 日志包含 workflow id 且 `_in_flight_futures` 清空。
4. 新增 `test_wait_false_submits_without_blocking`：`wait=False` 调用即时返回，`_in_flight_futures` 含 1 项，`_in_progress_ids` 已登记。

## 三、实施步骤

1. Bug 1 修复 + 单测（1.5）→ 全量跑 pr_review 相关测试。
2. Bug 2 修复 + 单测（2.5）→ 全量跑 scheduler 相关测试。
3. `python -m pytest tests/issues/716 tests/issues/1002 tests/issues/2295 tests/unit/test_scheduler_batch_order_tiebreak.py` + pr_review 相关测试目录全绿。（附注：若本机存在 `~/.open-ace/agent-launcher.conf`，`test_max_concurrency`、`test_pending_workflows_are_prioritized_ahead_of_waiting` 可能因 cap 被环境覆盖为 3 而假红——属环境性失败，以 HOME 隔离或 CI 结果为准。）
4. 提交 PR（描述含两个 bug 的生产证据时间线）→ 独立 agent 审查 PR，意见发表到 PR，迭代至零意见 → 合并 → 部署 192.168.31.159 → 重启 openace-scheduler.service。
5. 复盘验证：重置 #331 使其走完 pr_review（新 PR 创建）→ merge；观察新批次在长 advance 期间是否保持 10s 级调度心跳。

## 四、风险与回滚

| 风险 | 缓解 |
|---|---|
| PR 状态查询增加 gh 调用（任何 round 且有已记录 PR 时，每轮 review 一次） | 单次 `gh pr view` 秒级；失败路径已论证安全 |
| 持久化 executor 泄漏（stop 未 shutdown） | stop() 显式 shutdown；进程退出亦回收（daemon 线程模型不变） |
| `_in_flight_futures` 跨线程访问 | 正常路径仅主循环线程触碰；stop() 线程的回收路径由 `_futures_lock` 保护（快照迭代） |
| 新 PR 语义偏差（同分支多 PR） | GitHub merge-base diff 语义保证只含增量；"already exists" 恢复兜底防重复 |

回滚：两个修复相互独立，均可单独 revert；不影响数据库 schema，无迁移。
