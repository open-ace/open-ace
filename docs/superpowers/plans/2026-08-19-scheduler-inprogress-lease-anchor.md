# Scheduler in-progress 租约锚点自愈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让调度器内存态 `_in_progress_*` 集合以 DB 租约为存活锚点自愈:**周期已返回的泄漏类**(异常绕过清理等)最多冻结到下一周期;挂死(hang)类不在本修复范围(见 PR 审查后的 scope 降格,修订记录第 6 条)。

**Architecture:** 三层:(1) 统一内存条目回收 helper(记录每个工作流占用的冲突键 `_in_progress_key_map`,一处实现三处复用);(2) `_process_workflows` 每周期用已取回的 active rows 做租约 staleness 回收;(3) `_advance_single` try 前置段的异常也走回收。

**Tech Stack:** Python 3.10+(仅标准库 threading/datetime),pytest,无新依赖。

**Spec:** 本文件自带根因与设计论证(brainstorming 产物,Bounded 路径,无独立 spec)。

## 根因与证据(为什么这么修)

**已证实的事实链**(全部来自生产日志/DB/代码,2026-08-19 取证):
1. fec4782b 于 11:20:42 UTC 完成 round4→round5 升级 commit(merge 阶段,DB updated_at);此后**零** development 阶段日志、零异常、零 "future error"、零心跳告警(全量窗口筛选,排除噪音后)。
2. 写 NULL 的有两处:`release_lock`(autonomous_repo.py L1411)与 `clear_in_progress` 的强制 UPDATE(autonomous_scheduler.py ~L331);另 `acquire_cleanup_lock`(repo L1356)是非 NULL 写者。**两处 NULL 写者都会同时清内存条目**,故"某次完整清理执行过 ⇒ 当前占位条目系清理后重新写入"的推断不变(独立审查纠偏:原文"release 是唯一 NULL 写者"不实)。
3. 冻结仍持续到 12:11 重启 ⟹ 当前占位的内存条目是**清理之后再次选中时重新写入**的。
4. 再次选中的 worker 消失于 `_advance_single` 的 **try 前置段**(`repo.get_workflow` L577 → `acquire_lock` L600):这是唯一**零日志、零超时、零 finally 保护**的区段——挂死或无声异常都必然无痕。

**推断(标注:未经直接观察证明)**:该 worker 在前置段的 DB 调用上无限期挂起(连接池耗尽/锁等待)——hang 按定义不产生日志,且进程已重启,线程栈不可事后获取。备选解释(前一个 worker 挂死在 advance() 内且心跳线程同步无声死亡)与"心跳应有续租/告警日志"矛盾,已排除为主因。

**结论**:无论挂死点在前置段哪一行,生命周期缺陷是同一个:**内存 in-progress 条目无存活锚点、前置段无清理保护**。修复以 DB 租约为存活真相(活 worker ⇒ 心跳每 60s 续租,与 agent 时长无关),三层修复(统一回收/租约锚点回收遍/前置段保护;scope 见修订记录第 6 条);另加诊断 WARNING,下次复发时留下现场证据。

## Global Constraints

- 保持 #1002 语义:**status=paused 的行不回收**(其 in-flight advance() 拥有恢复权,见 `_reclaim_paused_slots` 的注释)。
- 保持 waiting 语义:waiting 工作流不占冲突键(选中时不 reserve,finally 不 release)。
- 不新增 DB 查询:回收判据复用 `_process_workflows` 已取回的 `workflows` rows(含 `locked_at` 列)。
- `set.discard` 幂等,与 finally 的微秒级窗口并发安全(回收先跑也只是提前 discard;新 acquire 有 owner 校验,旧 finally 的 release 不会误放新租约)。
- 时间比较沿用仓库既有字符串比较惯用法(`datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")`,与 `acquire_lock` 的 cutoff 一致;`locked_at` 存的是 UTC 字符串)。
- 测试放 `tests/unit/`(CI `test(3.x)` 收集范围;参照 `test_scheduler_batch_order_tiebreak.py` 的既有调度器单测风格)。

---

### Task 1: 统一 in-progress 条目回收 helper + 冲突键映射

**Files:**
- Modify: `app/services/autonomous_scheduler.py`(`__init__` L~206-221、`clear_in_progress` L~276-317、`_advance_single` 的 acquire-fail 分支 L~601-613 与 finally L~700-711、`_process_workflows` 的 mark 段 L~1127-1152)
- Test: `tests/unit/test_scheduler_inprogress_reap.py`

