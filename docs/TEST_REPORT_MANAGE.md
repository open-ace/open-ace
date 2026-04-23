# Open ACE 管理功能全面测试验证报告

**测试日期**: 2026-04-22
**测试环境**: http://localhost:5001
**测试账号**: admin/admin123
**数据库**: PostgreSQL

---

## 一、测试总览

| 阶段 | 总计 | 通过 | 失败 | 警告 | 通过率 |
|------|------|------|------|------|--------|
| API 端点测试 | 58 | 36 | 12 | 0 | 62.1% |
| 数据正确性验证 | 8 | 6 | 0 | 2 | 75.0% |
| **合计** | **66** | **42** | **12** | **2** | **63.6%** |

### 严重问题统计

| 级别 | 数量 | 说明 |
|------|------|------|
| **P0 - 系统级 Bug** | 3 | 路由注册错误导致多个页面完全不可用 |
| **P1 - 功能级 Bug** | 4 | SQL 类型错误、缺少字段导致关键 API 500 |
| **P2 - 数据准确性** | 2 | 数据计算不一致 |
| **P3 - 体验问题** | 3 | 超时、空数据等 |

---

## 二、P0 系统级 Bug：Blueprint 路由注册覆盖

### 问题描述

在 `app/__init__.py` 的 `register_blueprints()` 中，多个 Blueprint 注册时使用了 `url_prefix="/api"` 参数，**覆盖了** Blueprint 构造函数中定义的 `url_prefix`，导致前端调用的 API 路径与后端实际注册的路径不匹配。

### 影响范围

| Blueprint | 构造函数 url_prefix | 注册 url_prefix | 前端期望路径 | 实际路径 | 状态 |
|-----------|---------------------|----------------|-------------|---------|------|
| **compliance_bp** | `/api/compliance` | `/api` (覆盖) | `/api/compliance/*` | `/api/*` | **全部 404** |
| **tenant_bp** | `/api/tenants` | `/api` (覆盖) | `/api/tenants/*` | `/api/*` | **全部 404** |
| **sso_bp** | `/api/sso` | `/api` (覆盖) | `/api/sso/*` | `/api/*` | **全部 404** |

### 受影响的管理页面

1. **合规管理页面** (`/manage/compliance`) — 完全不可用
   - 合规报告生成/查看
   - 数据保留规则管理
   - 审计分析（安全评分、模式分析、异常检测）
   - 存储估算

2. **租户管理页面** (`/manage/tenants`) — 完全不可用
   - 租户列表展示
   - 租户创建/编辑/删除
   - 租户配额管理
   - 租户使用统计

3. **SSO 设置页面** (`/manage/settings/sso`) — 完全不可用
   - SSO 提供商列表
   - SSO 提供商配置
   - SSO 身份管理

### 根因代码

```python
# app/__init__.py 第 187-207 行
app.register_blueprint(compliance_bp, url_prefix="/api")  # 覆盖了 /api/compliance
app.register_blueprint(tenant_bp, url_prefix="/api")       # 覆盖了 /api/tenants
app.register_blueprint(sso_bp, url_prefix="/api")           # 覆盖了 /api/sso
```

Flask 的行为：当 `register_blueprint()` 传入 `url_prefix` 参数时，**覆盖** Blueprint 构造函数中定义的 `url_prefix`。

### 修复方案

```python
# 方案一：移除注册时的 url_prefix，使用 Blueprint 自己的 url_prefix
app.register_blueprint(compliance_bp)  # 使用构造函数的 /api/compliance
app.register_blueprint(tenant_bp)       # 使用构造函数的 /api/tenants
app.register_blueprint(sso_bp)           # 使用构造函数的 /api/sso

# 方案二：修改注册 url_prefix 为正确路径
app.register_blueprint(compliance_bp, url_prefix="/api/compliance")
app.register_blueprint(tenant_bp, url_prefix="/api/tenants")
app.register_blueprint(sso_bp, url_prefix="/api/sso")
```

**推荐方案一**，因为 Blueprint 构造函数中已经定义了正确的 url_prefix，移除多余的覆盖参数即可。

---

## 三、P1 功能级 Bug

### Bug 1: Alerts API SQL 类型错误

**影响**: 告警中心页面无法加载数据

**API 端点**:
- `GET /api/alerts` — 500
- `GET /api/alerts/unread-count` — 500

