# 内容过滤规则增强功能 - 使用指南

## 概述

本次更新为 Open ACE 的内容过滤系统添加了以下增强功能：

- **测试规则隔离**：标记和过滤测试规则，避免干扰生产环境
- **审批流程**：规则需要审批后才能生效
- **多租户隔离**：规则按租户隔离，避免跨租户访问
- **优先级排序**：规则按优先级执行
- **匹配策略**：支持 all/first/highest 三种匹配策略
- **触发日志**：记录规则触发历史，用于监控和分析
- **版本控制**：规则版本快照和回滚功能
- **动态日志级别**：根据规则属性自动调整日志级别

## 快速开始

### 1. 数据库迁移

```bash
# 升级数据库
flask db upgrade

# 或使用 Alembic
alembic upgrade head
```

### 2. 清理测试规则

```bash
# 查看测试规则
python scripts/cleanup_test_filter_rules.py

# 禁用测试规则（跳过确认）
python scripts/cleanup_test_filter_rules.py --force --disable

# 删除测试规则
python scripts/cleanup_test_filter_rules.py --force --delete
```

### 3. 使用规则加载器

```python
from app.modules.governance.rule_loader import RuleLoader
from app.repositories.governance_repo import GovernanceRepository

# 创建加载器
repo = GovernanceRepository()
loader = RuleLoader(governance_repo=repo)

# 加载生产规则
rules = loader.load_rules(tenant_id=1)

# 加载所有规则（包括测试规则）
all_rules = loader.load_rules(include_test=True)
```

### 4. 使用规则缓存

```python
from app.modules.governance.rule_cache import RuleCache
from app.modules.governance.rule_loader import RuleLoader

# 创建缓存
loader = RuleLoader()
cache = RuleCache(rule_loader=loader)

# 获取规则（自动缓存）
rules = cache.get_rules(tenant_id=1)

# 失效缓存
cache.invalidate(tenant_id=1)
cache.invalidate()  # 失效所有租户
```

### 5. 使用内容过滤器

```python
from app.modules.governance.content_filter import ContentFilter

# 创建过滤器
filter = ContentFilter()

# 检查内容（自动过滤测试规则和未审批规则）
result = filter.check_content(
    "敏感内容",
    tenant_id=1,
    match_strategy="all"
)

if not result.passed:
    print(f"内容被拦截: {result.message}")
```

## 功能详解

### 测试规则隔离

测试规则（`is_test=True`）将被自动过滤，不会在实际内容检查中生效：

```sql
-- 标记为测试规则
UPDATE content_filter_rules
SET is_test = TRUE
WHERE id = 123;
```

### 审批流程

只有 `approval_status='approved'` 的规则才会生效：

```sql
-- 创建规则（默认 pending）
INSERT INTO content_filter_rules (pattern, type, approval_status)
VALUES ('测试模式', 'keyword', 'pending');

-- 审批通过
UPDATE content_filter_rules
SET approval_status = 'approved',
    approved_by = 1,
    approved_at = NOW()
WHERE id = 123;
```

### 多租户隔离

规则可以绑定到特定租户，或作为全局规则（`tenant_id=NULL`）：

```sql
-- 租户特定规则
INSERT INTO content_filter_rules (pattern, type, tenant_id)
VALUES ('租户1关键词', 'keyword', 1);

-- 全局规则（所有租户可见）
INSERT INTO content_filter_rules (pattern, type, tenant_id)
VALUES ('全局关键词', 'keyword', NULL);
```

### 优先级排序

规则按 `priority` 字段升序执行（数值越小优先级越高）：

```sql
-- 高优先级规则
INSERT INTO content_filter_rules (pattern, priority)
VALUES ('紧急关键词', 10);

-- 低优先级规则
INSERT INTO content_filter_rules (pattern, priority)
VALUES ('普通关键词', 100);
```

### 匹配策略

支持三种匹配策略：

1. **all**：匹配所有规则（默认）
2. **first**：匹配第一条后立即返回（短路）
3. **highest**：只返回优先级最高的匹配

```python
# 使用 'first' 策略
result = filter.check_content("内容", match_strategy="first")
```

### 触发日志

规则触发时会自动记录到 `filter_rule_trigger_log` 表：

```sql
-- 查询触发历史
SELECT
    r.pattern,
    COUNT(*) as trigger_count,
    MAX(t.matched_at) as last_triggered
FROM filter_rule_trigger_log t
JOIN content_filter_rules r ON t.rule_id = r.id
WHERE t.matched_at > NOW() - INTERVAL '7 days'
GROUP BY r.id, r.pattern
ORDER BY trigger_count DESC;
```

### 版本控制

规则的每次变更都会创建版本快照：

```python
from app.services.rule_version_manager import RuleVersionManager

# 创建版本管理器
manager = RuleVersionManager(governance_repo=repo)

# 创建版本快照
manager.create_version(rule_id=123, user_id=1, change_reason="更新规则")

# 查看版本历史
versions = manager.get_versions(rule_id=123)

# 回滚到指定版本
manager.rollback_to_version(
    rule_id=123,
    version_number=2,
    user_id=1,
    rollback_reason="发现错误"
)
```

## 监控和告警

### 关键指标

建议在监控系统中配置以下指标告警：

| 指标 | 阈值 | 说明 |
|------|------|------|
| 规则过滤延迟 P99 | > 50ms | 性能下降告警 |
| 触发日志写入失败率 | > 1% | 数据库问题告警 |
| 测试规则触发次数 | > 10次/小时 | 提醒清理测试规则 |
| 单条规则触发次数 | > 100次/小时 | 规则可能过于宽泛 |

### 查询统计

```sql
-- 高频触发规则
SELECT
    r.id,
    r.pattern,
    COUNT(*) as trigger_count
FROM filter_rule_trigger_log t
JOIN content_filter_rules r ON t.rule_id = r.id
WHERE t.matched_at > NOW() - INTERVAL '1 hour'
GROUP BY r.id
ORDER BY trigger_count DESC
LIMIT 10;
```

## 故障排查

### 常见问题

**问题**: 规则不生效
- 检查 `is_enabled` 是否为 TRUE
- 检查 `is_test` 是否为 FALSE
- 检查 `approval_status` 是否为 'approved'
- 检查 `tenant_id` 是否匹配

**问题**: 日志噪音过多
- 检查是否有测试规则触发
- 检查规则是否标记为 `is_test=TRUE`
- 运行清理脚本：`python scripts/cleanup_test_filter_rules.py`

**问题**: 性能下降
- 检查规则数量是否过多
- 检查正则表达式是否复杂
- 检查缓存命中率

## API 参考

详细 API 文档请参考：
- `app/routes/governance.py` - 规则管理 API
- `docs/API_REFERENCE.md` - 完整 API 文档

## 相关文档

- [实现总结](docs/ISSUE_2550_IMPLEMENTATION_SUMMARY.md)
- [数据库设计](docs/DATABASE_SCHEMA.md)
- [测试策略](docs/TEST_STRATEGY.md)