**Interfaces:**
- Produces: `self._in_progress_key_map: dict[str, tuple[str | None, str, str]]`(workflow_id → (batch_id, workspace, branch),mark 时写入);`self._discard_in_progress_entry(workflow_id: str, *, release_conflict_keys: bool = True) -> None`(内部持锁,幂等)。`release_conflict_keys=False` 用于 paused 保号场景的将来需要;本任务三处调用点均传 True。

- [ ] **Step 1: 写失败测试(键映射 + 回收幂等)**

```python
"""Scheduler in-progress entry reclaim: key map + unified discard helper."""
from app.services.autonomous_scheduler import AutonomousScheduler


def _bare_scheduler() -> AutonomousScheduler:
    sched = AutonomousScheduler.__new__(AutonomousScheduler)
    sched._in_progress_ids = set()
    sched._in_progress_batch_ids = set()
    sched._in_progress_workspaces = set()
    sched._in_progress_branches = set()
    sched._in_progress_by_user = {}
    sched._in_progress_key_map = {}
    sched._in_progress_lock = __import__("threading").Lock()
    return sched


def test_mark_then_discard_releases_reserved_keys_exactly():
    sched = _bare_scheduler()
    wid = "w-1"
    with sched._in_progress_lock:
        sched._in_progress_ids.add(wid)
        sched._in_progress_by_user.setdefault(3, set()).add(wid)
        sched._in_progress_batch_ids.add("batch-9")
        sched._in_progress_workspaces.add("/ws/open6")
        sched._in_progress_branches.add("auto-dev/w-1")
        sched._in_progress_key_map[wid] = ("batch-9", "/ws/open6", "auto-dev/w-1")
    sched._discard_in_progress_entry(wid)
    assert wid not in sched._in_progress_ids
    assert wid not in sched._in_progress_by_user.get(3, set())
    assert "batch-9" not in sched._in_progress_batch_ids
    assert "/ws/open6" not in sched._in_progress_workspaces
    assert "auto-dev/w-1" not in sched._in_progress_branches
    assert wid not in sched._in_progress_key_map
    # 幂等:再删一次不抛
    sched._discard_in_progress_entry(wid)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /tmp/cls2-sched-leak && python3 -m pytest tests/unit/test_scheduler_inprogress_reap.py -q`
Expected: FAIL(`_discard_in_progress_entry` 不存在)

- [ ] **Step 3: 最小实现**

`__init__` 的集合声明区(L~215-220 附近)加一行:
```python
        # workflow_id -> (batch_id, workspace, branch) reserved at selection
        # time, so a later reclaim can release EXACTLY those keys even after
        # the row has changed shape or left the active set (leak self-heal).
        self._in_progress_key_map: dict[str, tuple[str | None, str, str]] = {}
```

新增方法(放 `clear_in_progress` 旁):
```python
    def _discard_in_progress_entry(self, workflow_id: str) -> None:
        """Remove one workflow's in-progress bookkeeping, keys included.

        Single authority for entry teardown, used by _advance_single's
        acquire-fail branch and finally, by clear_in_progress, and by the
        lease-anchored reclaim pass. Idempotent; caller must NOT hold
        _in_progress_lock (this method takes it).
        """
        with self._in_progress_lock:
            self._in_progress_ids.discard(workflow_id)
            for bucket in self._in_progress_by_user.values():
                bucket.discard(workflow_id)
            batch_id, workspace, branch = self._in_progress_key_map.pop(
                workflow_id, (None, "", "")
            )
            if batch_id:
                self._in_progress_batch_ids.discard(batch_id)
            if workspace:
                self._in_progress_workspaces.discard(workspace)
            if branch:
                self._in_progress_branches.discard(branch)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/unit/test_scheduler_inprogress_reap.py -q`
Expected: PASS

- [ ] **Step 5: 三处旧 discard 块替换为 helper,mark 段写入 key map**

(a) `_process_workflows` mark 段(L~1127-1152)在 `self._in_progress_ids.add(wf_id)` 循环里加:
```python
                self._in_progress_key_map[wf_id] = (
                    batch_id if (batch_id := wf.get("batch_id")) and not is_waiting else None,
                    workspace if (not is_waiting and (workspace := None) is None) else "",
                    "",
                )
```
⚠️ 上面是伪码占位说明真实语义——**实际实现**改为在循环开头先解包(避免海象歧义):
```python
                is_waiting = wf.get("status") == "waiting"
                batch_id = wf.get("batch_id")
                workspace, branch = self._conflict_keys(wf)
                self._in_progress_key_map[wf_id] = (
                    batch_id if (batch_id and not is_waiting) else None,
                    workspace if (workspace and not is_waiting) else "",
                    branch if (branch and not is_waiting) else "",
                )
```
(与下方现有 reserve 逻辑的 `if batch_id and not is_waiting` 完全同判。)