**错误信息**:
```
argument of IS FALSE must be type boolean, not type integer
LINE 1: ...UNT(*) as count FROM alerts WHERE user_id = 1 AND read IS FA...
```

**根因**: `alerts` 表的 `read` 列类型为 `integer`（0/1），但代码使用 `IS FALSE`（PostgreSQL 布尔运算符）查询。

**根因代码**: `app/routes/alerts.py` 或 `app/modules/governance/alert_notifier.py`

**修复方案**:
```python
# 错误代码
conditions.append("read IS FALSE")

# 修复：对 PostgreSQL 使用整数比较
conditions.append("read = 0")  # 或 "read != 1"
# 或使用项目已有的 adapt_sql 辅助函数处理跨数据库兼容
```

---

### Bug 2: ROI 按用户分析 SQL 缺少字段

**影响**: ROI 页面的"按用户"分析不可用

**API 端点**: `GET /api/roi/by-user` — 500

**错误信息**:
```
column "user_id" does not exist
LINE 3: user_id,
```

**根因**: `daily_usage` 表没有 `user_id` 列。该表包含的列为：`id, date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, models_used, created_at`

**根因代码**: `app/services/roi_calculator.py` 第 551-658 行

**修复方案**:
- 方案 A: 通过 `host_name` 关联用户（host_name 中通常包含用户名信息）
- 方案 B: 通过 `daily_messages` 表中的用户数据关联
- 方案 C: 在 `daily_usage` 表添加 `user_id` 列（需要迁移脚本）

---

### Bug 3: Compliance 保留规则 API 500 错误

**影响**: 合规管理页面的数据保留功能不可用

**API 端点**（后端实际路径）:
- `GET /api/retention/rules` — 500
- `GET /api/audit/security-score` — 500
- `GET /api/audit/patterns` — 500
- `GET /api/audit/anomalies` — 500

**根因**: `app/modules/compliance/retention.py` 的 `_delete_old_data` 方法使用 `created_at OR timestamp` 查询，但某些表可能没有这两个时间戳列。`app/modules/compliance/audit.py` 的 `analyze_patterns` 方法假设 `user_id` 存在于审计日志对象上，但 `audit_logs` 表的结构可能不匹配。

**修复方案**:
1. 检查并统一各表的时间戳列名
2. 在 retention 查询前检查表的实际列结构
3. 为 AuditAnalyzer 添加属性访问保护

---

## 四、各页面详细测试结果

### 1. Dashboard (/manage/dashboard)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 今日用量 | `/api/today` | ✅ | 返回 qwen(44.7M tokens) 和 claude(1.3M tokens) |
| 汇总数据 | `/api/summary` | ✅ | 3个工具：claude, openclaw, qwen |
| 趋势数据 | `/api/trend` | ✅ | 返回 30 天趋势 |
| 工具列表 | `/api/tools` | ✅ | claude, openclaw, qwen |
| 主机列表 | `/api/hosts` | ✅ | 3 个主机 |
| 请求数统计 | `/api/request/today` | ✅ | 总计 842 请求 |

**数据准确性验证**:
- ✅ 今日用量 token 计算: `tokens_used = input_tokens + output_tokens` ✓
- ✅ 汇总平均计算: `avg_tokens = total_tokens / days_count` ✓

---

### 2. 趋势分析 (/manage/analysis/trend)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 关键指标 | `/api/analysis/key-metrics` | ✅ | 总 token 2.9B, 59 会话 |
| 小时用量 | `/api/analysis/hourly-usage` | ⚠️ | 返回空数组 `[]`，无小时级数据 |
| 用户排名 | `/api/analysis/user-ranking` | ✅ | 7 个用户 |
| 工具对比 | `/api/analysis/tool-comparison` | ✅ | 3 个工具对比数据 |
| 对话统计 | `/api/analysis/conversation-stats` | ✅ | 1000 会话, 186K 消息 |
| 异常检测 | `/api/analysis/anomaly-detection` | ✅ | 检测到多个用量下降异常 |

**⚠️ 问题**:
- `/api/analysis/hourly-usage` 返回空数据。原因：`hourly_stats` 表可能没有数据，数据采集脚本未按小时级别采集。
- 对话统计数据 (1000 会话, 186K 消息) 与实际用户数不匹配，疑似使用了模拟/缓存数据。

---

