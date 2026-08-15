# #2491 P0 探针：pytest-rerunfailures × JUnit 双源行为实测

日期：2026-08-15 · 环境：pytest 9.1.1 / pytest-rerunfailures 16.4 / pytest-timeout 2.4.0（requirements-ci.lock）
目的：在冻结 envelope schema 之前实测 `--reruns` 下 JUnit 与 attempt 记录的对应关系（方案 §2.6 / A2，复审 V1）。

## 方法

合成 3 个用例（必过 / 必败 / rerun 后通过，用计数文件制造 flaky），以
`pytest --reruns 1 --junitxml=...` 运行，对比 JUnit XML 与
`scripts/e2e/pytest_attempts.py` plugin 的 JSONL 输出。脚本可重跑（探针样本在
本仓库外复现：任何 `pytest --reruns 1 --junitxml` 短跑即可复核）。

## 结论（已冻结进 schema）

1. **JUnit 只保留 final outcome，不保留 attempt**：
   - rerun 后通过的 flaky 用例在 JUnit 中是**一个干净 pass 的 testcase**，
     没有任何 rerun 标记、属性或子节点；
   - 必败用例只有一个 testcase（两次 attempt 不产生两条记录）；
   - rerun 计数只出现在 stdout 摘要（"2 rerun"）。
   ⇒ 与 issue #2491 背景描述一致：**retry 后通过的 flaky 信号在 JUnit 中丢失**。
2. **attempt JSONL（`pytest_attempts.py`）为权威源**：每个 phase（setup/call/
   teardown）× 每次 attempt 一行；rerunfailures 重跑会重新走 setup，plugin 以
   setup 报告为界递增 attempt 序号；outcome 取值含 `rerun`（非 final）。
   final 判定 = 最后一次 attempt 的 call phase；first attempt 与 attempt 数
   完整保留 → flaky 信号不再依赖日志。
3. **双源一致性定义（冻结）**：per-nodeid 的 **final outcome 必须相等**
   （必要条件，JUnit 侧仅此一项）；attempt 级校验只对 JSONL 自身做
   （JUnit 无 attempt 信息，不参与 attempt 对账）。异常类提取自 longrepr 的
   `E   <Class>: <msg>` 行，best-effort，缺失时为 null（fingerprint 仍可由
   message/frames 构造）。
4. **collect-only 依赖实测**：`pytest tests/e2e --collect-only -q` **不需要**
   server / frontend build（1s 内收集 275 nodeids）。但 `pytest.ini` 的
   `addopts = -v` 会把 `-q` 的 nodeid 列表变成 collection 树，manifest 生成
   必须加 `-o addopts=`（已写进 `manifest.py` 与其生成协议）。

## 对 schema 的直接影响

- envelope 的 attempts 字段以 JSONL 行结构为准（nodeid/attempt/phase/outcome/
  duration/exception_class/message）；
- JUnit 交叉核对降级为 final-outcome 比对，避免 gate 自身 flaky（A2）；
- contract key 收录 pytest / rerunfailures / timeout 的 major（16.x 行为若有
  变化，重跑本探针并触发 observation-window 审查）。

## R6/N2/N3 待 issue 澄清的三问（随 P1 PR 提请）

1. 验收项 "known-only 正常 run 为 0" 的语义：按 (a)（comparator exit 0）实现，
   (b) 解读以参数化单测保留，澄清后删错支；
2. `observing/candidate + known-fail` 在 PR 侧按 issue 原文归 **advisory**
   （非阻断）；
3. required→deterministic-known-fail 的原子处置（对称扩展自"变 flaky"条款）。