(b) `_advance_single` acquire-fail 分支(L~601-613)与 finally 尾部(L~700-711)的 `with self._in_progress_lock:` 整块替换为 `self._discard_in_progress_entry(workflow_id)`(finally 中该块位于 release_lock 之后,保持顺序)。
(c) `clear_in_progress`(L~276-317)内部的 discard 逻辑同样替换为 `self._discard_in_progress_entry(workflow_id)`(保留其 DB 锁释放部分不变)。

- [ ] **Step 6: 全量回归**

Run: `python3 -m pytest tests/unit/ tests/autonomous/ -q`
Expected: 全 PASS(既有 1897 retry-clears-in-progress 若在 tests/issues 不在收集范围则单独跑:`python3 -m pytest tests/issues/1897 -q`)

- [ ] **Step 7: Commit**

```bash
git add app/services/autonomous_scheduler.py tests/unit/test_scheduler_inprogress_reap.py
git commit -m "refactor(scheduler): single authority for in-progress entry teardown + key map"
```

---

### Task 2: 租约锚点回收遍(核心自愈)

**Files:**
- Modify: `app/services/autonomous_scheduler.py`(新增 `_reclaim_stale_in_progress`,挂进 `_process_workflows` L~1023 `_reclaim_paused_slots(repo)` 之后)
- Test: `tests/unit/test_scheduler_inprogress_reap.py`(追加)

**Interfaces:**
- Consumes: Task 1 的 `_discard_in_progress_entry`、`_in_progress_key_map`。
- Produces: `self._reclaim_stale_in_progress(workflows: list[dict]) -> None`;判据函数 `self._lease_is_stale(wf: dict | None) -> bool`(wf=None 或 `locked_at` 为空或早于 cutoff)。

- [ ] **Step 1: 写失败测试**

```python
from datetime import datetime, timedelta, timezone


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _sched_with_entry(sched, wid, row):
    with sched._in_progress_lock:
        sched._in_progress_ids.add(wid)
        sched._in_progress_key_map[wid] = (row.get("batch_id"), row.get("_workspace", ""), "")


def test_stale_lease_entry_is_reaped_and_visible():
    sched = _bare_scheduler()
    stale = _fmt(datetime.now(timezone.utc) - timedelta(seconds=1801))
    row = {"workflow_id": "w-stale", "status": "developing", "locked_at": stale,
           "batch_id": "b1", "_workspace": "/ws/a"}
    _sched_with_entry(sched, "w-stale", row)
    sched._reclaim_stale_in_progress([row])
    assert "w-stale" not in sched._in_progress_ids
    assert "b1" not in sched._in_progress_batch_ids


def test_fresh_lease_entry_is_kept():
    sched = _bare_scheduler()
    fresh = _fmt(datetime.now(timezone.utc) - timedelta(seconds=60))
    row = {"workflow_id": "w-live", "status": "developing", "locked_at": fresh,
           "batch_id": "b2", "_workspace": "/ws/a"}
    _sched_with_entry(sched, "w-live", row)
    sched._reclaim_stale_in_progress([row])
    assert "w-live" in sched._in_progress_ids
    assert "b2" in sched._in_progress_batch_ids


def test_paused_row_is_never_reaped():
    sched = _bare_scheduler()
    stale = _fmt(datetime.now(timezone.utc) - timedelta(seconds=9999))
    row = {"workflow_id": "w-paused", "status": "paused", "locked_at": stale,
           "batch_id": "b3", "_workspace": ""}
    _sched_with_entry(sched, "w-paused", row)
    sched._reclaim_stale_in_progress([row])
    assert "w-paused" in sched._in_progress_ids  # #1002: in-flight advance owns resume


def test_row_missing_from_active_list_is_reaped():
    sched = _bare_scheduler()
    _sched_with_entry(sched, "w-gone", {"batch_id": "b4", "_workspace": "/ws/b"})
    sched._reclaim_stale_in_progress([])  # completed/failed while entry leaked
    assert "w-gone" not in sched._in_progress_ids
    assert "/ws/b" not in sched._in_progress_workspaces
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/unit/test_scheduler_inprogress_reap.py -q`
Expected: 新增 4 条 FAIL(`_reclaim_stale_in_progress` 不存在)