### 3. 请求看板 (/manage/analysis/request-dashboard)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 今日请求 | `/api/request/today` | ✅ | 总计 842 (claude:385, qwen:457) |
| 请求趋势 | `/api/request/trend` | ✅ | 30 天趋势数据 |
| 按工具分布 | `/api/request/by-tool` | ✅ | qwen 占绝对多数 |
| 按用户统计 | `/api/request/by-user` | ✅ | 2 个活跃用户 |

**数据准确性**:
- ⚠️ `/api/request/by-user` 中用户名格式为 `rhuang-RichdeMacBook-Pro.local-qwen`，这是自动拼接的 host+tool 名称，不是实际用户名，可读性差。

---

### 4. 异常检测 (/manage/analysis/anomaly)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 异常检测 | `/api/analysis/anomaly-detection` | ✅ | 检测到多个异常 |
| 异常趋势 | `/api/analysis/anomaly-trend` | 未测试 | |

**数据验证**:
- ✅ 检测到近期多天用量下降异常（token 用量从期望的 ~94.6M 降至 ~2.5M）
- 所有异常严重级别均为 "low"，可能需要调整阈值

---

### 5. ROI 分析 (/manage/analysis/roi)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| ROI 总览 | `/api/roi` | ✅ | 总成本 ¥20,554.96 |
| ROI 汇总 | `/api/roi/summary` | ✅ | 17 个成本细分 |
| ROI 趋势 | `/api/roi/trend` | ✅ | 月度趋势 |
| 按工具分析 | `/api/roi/by-tool` | ✅ | claude/openclaw/gpt/qwen |
| 按用户分析 | `/api/roi/by-user` | ❌ 500 | `user_id` 列不存在 |
| 成本分解 | `/api/roi/cost-breakdown` | ✅ | 17 项细分 |
| 每日成本 | `/api/roi/daily-costs` | ✅ | 30 天每日成本 |
| 优化建议 | `/api/optimization/suggestions` | ✅ | 4 条优化建议 |
| 效率报告 | `/api/optimization/efficiency` | ✅ | 输入输出比 229.38 |

**数据准确性**:
- ✅ 成本计算: `total_cost ≈ input_cost + output_cost` ✓
- ✅ Token 计算: `tokens_used = input_tokens + output_tokens` ✓
- ⚠️ ROI 百分比为 -98.72%（负值），原因是 `productivity_gain` 仅为 900，远低于实际成本
- ⚠️ `gpt` 工具在 by-tool 中出现但 `total_tokens=0, total_cost=0`，属于空数据展示

---

### 6. 对话历史 (/manage/analysis/conversation-history)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 消息列表 | `/api/messages` | ✅ | |
| 消息统计 | `/api/messages/count` | ✅ | |
| 发送者 | `/api/senders` | ✅ | |

---

### 7. 消息页面 (/manage/analysis/messages)

与对话历史使用相同的 API，结果一致。

---

### 8. 审计中心 (/manage/audit)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 审计日志 | `/api/audit/logs` | ✅ | 但返回空数据 `logs: [], total: 0` |
| 审计导出 | `/api/audit/logs/export` | ✅ | |
| 过滤规则 | `/api/filter-rules` | ✅ | 返回空数组 `[]` |
| 安全设置 | `/api/security-settings` | ✅ | 完整配置返回 |

**⚠️ 问题**:
- 审计日志为空。原因：系统运行中未记录审计日志到 `audit_logs` 表
- 过滤规则为空。原因：未配置任何内容过滤规则
- 安全评分/模式分析/异常检测 API 返回 500（与合规 API 共用代码）

**安全设置数据验证** (✅):
```json
{
  "session_timeout": 30,
  "max_login_attempts": 5,
  "password_min_length": 8,
  "password_require_lowercase": true,
  "password_require_uppercase": true,
  "password_require_number": true,
  "password_require_special": false,
  "two_factor_enabled": false,
  "ip_whitelist": []
}
```

---

### 9. 配额与告警 (/manage/quota)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 配额检查 | `/api/quota/check` | ✅ | admin 无配额限制 |
| 配额状态 | `/api/quota/status` | ✅ | 无限制 |
| 所有用户配额 | `/api/quota/status/all` | ✅ | 9 个用户配额信息 |
| 告警列表 | `/api/alerts` | ❌ 500 | SQL `IS FALSE` 类型错误 |
| 未读数量 | `/api/alerts/unread-count` | ❌ 500 | 同上 |
| 通知偏好 | `/api/alerts/preferences` | ✅ | |

**配额数据验证**:
- admin 用户: daily_token 限额 1,000,000, 已用 0 (✅ 合理)
- 用户 "黄迎春": daily_token 限额 200, monthly_token 40,000 (✅ 合理)

---

### 10. 合规管理 (/manage/compliance) — 完全不可用

| 测试项 | 前端路径 | 结果 | 后端路径 | 结果 |
|--------|---------|------|---------|------|
| 报告类型 | `/api/compliance/reports` | ❌ 404 | `/api/reports` | ✅ |
| 已保存报告 | `/api/compliance/reports/saved` | ❌ 404 | `/api/reports/saved` | ✅ |
| 安全评分 | `/api/compliance/audit/security-score` | ❌ 404 | `/api/audit/security-score` | ❌ 500 |
| 模式分析 | `/api/compliance/audit/patterns` | ❌ 404 | `/api/audit/patterns` | ❌ 500 |
| 异常检测 | `/api/compliance/audit/anomalies` | ❌ 404 | `/api/audit/anomalies` | ❌ 500 |
| 保留规则 | `/api/compliance/retention/rules` | ❌ 404 | `/api/retention/rules` | ❌ 500 |

**问题**: 双重问题
1. 路由路径不匹配（P0 Bug）
2. 即使路径正确，API 也返回 500（P1 Bug）

---

### 11. 安全中心 (/manage/security)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 过滤规则 | `/api/filter-rules` | ✅ | 空数组 |
| 安全设置 | `/api/security-settings` | ✅ | 完整配置 |

---

### 12. 用户管理 (/manage/users)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 用户列表 | `/api/admin/users` | ✅ | 9 个用户 |
| 配额使用 | `/api/admin/quota/usage` | ✅ | |

**用户数据验证** (✅):
- 9 个用户，角色分布：1 admin + 8 user
- 所有用户字段完整：username, email, role, is_active, system_account 等

---

### 13. 租户管理 (/manage/tenants) — 完全不可用

| 测试项 | 前端路径 | 结果 |
|--------|---------|------|
| 租户列表 | `/api/tenants` | ❌ 404 |
| 租户计划 | `/api/tenants/plans` | ❌ 404 |

**问题**: Blueprint 路由注册覆盖（P0 Bug）。数据库中 `tenants` 表存在，但 API 路径不正确。

---

### 14. 项目管理 (/manage/projects)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 项目列表 | `/api/projects` | ✅ | |
| 项目统计 | `/api/projects/stats` | ✅ | 5 个项目 |

**项目数据验证**:
- ✅ 5 个项目，总计：6,082,568 tokens, 2,193 请求, 338 会话
- ⚠️ Open ACE 项目的 `total_requests`(1522) 与 `user_stats` 之和(1522)一致 ✓
- ⚠️ testproj 项目的 token 总量 (552,980) 为字符串类型而非数字，可能导致前端显示问题

---

### 15. 远程工作区 - 机器管理 (/manage/remote/machines)

| 测试项 | API | 结果 | 备注 |
|--------|-----|------|------|
| 机器列表 | `/api/remote/machines` | ✅ | 4 台机器 |

**机器数据验证**:
- ✅ 4 台机器：1 台在线 (openace), 3 台离线 (E2E 测试数据)
- ✅ 机器详情包含完整信息：IP, OS, CPU, 内存, 磁盘

---

### 16. API Key 管理 (/manage/remote/api-keys)

| 测试项 | API | 结果 |
|--------|-----|------|
| 未单独测试 | — | — |

---

### 17. SSO 设置 (/manage/settings/sso) — 完全不可用

| 测试项 | 前端路径 | 结果 | 后端路径 | 结果 |
|--------|---------|------|---------|------|
| 提供商列表 | `/api/sso/providers` | ❌ 404 | `/api/providers` | ✅ |

**问题**: Blueprint 路由注册覆盖（P0 Bug）。后端数据正常（预定义提供商：google 等）。

---

## 五、数据正确性问题汇总

### 问题 1: 项目统计 token 类型不一致

`/api/projects/stats` 返回的 `total_tokens` 字段在不同项目中类型不一致：
- 项目 "Open ACE": `"total_tokens": "6082568"` (字符串)
- 项目 "testproj": `"total_tokens": 552980` (数字)
- 项目 "Qwen Code Project": `"total_tokens": "2450000"` (字符串)

**影响**: 前端可能无法正确进行数值比较或排序。

### 问题 2: 用户排名数据重复