- [ ] **Step 3: 最小实现**

```python
    # Reap threshold mirrors the repository's LOCK_TIMEOUT_SECONDS (1800): an
    # in-progress entry whose DB lease is gone/stale cannot belong to a live
    # worker (the heartbeat renews live leases every 60s regardless of agent
    # duration), so the memory entry is fiction and is starved-safe to drop.
    _IN_PROGRESS_STALE_SECONDS = 1800

    def _lease_is_stale(self, wf: dict | None) -> bool:
        if not wf:
            return True
        locked_at = (wf.get("locked_at") or "").strip()
        if not locked_at:
            return True
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self._IN_PROGRESS_STALE_SECONDS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        return locked_at < cutoff

    def _reclaim_stale_in_progress(self, workflows: list[dict]) -> None:
        """Self-heal _in_progress entries against the DB lease (source of truth).

        A worker hung inside advance() never reaches its finally, so its
        in-memory entry is immortal and freezes the workflow (and its batch /
        workspace / branch conflict keys) until a service restart — observed
        2026-08-19 (workflow fec4782b, ~50 min frozen, restart healed in 1s).
        The distributed lease already encodes liveness cross-process: heartbeats
        renew it every 60s independent of agent duration, and acquire_lock
        breaks leases stale past 1800s anyway. Dropping memory entries whose
        lease is missing/stale therefore introduces no new concurrency risk —
        it only stops this process from freezing itself.

        Exclusions: paused rows keep their entry (#1002 — an in-flight
        advance() owns the resumption); fresh leases keep theirs (live worker).
        """
        with self._in_progress_lock:
            if not self._in_progress_ids:
                return
            ids_snapshot = list(self._in_progress_ids)
        rows_by_id = {wf.get("workflow_id"): wf for wf in workflows}
        for wid in ids_snapshot:
            row = rows_by_id.get(wid)
            if row is not None and row.get("status") == "paused":
                continue
            if row is not None and not self._lease_is_stale(row):
                continue
            logger.warning(
                "Reaping stale in-progress entry for workflow %s "
                "(row=%s, lease=%s); worker is gone or hung without a lease",
                wid[:8],
                "active" if row is not None else "not-in-active-set",
                (row or {}).get("locked_at") or "NULL",
            )
            self._discard_in_progress_entry(wid)
```

挂载点(`_process_workflows`,L~1023 `_reclaim_paused_slots(repo)` 调用之后):
```python
        # Self-heal _in_progress entries with no live DB lease behind them
        # (leaked worker; 2026-08-19 fec4782b). Scope: leaks whose cycle has
        # returned — see the reclaim docstring.
        self._reclaim_stale_in_progress(workflows)
```
注意放在 `workflows = repo.get_active_workflows()` 取数**之后**。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/unit/test_scheduler_inprogress_reap.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/autonomous_scheduler.py tests/unit/test_scheduler_inprogress_reap.py
git commit -m "fix(scheduler): lease-anchored self-heal for in-progress entries"
```

---

### Task 3: `_advance_single` pre-try 异常洞修补

**Files:**
- Modify: `app/services/autonomous_scheduler.py`(`_advance_single` L~569-613:try 之前的 `repo.get_workflow` / `_conflict_keys` / `acquire_lock` 段)
- Test: `tests/unit/test_scheduler_inprogress_reap.py`(追加)

**Interfaces:**
- Consumes: Task 1 的 `_discard_in_progress_entry`。

- [ ] **Step 1: 写失败测试**

```python
def test_pre_try_exception_still_clears_entry():
    """get_workflow raising before the try must not leak the in-progress
    entry (observed leak class: exception propagates to the executor future,
    whose catch only logs)."""
    sched = _bare_scheduler()
    sched._in_progress_ids.add("w-pre")
    sched._in_progress_key_map["w-pre"] = (None, "", "")

    class BoomRepo:
        def get_workflow(self, wid):
            raise RuntimeError("db hiccup")

    import unittest.mock as mock
    with mock.patch.object(sched, "_discard_in_progress_entry") as discard, \
         mock.patch("app.routes.autonomous._get_repo", create=True, return_value=BoomRepo()):
        sched._advance_single("w-pre")
    discard.assert_called_once_with("w-pre")
    assert "w-pre" not in sched._in_progress_ids
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/unit/test_scheduler_inprogress_reap.py::test_pre_try_exception_still_clears_entry -q`
Expected: FAIL(条目仍残留)

- [ ] **Step 3: 最小实现**

把 `_advance_single` 开头到 `acquire_lock` 失败分支的整段包进 try/except:

```python
        try:
            workflow = repo.get_workflow(workflow_id)
        except Exception:
            # Pre-try section: an exception here would bypass the main
            # try/finally entirely and leak the in-progress entry (the
            # executor's future.result() catch only logs). Reap and re-raise
            # for the executor to log.
            self._discard_in_progress_entry(workflow_id)
            raise
```
(`acquire_lock` 的"返回 False"分支已自带清理,替换为 `self._discard_in_progress_entry(workflow_id)` 即可;`_conflict_keys`/`was_waiting` 计算不会抛——workflow 为 None 时有兜底。)

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `python3 -m pytest tests/unit/test_scheduler_inprogress_reap.py tests/unit/ tests/autonomous/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/autonomous_scheduler.py tests/unit/test_scheduler_inprogress_reap.py
git commit -m "fix(scheduler): clear in-progress entry when pre-try repo access raises"
```

---

## 验证 Gap 对应

- 改动 lane:`tests/unit/` 与 `app/services/`(CI `test(3.x)` 收集)。
- 对照 clean origin/main worktree 跑同套测试排除环境失败。
- PR 文本**不得**出现 `closes|fixes|resolves ... #N`(auto-close 陷阱)。

## 部署

- 单文件热补丁:`app/services/autonomous_scheduler.py` cp + chown openace;`systemctl restart openace-scheduler.service`(编排进程内存态本来就要重启才加载)。无迁移。
- 上线后验证:journalctl 观察一个周期无异常;若再现泄漏,应看到 `Reaping stale in-progress entry` WARNING 并自动恢复(替代冻结)。

## 独立审查修订记录(2026-08-19,4 项 blocking 已全部按"以实现为准"消化)

1. **B1(paused 不在 active 名单)**:`get_active_workflows` 不含 paused,原计划"row 缺失⇒回收"会误杀 #1002 保号条目。实现改为 `_reclaim_stale_in_progress(workflows, repo)`:缺失行逐个 `repo.get_workflow` 查状态——paused 保留、查询异常保留(fail-closed)、终态回收;健康路径零额外查询(仅泄漏时才查)。
2. **B2(Postgres datetime 崩溃)**:`locked_at` 在 Postgres/RealDictCursor 下返回 datetime,`.strip()` 会炸掉整个调度周期。实现新增 `_lease_timestamp()` 双类型归一化(str/datetime → aware UTC datetime),比较改为数值年龄比较;补 `test_postgres_datetime_lease_shapes_do_not_crash`。
3. **B3(计划自带测试写坏)**:fresh-lease 测试断言了从未写入的集合成员;pre-try 测试缺 `pytest.raises` 且 mock 了 helper 却断言真实状态。实现侧测试全部重写自洽(真实 helper + 真实集合断言)。
4. **B4(挂载点矛盾)**:统一为"`workflows = repo.get_active_workflows()` 的 try/except 之后、filter 之前"(Task 2 正文旧表述作废)。
5. 事实纠偏见根因段第 2 条(NULL 写者);Task 1 Step 5(a) 的伪码占位段已废弃,以实现的 mark 段(先解包再记 map)为准。
6. **PR #2846 审查后的 scope 降格(2026-08-19)**:PR 独立审查实证——worker 若为**挂死**,`_process_workflows` 的 `ThreadPoolExecutor` 上下文(`shutdown(wait=True)`)会把整个周期停住,本回收遍(位于周期顶部)永远没机会运行,挂死类仍需重启。故修复范围诚实化为:**仅治愈"周期已返回"的泄漏**(异常类——事故 fec4782b 的观测形态:泄漏期间其他工作流持续推进,说明周期未被停住);挂死场景下"冻结但无 Reaping WARNING"本身即周期被停住的诊断证据,watchdog 留作有证据后的跟进。原文"两层覆盖两种变体/30 分钟内自愈/不再需要重启"等表述按此修正。审查同时落地:缺失行新鲜租约保留(收窄 #1002 resume 竞态)、乱串租约按 unknown 保留(fail-closed 对称)、`_get_repo()` 入 try、2295 选路 fixture patch 回收遍。