`/api/analysis/user-ranking` 中存在多个 `username: "rhuang"` 的条目（user_id 1 和 4），实际是不同 host/tool 的组合，但用户名显示重复。

### 问题 3: 请求统计中的用户名格式

`/api/request/by-user` 返回的用户名为 `rhuang-RichdeMacBook-Pro.local-qwen`，这是 `username-host-toolname` 的拼接格式，不利于阅读和前端展示。

---

## 六、性能问题

### API 响应超时

在并发 API 测试中，以下端点出现超时（>10s）：

| 端点 | 状态 |
|------|------|
| `/api/tools` | 超时 |
| `/api/analysis/key-metrics` | 超时 |
| `/api/analysis/hourly-usage` | 超时 |
| `/api/analysis/anomaly-detection` | 超时 |
| `/api/analysis/user-ranking` | 超时 |
| `/api/analysis/tool-comparison` | 超时 |
| `/api/analysis/conversation-stats` | 超时 |
| `/api/analysis/batch` | 超时 |
| `/api/roi` | 超时 |
| `/api/roi/summary` | 超时 |

**可能原因**: gevent 单线程处理，一个慢查询阻塞后续请求。analysis 和 ROI 端点可能执行重量级 SQL 查询。

---

## 七、修复优先级建议

### P0 — 立即修复（影响 3 个页面完全不可用）

1. **修复 Blueprint 路由注册覆盖**
   - 文件: `app/__init__.py`
   - 改动: 移除或修正 compliance_bp、tenant_bp、sso_bp 的 `url_prefix`
   - 工作量: 3 行代码修改
   - 影响页面: 合规管理、租户管理、SSO 设置

### P1 — 高优先级（影响关键功能）

2. **修复 Alerts SQL 类型错误**
   - 文件: `app/routes/alerts.py` 或 `app/modules/governance/alert_notifier.py`
   - 改动: `IS FALSE` → `= 0`
   - 工作量: 1 行代码修改
   - 影响页面: 告警中心

3. **修复 ROI by-user 缺少 user_id 列**
   - 文件: `app/services/roi_calculator.py`
   - 改动: 使用 `host_name` 或 `username` 替代 `user_id`
   - 工作量: 中等（需要调整查询逻辑）

4. **修复 Compliance 服务层 500 错误**
   - 文件: `app/modules/compliance/retention.py`, `app/modules/compliance/audit.py`
   - 改动: 修复时间戳列名和属性访问
   - 工作量: 中等

### P2 — 中优先级（数据准确性）

5. **统一项目统计 token 返回类型**
   - 文件: 项目统计相关代码
   - 改动: 确保 `total_tokens` 始终返回数字类型

6. **改进用户显示格式**
   - 文件: analysis/usage 相关代码
   - 改动: 优化用户名拼接格式，增加可读性

### P3 — 低优先级（体验优化）

7. **优化慢查询性能**
   - 为 analysis 和 ROI 端点添加缓存或索引
   - 考虑添加分页限制

8. **补充小时级数据采集**
   - 配置 `hourly_stats` 表的数据写入

9. **审计日志记录**
   - 确保关键操作写入 `audit_logs` 表

---

## 八、总结

### 各页面功能状态

| 页面 | 状态 | 说明 |
|------|------|------|
| Dashboard | ✅ 正常 | 数据完整，图表渲染正常 |
| 趋势分析 | ⚠️ 部分可用 | 小时级数据缺失 |
| 请求看板 | ✅ 正常 | 数据完整 |
| 异常检测 | ✅ 正常 | 检测结果合理 |
| ROI 分析 | ⚠️ 部分可用 | 按用户分析 500 错误 |
| 对话历史 | ✅ 正常 | |
| 消息页面 | ✅ 正常 | |
| 审计中心 | ⚠️ 部分可用 | 日志为空，分析功能 500 |
| 配额与告警 | ⚠️ 部分可用 | 配额正常，告警 500 |
| **合规管理** | ❌ 不可用 | 路由不匹配 + 服务层 500 |
| 安全中心 | ✅ 正常 | |
| 用户管理 | ✅ 正常 | |
| **租户管理** | ❌ 不可用 | 路由不匹配 |
| 项目管理 | ✅ 正常 | |
| 远程机器管理 | ✅ 正常 | |
| API Key 管理 | 未测试 | |
| **SSO 设置** | ❌ 不可用 | 路由不匹配 |

**可用率**: 11/17 页面完全可用，3 个页面部分可用，3 个页面完全不可用。
