# Open ACE 数据库分析与优化方案

> 文档版本: 2.0
> 创建日期: 2026-03-22
> 更新日期: 2026-03-22
> 作者: Qwen Code
>
> **v2.0 更新说明**: 基于代码审阅，补充了迁移文件与代码不一致的问题、缺失的表定义、动态建表问题等关键发现。

---

## 目录

1. [当前架构概览](#1-当前架构概览)
2. [表结构设计](#2-表结构设计)
3. [发现的问题](#3-发现的问题)
4. [优化方案](#4-优化方案)
5. [SQLite vs PostgreSQL 对比](#5-sqlite-vs-postgresql-对比)
6. [迁移建议](#6-迁移建议)
7. [执行计划](#7-执行计划)

---

## 1. 当前架构概览

### 1.1 技术栈

| 项目 | 值 |
|------|-----|
| **数据库类型** | SQLite |
| **存储位置** | `~/.open-ace/ace.db` |
| **ORM/访问方式** | 原生 sqlite3 + Alembic 迁移 |
| **连接管理** | 每次请求新建连接，无连接池 |

### 1.2 表统计

| 表名 | 用途 | 预估字段数 | 定义位置 |
|------|------|-----------|----------|
| `users` | 用户账户 | 12+ | 迁移文件 ✅ |
| `sessions` | 会话管理 | 6 | 迁移文件 ✅ |
| `daily_usage` | 使用量统计 | 11 | 迁移文件 ✅ |
| `daily_messages` | 消息记录 | 22 | 迁移文件 ✅ |
| `tenants` | 租户信息 | 16 | 动态创建 ⚠️ |
| `tenant_usage` | 租户使用量 | 8 | 动态创建 ⚠️ |
| `content_filter_rules` | 内容过滤规则 | 9 | 动态创建 ⚠️ |
| `quota_usage` | 用户配额使用 | 8 | 动态创建 ⚠️ |
| `quota_alerts` | 配额告警 | 13 | 动态创建 ⚠️ |
| `audit_logs` | 审计日志 | 13 | 动态创建 ⚠️ |

> ⚠️ **注意**: 迁移文件 `migrations/versions/20260321_001_initial_schema.py` 仅定义了 4 个表，其余 6 个表通过代码动态创建，存在维护风险。

### 1.3 项目特点

- **多租户架构**: 支持 tenants 表，具备 SaaS 能力
- **用户认证**: 完整的 users/sessions 表
- **消息追踪**: daily_messages 高频写入
- **使用量统计**: daily_usage 聚合统计
- **配额管理**: quota_usage + quota_alerts 表支持用户配额和告警
- **审计日志**: audit_logs 表记录操作审计
- **内容安全**: content_filter_rules 表支持内容过滤

---

## 2. 表结构设计

### 2.1 ERD 图

```mermaid
erDiagram
    users ||--o{ sessions : has
    users ||--o{ daily_messages : creates
    users ||--o{ quota_usage : consumes
    users ||--o{ quota_alerts : receives
    users ||--o{ audit_logs : generates
    tenants ||--o{ tenant_usage : tracks
    tenants ||--o{ users : contains

    users {
        integer id PK
        string username UK
        string email
        string password_hash
        string role
        integer is_active
        timestamp created_at
        timestamp last_login
        integer daily_token_quota
        integer monthly_token_quota
        integer daily_request_quota
        integer monthly_request_quota
    }

    sessions {
        integer id PK
        string token UK
        integer user_id FK
        timestamp created_at
        timestamp expires_at
        integer is_active
    }

    daily_usage {
        integer id PK
        string date
        string tool_name
        string host_name
        integer tokens_used
        integer input_tokens
        integer output_tokens
        integer cache_tokens
        integer request_count
        text models_used
        timestamp created_at
    }

    daily_messages {
        integer id PK
        string date
        string tool_name
        string host_name
        string message_id
        string parent_id
        string role
        text content
        text full_entry
        integer tokens_used
        integer input_tokens
        integer output_tokens
        string model
        string timestamp
        string sender_id
        string sender_name
        string message_source
        string feishu_conversation_id
        string group_subject
        integer is_group_chat
        string agent_session_id
        string conversation_id
        timestamp created_at
    }

    tenants {
        integer id PK
        string name
        string slug UK
        string status
        string plan
        string contact_email
        string contact_phone
        string contact_name
        text quota
        text settings
        timestamp created_at
        timestamp updated_at
        timestamp trial_ends_at
        timestamp subscription_ends_at
        integer user_count
        integer total_tokens_used
        integer total_requests_made
    }

    tenant_usage {
        integer id PK
        integer tenant_id FK
        string date
        integer tokens_used
        integer requests_made
        integer active_users
        integer new_users
        timestamp created_at
    }

    content_filter_rules {
        integer id PK
        string pattern
        string type
        string severity
        string action
        integer is_enabled
        string description
        timestamp created_at
        timestamp updated_at
    }

    quota_usage {
        integer id PK
        integer user_id FK
        string date
        string period
        integer tokens_used
        integer requests_used
        timestamp created_at
        timestamp updated_at
    }

    quota_alerts {
        integer id PK
        integer user_id FK
        string alert_type
        string quota_type
        string period
        real threshold
        integer current_usage
        integer quota_limit
        real percentage
        string message
        timestamp created_at
        integer acknowledged
        timestamp acknowledged_at
        integer acknowledged_by
    }

    audit_logs {
        integer id PK
        timestamp timestamp
        integer user_id
        string username
        string action
        string severity
        string resource_type
        string resource_id
        text details
        string ip_address
        string user_agent
        string session_id
        integer success
        string error_message
    }
```

> **ERD 更新说明**:
> - 移除了 `user_permissions` 表（代码中未实现）
> - 添加了 `quota_alerts` 和 `audit_logs` 表
> - 修正了 `sessions` 表的 `session_id` 为 `token`（与代码一致）
> - 补充了 `users` 表的配额字段
> - 补充了 `tenants` 表的联系人和统计字段

### 2.2 表详细结构

#### users 表

| 列名 | 类型 | 约束 | 说明 | 代码使用 |
|------|------|------|------|----------|
| id | INTEGER | PRIMARY KEY | 自增主键 | ✅ |
| username | VARCHAR | UNIQUE, NOT NULL | 用户名 | ✅ |
| email | VARCHAR | | 邮箱 | ✅ |
| password_hash | VARCHAR | NOT NULL | 密码哈希 | ✅ |
| role | VARCHAR | DEFAULT 'user' | 角色 | ✅ 代码使用，迁移缺失 |
| is_active | INTEGER | DEFAULT 1 | 是否激活 | ✅ |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 | ✅ |
| last_login | TIMESTAMP | | 最后登录 | ✅ |
| daily_token_quota | INTEGER | | 每日 Token 配额 | ✅ 代码使用，迁移缺失 |
| monthly_token_quota | INTEGER | | 每月 Token 配额 | ✅ 代码使用，迁移缺失 |
| daily_request_quota | INTEGER | | 每日请求配额 | ✅ 代码使用，迁移缺失 |
| monthly_request_quota | INTEGER | | 每月请求配额 | ✅ 代码使用，迁移缺失 |

> ⚠️ **问题**: 迁移文件定义了 `is_admin` 列，但代码使用 `role` 列。迁移文件缺少 `role` 和配额相关字段。

#### daily_messages 表

| 列名 | 类型 | 约束 | 说明 | 代码使用 |
|------|------|------|------|----------|
| id | INTEGER | PRIMARY KEY | 自增主键 | ✅ |
| date | VARCHAR | NOT NULL | 日期 (YYYY-MM-DD) | ✅ |
| tool_name | VARCHAR | NOT NULL | 工具名称 | ✅ |
| host_name | VARCHAR | DEFAULT 'localhost' | 主机名 | ✅ |
| message_id | VARCHAR | NOT NULL | 消息唯一 ID | ✅ |
| parent_id | VARCHAR | | 父消息 ID | ✅ |
| role | VARCHAR | NOT NULL | 角色 (user/assistant/system) | ✅ |
| content | TEXT | | 消息内容 | ✅ |
| full_entry | TEXT | | 完整 JSON | ✅ |
| tokens_used | INTEGER | DEFAULT 0 | Token 使用量 | ✅ |
| input_tokens | INTEGER | DEFAULT 0 | 输入 Token | ✅ |
| output_tokens | INTEGER | DEFAULT 0 | 输出 Token | ✅ |
| model | VARCHAR | | 模型名称 | ✅ |
| timestamp | VARCHAR | | 时间戳 | ✅ |
| sender_id | VARCHAR | | 发送者 ID | ✅ |
| sender_name | VARCHAR | | 发送者名称 | ✅ |
| message_source | VARCHAR | | 消息来源 | ✅ |
| feishu_conversation_id | VARCHAR | | 飞书会话 ID | ✅ |
| group_subject | VARCHAR | | 群组主题 | ✅ |
| is_group_chat | INTEGER | | 是否群聊 | ✅ |
| agent_session_id | VARCHAR | | Agent 会话 ID | ✅ 迁移定义，代码未用 |
| conversation_id | VARCHAR | | 会话 ID | ✅ 迁移定义，代码未用 |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 | ✅ |

#### sessions 表

| 列名 | 类型 | 约束 | 说明 | 代码使用 |
|------|------|------|------|----------|
| id | INTEGER | PRIMARY KEY | 自增主键 | ✅ |
| token | VARCHAR | UNIQUE, NOT NULL | 会话令牌 | ✅ 代码使用 token |
| session_id | VARCHAR | UNIQUE, NOT NULL | 会话 ID | ⚠️ 迁移定义，代码未用 |
| user_id | INTEGER | NOT NULL | 用户 ID | ✅ |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 | ✅ |
| expires_at | TIMESTAMP | NOT NULL | 过期时间 | ✅ |
| is_active | INTEGER | DEFAULT 1 | 是否活跃 | ✅ |

> ⚠️ **问题**: 迁移文件定义 `session_id` 列，但代码使用 `token` 列。需要统一命名。

#### tenants 表（动态创建）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| name | TEXT | NOT NULL | 租户名称 |
| slug | TEXT | UNIQUE, NOT NULL | URL 友好标识 |
| status | TEXT | DEFAULT 'active' | 状态 |
| plan | TEXT | DEFAULT 'standard' | 套餐 |
| contact_email | TEXT | | 联系邮箱 |
| contact_phone | TEXT | | 联系电话 |
| contact_name | TEXT | | 联系人 |
| quota | TEXT | | 配额配置 (JSON) |
| settings | TEXT | | 设置 (JSON) |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 |
| updated_at | TIMESTAMP | DEFAULT NOW | 更新时间 |
| trial_ends_at | TIMESTAMP | | 试用结束时间 |
| subscription_ends_at | TIMESTAMP | | 订阅结束时间 |
| user_count | INTEGER | DEFAULT 0 | 用户数 |
| total_tokens_used | INTEGER | DEFAULT 0 | 总 Token 使用量 |
| total_requests_made | INTEGER | DEFAULT 0 | 总请求数 |

#### tenant_usage 表（动态创建）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| tenant_id | INTEGER | NOT NULL, FK | 租户 ID |
| date | TEXT | NOT NULL | 日期 |
| tokens_used | INTEGER | DEFAULT 0 | Token 使用量 |
| requests_made | INTEGER | DEFAULT 0 | 请求数 |
| active_users | INTEGER | DEFAULT 0 | 活跃用户数 |
| new_users | INTEGER | DEFAULT 0 | 新用户数 |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 |

#### content_filter_rules 表（动态创建）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| pattern | TEXT | NOT NULL | 匹配模式 |
| type | TEXT | DEFAULT 'keyword' | 类型 (keyword/regex/pii) |
| severity | TEXT | DEFAULT 'medium' | 严重程度 |
| action | TEXT | DEFAULT 'warn' | 处理动作 |
| is_enabled | INTEGER | DEFAULT 1 | 是否启用 |
| description | TEXT | | 描述 |
| created_at | TEXT | NOT NULL | 创建时间 |
| updated_at | TEXT | | 更新时间 |

#### quota_usage 表（动态创建）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| user_id | INTEGER | NOT NULL | 用户 ID |
| date | TEXT | NOT NULL | 日期 |
| period | TEXT | DEFAULT 'daily' | 周期类型 |
| tokens_used | INTEGER | DEFAULT 0 | Token 使用量 |
| requests_used | INTEGER | DEFAULT 0 | 请求数 |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 |
| updated_at | TIMESTAMP | DEFAULT NOW | 更新时间 |

#### quota_alerts 表（动态创建）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| user_id | INTEGER | NOT NULL | 用户 ID |
| alert_type | TEXT | NOT NULL | 告警类型 |
| quota_type | TEXT | NOT NULL | 配额类型 |
| period | TEXT | DEFAULT 'daily' | 周期类型 |
| threshold | REAL | NOT NULL | 阈值 |
| current_usage | INTEGER | NOT NULL | 当前使用量 |
| quota_limit | INTEGER | NOT NULL | 配额限制 |
| percentage | REAL | NOT NULL | 使用百分比 |
| message | TEXT | | 告警消息 |
| created_at | TIMESTAMP | DEFAULT NOW | 创建时间 |
| acknowledged | INTEGER | DEFAULT 0 | 是否已确认 |
| acknowledged_at | TIMESTAMP | | 确认时间 |
| acknowledged_by | INTEGER | | 确认人 ID |

#### audit_logs 表（动态创建）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| timestamp | TIMESTAMP | DEFAULT NOW | 时间戳 |
| user_id | INTEGER | | 用户 ID |
| username | TEXT | | 用户名 |
| action | TEXT | NOT NULL | 操作类型 |
| severity | TEXT | DEFAULT 'info' | 严重程度 |
| resource_type | TEXT | | 资源类型 |
| resource_id | TEXT | | 资源 ID |
| details | TEXT | | 详情 (JSON) |
| ip_address | TEXT | | IP 地址 |
| user_agent | TEXT | | 用户代理 |
| session_id | TEXT | | 会话 ID |
| success | INTEGER | DEFAULT 1 | 是否成功 |
| error_message | TEXT | | 错误消息 |

---

## 3. 发现的问题

### 3.0 迁移文件与代码不一致（🔴 严重）

#### 3.0.1 迁移文件缺失表定义

**问题描述**: 迁移文件 `migrations/versions/20260321_001_initial_schema.py` 仅定义了 4 个表，但代码中实际使用了 10 个表。

| 表名 | 迁移定义 | 代码定义 | 位置 |
|------|----------|----------|------|
| daily_usage | ✅ | - | 迁移文件 |
| daily_messages | ✅ | - | 迁移文件 |
| users | ✅ | - | 迁移文件 |
| sessions | ✅ | - | 迁移文件 |
| tenants | ❌ | ✅ | tenant_repo.py |
| tenant_usage | ❌ | ✅ | tenant_repo.py |
| content_filter_rules | ❌ | ✅ | governance_repo.py |
| quota_usage | ❌ | ✅ | quota_manager.py |
| quota_alerts | ❌ | ✅ | quota_manager.py |
| audit_logs | ❌ | ✅ | audit_logger.py |

**影响**:
- 新环境部署时表结构不完整
- 无法通过 Alembic 管理所有表变更
- 团队协作时数据库状态不一致

#### 3.0.2 users 表字段不一致

**问题描述**: 迁移文件与代码使用的字段不匹配。

| 字段 | 迁移定义 | 代码使用 | 说明 |
|------|----------|----------|------|
| is_admin | ✅ | ❌ | 迁移定义，代码未使用 |
| role | ❌ | ✅ | 代码使用，迁移缺失 |
| daily_token_quota | ❌ | ✅ | 代码使用，迁移缺失 |
| monthly_token_quota | ❌ | ✅ | 代码使用，迁移缺失 |
| daily_request_quota | ❌ | ✅ | 代码使用，迁移缺失 |
| monthly_request_quota | ❌ | ✅ | 代码使用，迁移缺失 |

**代码证据** (`app/repositories/user_repo.py`):
```python
def create_user(self, username, email, password_hash, role='user', ...):
    cursor.execute('''
        INSERT INTO users (username, email, password_hash, role, ...)
    ''')

def update_user_quota(self, user_id, daily_token_quota=None, ...):
    updates.append('daily_token_quota = ?')
```

#### 3.0.3 sessions 表字段不一致

**问题描述**: 迁移文件定义 `session_id` 列，但代码使用 `token` 列。

| 字段 | 迁移定义 | 代码使用 |
|------|----------|----------|
| session_id | ✅ UNIQUE | ❌ 未使用 |
| token | ❌ | ✅ UNIQUE |

**代码证据** (`app/repositories/user_repo.py`):
```python
def create_session(self, user_id: int, token: str, ...):
    cursor.execute('''
        INSERT INTO sessions (user_id, token, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    ''')

def get_session_by_token(self, token: str):
    query = '''
        SELECT s.*, u.username, u.email, u.role
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token = ?
    '''
```

**影响**: 运行时会因列不存在而报错。

#### 3.0.4 动态建表问题汇总

**问题描述**: 以下模块在运行时动态创建表，绕过了迁移管理：

| 模块 | 文件 | 创建的表 |
|------|------|----------|
| TenantRepository | tenant_repo.py | tenants, tenant_usage |
| GovernanceRepository | governance_repo.py | content_filter_rules |
| QuotaManager | quota_manager.py | quota_usage, quota_alerts |
| AuditLogger | audit_logger.py | audit_logs |

**风险**:
1. 表结构变更无法追踪
2. 无法回滚到特定版本
3. 多开发者协作时产生冲突
4. 生产环境部署时可能遗漏表

### 3.1 索引问题

#### 3.1.1 索引过多

**问题描述**: `daily_messages` 表有 12 个索引，过多的索引会：
- 降低 INSERT/UPDATE 性能
- 增加存储空间
- 增加维护成本

**当前索引列表**:

```sql
idx_messages_date
idx_messages_tool_name
idx_messages_host_name
idx_messages_sender_name
idx_messages_sender_id
idx_messages_timestamp
idx_messages_role
idx_messages_date_tool
idx_messages_date_host
idx_messages_date_sender
idx_messages_date_tool_host
idx_messages_date_timestamp
idx_messages_date_role_timestamp
```

**建议**: 删除冗余索引，保留高频查询路径。

#### 3.1.2 索引引用不存在的列

**问题描述**: `app/utils/db_optimize.py` 中定义的索引引用了不存在的列。

```python
# 错误的索引定义
RECOMMENDED_INDEXES = {
    'daily_messages': [
        ('idx_messages_sender', ['sender']),      # 列不存在，应为 sender_name
        ('idx_messages_recipient', ['recipient']), # 列不存在
    ],
}
```

#### 3.1.3 缺少关键索引

**问题描述**: 以下高频查询列缺少索引：

| 表 | 列 | 查询场景 |
|----|----|---------|
| daily_messages | feishu_conversation_id | 会话历史查询 |
| daily_messages | conversation_id | 会话关联 |
| sessions | expires_at | 过期会话清理 |

### 3.2 数据类型问题

#### 3.2.1 日期/时间存储为字符串

**问题描述**: `date` 和 `timestamp` 列使用 VARCHAR 存储，导致：
- 无法使用日期函数
- 字符串比较可能不准确
- 无法使用日期索引优化

```sql
-- 当前定义
date VARCHAR NOT NULL
timestamp VARCHAR

-- 建议定义
date DATE NOT NULL
timestamp TIMESTAMP
```

#### 3.2.2 布尔值使用 INTEGER

**问题描述**: 布尔字段使用 INTEGER (0/1)，语义不清晰。

```sql
-- 当前定义
is_active INTEGER DEFAULT 1
is_group_chat INTEGER

-- PostgreSQL 建议定义
is_active BOOLEAN DEFAULT TRUE
is_group_chat BOOLEAN
```

### 3.3 表设计问题

#### 3.3.1 缺少外键约束

**问题描述**: 多个表缺少外键约束，可能导致孤儿记录。

| 表 | 列 | 应引用 |
|----|----|----|
| sessions | user_id | users.id |
| tenant_usage | tenant_id | tenants.id |
| quota_usage | user_id | users.id |
| user_permissions | user_id | users.id |

**风险**:
- 删除用户后，相关记录不会被清理
- 数据完整性无法保证

#### 3.3.2 列定义不一致

**问题描述**: 迁移脚本与代码中的列名不一致。

| 表 | 迁移定义 | 代码使用 |
|----|---------|---------|
| sessions | session_id | token |
| users | is_admin | role |

```python
# user_repo.py 使用 token
def create_session(self, user_id: int, token: str, ...):
    cursor.execute('''
        INSERT INTO sessions (user_id, token, ...)
    ''')

# 迁移定义 session_id
sa.Column('session_id', sa.String(), nullable=False, unique=True)
```

#### 3.3.3 缺少列定义

**问题描述**: 代码中使用的列在迁移中未定义。

```python
# user_repo.py 使用 role 列
def create_user(self, username, email, password_hash, role='user', ...):
    cursor.execute('''
        INSERT INTO users (username, email, password_hash, role, ...)
    ''')

# 迁移中未定义 role 列
# 只有 is_admin INTEGER
```

### 3.4 JSON 存储问题

#### 3.4.1 JSON 字段无法索引

**问题描述**: `tenants` 表的 `quota` 和 `settings` 字段存储为 JSON 文本，无法高效查询。

```sql
-- 当前查询方式（全表扫描）
SELECT * FROM tenants
WHERE json_extract(quota, '$.daily_token_limit') > 1000000;
```

**影响**:
- 配额查询性能差
- 无法按配额条件筛选租户

#### 3.4.2 JSON 数组查询困难

**问题描述**: `daily_usage.models_used` 存储为 JSON 数组，无法高效查询特定模型的使用记录。

```sql
-- 无法高效执行
SELECT * FROM daily_usage
WHERE 'gpt-4' IN (SELECT value FROM json_each(models_used));
```

### 3.5 架构问题

#### 3.5.1 动态建表

**问题描述**: `tenant_repo.py` 在运行时创建表，违反迁移管理原则。

```python
def _ensure_tables(self) -> None:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tenants (...)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tenant_usage (...)
    ''')
```

**风险**:
- 表结构变更难以追踪
- 无法版本控制
- 团队协作困难

#### 3.5.2 配置文件存储

**问题描述**: 安全设置存储在 JSON 文件而非数据库。

```python
# governance_repo.py
SETTINGS_FILE = os.path.join(CONFIG_DIR, "governance_settings.json")
```

**风险**:
- 无法审计配置变更
- 多实例部署时配置不同步
- 备份不完整

#### 3.5.3 缺少软删除

**问题描述**: 重要数据表缺少 `deleted_at` 字段，无法恢复误删数据。

| 表 | 需要软删除 | 当前状态 |
|----|-----------|---------|
| users | ✅ | ❌ 物理删除 |
| tenants | ✅ | ⚠️ 改状态 |
| daily_messages | ✅ | ❌ 物理删除 |

### 3.6 问题严重程度汇总

| 问题类别 | 严重程度 | 影响范围 | 新增/原有 |
|----------|----------|----------|-----------|
| 迁移文件缺失表定义 | 🔴 高 | 部署失败 | 🆕 新增 |
| users 表字段不一致 | 🔴 高 | 运行时错误 | 🆕 新增 |
| sessions 表字段不一致 | 🔴 高 | 运行时错误 | 🆕 新增 |
| 索引引用不存在的列 | 🔴 高 | 代码错误 | 原有 |
| 列定义不一致 | 🔴 高 | 运行时错误 | 原有 |
| 缺少列定义 | 🔴 高 | 运行时错误 | 原有 |
| 缺少外键约束 | 🔴 高 | 数据完整性 | 原有 |
| 动态建表绕过迁移 | 🔴 高 | 可维护性 | 🆕 新增 |
| 索引过多 | 🟡 中 | 写入性能 | 原有 |
| 缺少关键索引 | 🟡 中 | 查询性能 | 原有 |
| JSON 存储问题 | 🟡 中 | 查询性能 | 原有 |
| 数据类型问题 | 🟡 中 | 语义清晰度 | 原有 |
| 配置文件存储 | 🟡 中 | 多实例同步 | 原有 |
| 缺少软删除 | 🟢 低 | 数据恢复 | 原有 |

> **优先级建议**: 必须先解决 🔴 高严重程度问题，特别是迁移文件与代码不一致的问题，否则系统无法正常运行。

---

## 4. 优化方案

### 4.0 方案零：迁移文件修复（🔴 最高优先级）

> **必须首先执行此方案**，否则系统无法正常运行。

#### 4.0.1 创建新的迁移文件补充缺失的表

```bash
# 创建迁移文件
alembic revision -m "add_missing_tables"
```

```python
# migrations/versions/xxxx_add_missing_tables.py

def upgrade() -> None:
    # 创建 tenants 表
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), unique=True, nullable=False),
        sa.Column('status', sa.Text(), server_default='active'),
        sa.Column('plan', sa.Text(), server_default='standard'),
        sa.Column('contact_email', sa.Text()),
        sa.Column('contact_phone', sa.Text()),
        sa.Column('contact_name', sa.Text()),
        sa.Column('quota', sa.Text()),
        sa.Column('settings', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('trial_ends_at', sa.TIMESTAMP()),
        sa.Column('subscription_ends_at', sa.TIMESTAMP()),
        sa.Column('user_count', sa.Integer(), server_default='0'),
        sa.Column('total_tokens_used', sa.Integer(), server_default='0'),
        sa.Column('total_requests_made', sa.Integer(), server_default='0'),
    )
    op.create_index('idx_tenants_slug', 'tenants', ['slug'])
    op.create_index('idx_tenants_status', 'tenants', ['status'])

    # 创建 tenant_usage 表
    op.create_table(
        'tenant_usage',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Text(), nullable=False),
        sa.Column('tokens_used', sa.Integer(), server_default='0'),
        sa.Column('requests_made', sa.Integer(), server_default='0'),
        sa.Column('active_users', sa.Integer(), server_default='0'),
        sa.Column('new_users', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('tenant_id', 'date', name='uq_tenant_usage_tenant_date'),
    )
    op.create_index('idx_tenant_usage_tenant', 'tenant_usage', ['tenant_id'])
    op.create_index('idx_tenant_usage_date', 'tenant_usage', ['date'])

    # 创建 content_filter_rules 表
    op.create_table(
        'content_filter_rules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('type', sa.Text(), server_default='keyword'),
        sa.Column('severity', sa.Text(), server_default='medium'),
        sa.Column('action', sa.Text(), server_default='warn'),
        sa.Column('is_enabled', sa.Integer(), server_default='1'),
        sa.Column('description', sa.Text()),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text()),
    )

    # 创建 quota_usage 表
    op.create_table(
        'quota_usage',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Text(), nullable=False),
        sa.Column('period', sa.Text(), server_default='daily'),
        sa.Column('tokens_used', sa.Integer(), server_default='0'),
        sa.Column('requests_used', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('user_id', 'date', 'period', name='uq_quota_usage_user_date_period'),
    )
    op.create_index('idx_quota_usage_user', 'quota_usage', ['user_id'])
    op.create_index('idx_quota_usage_date', 'quota_usage', ['date'])

    # 创建 quota_alerts 表
    op.create_table(
        'quota_alerts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('alert_type', sa.Text(), nullable=False),
        sa.Column('quota_type', sa.Text(), nullable=False),
        sa.Column('period', sa.Text(), server_default='daily'),
        sa.Column('threshold', sa.REAL(), nullable=False),
        sa.Column('current_usage', sa.Integer(), nullable=False),
        sa.Column('quota_limit', sa.Integer(), nullable=False),
        sa.Column('percentage', sa.REAL(), nullable=False),
        sa.Column('message', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('acknowledged', sa.Integer(), server_default='0'),
        sa.Column('acknowledged_at', sa.TIMESTAMP()),
        sa.Column('acknowledged_by', sa.Integer()),
    )
    op.create_index('idx_quota_alerts_user', 'quota_alerts', ['user_id'])
    op.create_index('idx_quota_alerts_created', 'quota_alerts', ['created_at'])

    # 创建 audit_logs 表
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('timestamp', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('user_id', sa.Integer()),
        sa.Column('username', sa.Text()),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('severity', sa.Text(), server_default='info'),
        sa.Column('resource_type', sa.Text()),
        sa.Column('resource_id', sa.Text()),
        sa.Column('details', sa.Text()),
        sa.Column('ip_address', sa.Text()),
        sa.Column('user_agent', sa.Text()),
        sa.Column('session_id', sa.Text()),
        sa.Column('success', sa.Integer(), server_default='1'),
        sa.Column('error_message', sa.Text()),
    )
    op.create_index('idx_audit_timestamp', 'audit_logs', ['timestamp'])
    op.create_index('idx_audit_user_id', 'audit_logs', ['user_id'])
    op.create_index('idx_audit_action', 'audit_logs', ['action'])
    op.create_index('idx_audit_resource', 'audit_logs', ['resource_type', 'resource_id'])
```

#### 4.0.2 修复 users 表字段

```bash
alembic revision -m "fix_users_table_fields"
```

```python
def upgrade() -> None:
    # 添加 role 列（替换 is_admin）
    op.add_column('users', sa.Column('role', sa.String(), server_default='user'))

    # 迁移数据：is_admin -> role
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin = 1")

    # 添加配额字段
    op.add_column('users', sa.Column('daily_token_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('monthly_token_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('daily_request_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('monthly_request_quota', sa.Integer(), nullable=True))

    # 添加索引
    op.create_index('idx_users_role', 'users', ['role'])
    op.create_index('idx_users_email', 'users', ['email'])

def downgrade() -> None:
    op.drop_index('idx_users_email', 'users')
    op.drop_index('idx_users_role', 'users')
    op.drop_column('users', 'monthly_request_quota')
    op.drop_column('users', 'daily_request_quota')
    op.drop_column('users', 'monthly_token_quota')
    op.drop_column('users', 'daily_token_quota')
    op.drop_column('users', 'role')
```

#### 4.0.3 修复 sessions 表字段

```bash
alembic revision -m "fix_sessions_table_fields"
```

```python
def upgrade() -> None:
    # SQLite 不支持直接重命名列，需要重建表
    op.create_table(
        'sessions_new',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('token', sa.String(), nullable=False, unique=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('is_active', sa.Integer(), server_default='1'),
    )

    # 迁移数据
    op.execute("""
        INSERT INTO sessions_new (id, token, user_id, created_at, expires_at, is_active)
        SELECT id, session_id, user_id, created_at, expires_at, is_active FROM sessions
    """)

    # 删除旧表，重命名新表
    op.drop_table('sessions')
    op.rename_table('sessions_new', 'sessions')

    # 创建索引
    op.create_index('idx_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('idx_sessions_token', 'sessions', ['token'])
    op.create_index('idx_sessions_expires', 'sessions', ['expires_at'])

def downgrade() -> None:
    # 回滚时重建原表结构
    op.create_table(
        'sessions_old',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.String(), nullable=False, unique=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('is_active', sa.Integer(), server_default='1'),
    )
    op.execute("""
        INSERT INTO sessions_old (id, session_id, user_id, created_at, expires_at, is_active)
        SELECT id, token, user_id, created_at, expires_at, is_active FROM sessions
    """)
    op.drop_table('sessions')
    op.rename_table('sessions_old', 'sessions')
    op.create_index('idx_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('idx_sessions_session_id', 'sessions', ['session_id'])
```

#### 4.0.4 移除动态建表代码

迁移完成后，需要移除各模块中的 `_ensure_tables` 方法：

| 文件 | 需要移除的方法 |
|------|---------------|
| `app/repositories/tenant_repo.py` | `_ensure_tables()` |
| `app/repositories/governance_repo.py` | `_ensure_tables()` |
| `app/modules/governance/quota_manager.py` | `_ensure_tables()` |
| `app/modules/governance/audit_logger.py` | `_ensure_table()` |

**修改示例**:

```python
# 修改前
class TenantRepository:
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()
        self._ensure_tables()  # 移除这行

    def _ensure_tables(self) -> None:
        # ... 动态建表代码

# 修改后
class TenantRepository:
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()
        # 表结构由 Alembic 迁移管理
```

### 4.1 方案一：索引优化（立即执行）

#### 4.1.1 删除无效/冗余索引

```sql
-- 删除引用不存在列的索引定义（代码层面）
-- 删除冗余索引
DROP INDEX IF EXISTS idx_messages_date_tool;      -- 被 idx_messages_date_tool_host 覆盖
DROP INDEX IF EXISTS idx_messages_date_host;      -- 被 idx_messages_date_tool_host 覆盖
DROP INDEX IF EXISTS idx_messages_date_sender;    -- 低频查询
```

#### 4.1.2 添加缺失索引

```sql
-- 会话查询索引
CREATE INDEX IF NOT EXISTS idx_messages_feishu_conv
    ON daily_messages(feishu_conversation_id);

CREATE INDEX IF NOT EXISTS idx_messages_conv_id
    ON daily_messages(conversation_id);

-- 会话历史查询优化
CREATE INDEX IF NOT EXISTS idx_messages_date_conv
    ON daily_messages(date, feishu_conversation_id);

-- sessions 表索引
CREATE INDEX IF NOT EXISTS idx_sessions_expires
    ON sessions(expires_at);

CREATE INDEX IF NOT EXISTS idx_sessions_active
    ON sessions(is_active, expires_at);
```

#### 4.1.3 优化复合索引

```sql
-- 替换多个单列索引为一个优化的复合索引
CREATE INDEX IF NOT EXISTS idx_messages_query_optimized
    ON daily_messages(date, tool_name, host_name, timestamp DESC);
```

### 4.2 方案二：表结构修复（需要迁移）

#### 4.2.1 修复 users 表

```sql
-- 迁移脚本: migrations/versions/xxxx_fix_users_table.py

def upgrade():
    # 添加缺失的列
    op.add_column('users', sa.Column('role', sa.String(), server_default='user'))
    op.add_column('users', sa.Column('daily_token_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('monthly_token_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('daily_request_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('monthly_request_quota', sa.Integer(), nullable=True))

    # 迁移 is_admin 到 role
    op.execute("""
        UPDATE users SET role = 'admin' WHERE is_admin = 1
    """)

    # 添加索引
    op.create_index('idx_users_role', 'users', ['role'])
    op.create_index('idx_users_active', 'users', ['is_active'])

def downgrade():
    op.drop_index('idx_users_active', 'users')
    op.drop_index('idx_users_role', 'users')
    op.drop_column('users', 'monthly_request_quota')
    op.drop_column('users', 'daily_request_quota')
    op.drop_column('users', 'monthly_token_quota')
    op.drop_column('users', 'daily_token_quota')
    op.drop_column('users', 'role')
```

#### 4.2.2 修复 sessions 表

```sql
-- SQLite 不支持直接重命名列，需要重建表

def upgrade():
    # 创建新表
    op.create_table(
        'sessions_new',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('token', sa.String(), nullable=False, unique=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('is_active', sa.Integer(), server_default='1'),
    )

    # 迁移数据
    op.execute("""
        INSERT INTO sessions_new (id, token, user_id, created_at, expires_at, is_active)
        SELECT id, session_id, user_id, created_at, expires_at, is_active FROM sessions
    """)

    # 删除旧表，重命名新表
    op.drop_table('sessions')
    op.rename_table('sessions_new', 'sessions')

    # 创建索引
    op.create_index('idx_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('idx_sessions_token', 'sessions', ['token'])
    op.create_index('idx_sessions_expires', 'sessions', ['expires_at'])
```

#### 4.2.3 添加外键约束

```sql
-- 为 quota_usage 表添加外键
def upgrade():
    # SQLite 需要重建表来添加外键
    op.create_table(
        'quota_usage_new',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('tool_name', sa.String()),
        sa.Column('tokens_used', sa.Integer(), server_default='0'),
        sa.Column('requests_used', sa.Integer(), server_default='0'),
    )

    op.execute("""
        INSERT INTO quota_usage_new SELECT * FROM quota_usage
    """)

    op.drop_table('quota_usage')
    op.rename_table('quota_usage_new', 'quota_usage')

    op.create_index('idx_quota_user_date', 'quota_usage', ['user_id', 'date'])
```

### 4.3 方案三：JSON 字段拆分（可选，长期优化）

#### 4.3.1 创建 tenant_quotas 表

```sql
CREATE TABLE tenant_quotas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    daily_token_limit INTEGER DEFAULT 1000000,
    monthly_token_limit INTEGER DEFAULT 30000000,
    daily_request_limit INTEGER DEFAULT 10000,
    monthly_request_limit INTEGER DEFAULT 300000,
    max_users INTEGER DEFAULT 100,
    max_sessions_per_user INTEGER DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id)
);

CREATE INDEX idx_tenant_quotas_tenant ON tenant_quotas(tenant_id);
```

#### 4.3.2 创建 tenant_settings 表

```sql
CREATE TABLE tenant_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    content_filter_enabled INTEGER DEFAULT 1,
    audit_log_enabled INTEGER DEFAULT 1,
    audit_log_retention_days INTEGER DEFAULT 90,
    data_retention_days INTEGER DEFAULT 365,
    sso_enabled INTEGER DEFAULT 0,
    sso_provider VARCHAR(50),
    custom_branding INTEGER DEFAULT 0,
    branding_name VARCHAR(100),
    branding_logo_url VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id)
);

CREATE INDEX idx_tenant_settings_tenant ON tenant_settings(tenant_id);
```

#### 4.3.3 数据迁移脚本

```python
# scripts/migrate_tenant_json.py

import json
from app.repositories.database import Database

def migrate_tenant_json_fields():
    """将 tenants 表的 JSON 字段迁移到独立表"""
    db = Database()

    # 获取所有租户
    tenants = db.fetch_all("SELECT id, quota, settings FROM tenants")

    for tenant in tenants:
        tenant_id = tenant['id']

        # 迁移 quota
        if tenant.get('quota'):
            quota = json.loads(tenant['quota'])
            db.execute("""
                INSERT OR REPLACE INTO tenant_quotas
                (tenant_id, daily_token_limit, monthly_token_limit,
                 daily_request_limit, monthly_request_limit, max_users, max_sessions_per_user)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                quota.get('daily_token_limit', 1000000),
                quota.get('monthly_token_limit', 30000000),
                quota.get('daily_request_limit', 10000),
                quota.get('monthly_request_limit', 300000),
                quota.get('max_users', 100),
                quota.get('max_sessions_per_user', 5),
            ))

        # 迁移 settings
        if tenant.get('settings'):
            settings = json.loads(tenant['settings'])
            db.execute("""
                INSERT OR REPLACE INTO tenant_settings
                (tenant_id, content_filter_enabled, audit_log_enabled,
                 audit_log_retention_days, data_retention_days, sso_enabled, sso_provider)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                settings.get('content_filter_enabled', True),
                settings.get('audit_log_enabled', True),
                settings.get('audit_log_retention_days', 90),
                settings.get('data_retention_days', 365),
                settings.get('sso_enabled', False),
                settings.get('sso_provider'),
            ))

    print(f"Migrated {len(tenants)} tenants")
```

### 4.4 方案四：添加软删除支持

```sql
-- 为重要表添加软删除字段
ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP;
ALTER TABLE tenants ADD COLUMN deleted_at TIMESTAMP;
ALTER TABLE daily_messages ADD COLUMN deleted_at TIMESTAMP;

-- 添加索引支持软删除查询
CREATE INDEX idx_users_deleted ON users(deleted_at);
CREATE INDEX idx_tenants_deleted ON tenants(deleted_at);
CREATE INDEX idx_messages_deleted ON daily_messages(deleted_at);
```

---

## 5. SQLite vs PostgreSQL 对比

### 5.1 功能对比

| 功能 | SQLite | PostgreSQL | 影响 |
|------|--------|------------|------|
| **并发写入** | ❌ 单写者锁 | ✅ MVCC 多写者 | 高并发场景 |
| **JSON 支持** | ⚠️ 基础 | ✅ JSONB + GIN 索引 | 配额查询 |
| **全文搜索** | ⚠️ FTS5 | ✅ 内置 tsvector | 消息搜索 |
| **窗口函数** | ⚠️ 有限 | ✅ 完整支持 | 统计分析 |
| **行级锁** | ❌ | ✅ | 高并发更新 |
| **外键约束** | ⚠️ 可选 | ✅ 强制 | 数据完整性 |
| **连接池** | ❌ 不需要 | ✅ 必需 | 连接管理 |
| **复制/HA** | ❌ | ✅ 流复制 | 高可用 |
| **分区表** | ❌ | ✅ | 大数据量 |

### 5.2 性能对比

#### 5.2.1 写入性能

| 操作 | SQLite | PostgreSQL | 说明 |
|------|--------|------------|------|
| 单条 INSERT | ~1ms | ~0.5ms | PostgreSQL 更快 |
| 批量 INSERT 1000 条 | ~1000ms | ~50ms | PostgreSQL 快 20x |
| 并发 10 写入 | 串行 ~100ms | 并行 ~10ms | PostgreSQL 快 10x |
| UPDATE 大表 | 锁表 | 行级锁 | PostgreSQL 不阻塞读 |

#### 5.2.2 查询性能

| 操作 | SQLite | PostgreSQL | 说明 |
|------|--------|------------|------|
| 简单 SELECT | ~0.1ms | ~0.1ms | 相当 |
| JOIN 3 表 | ~10ms | ~5ms | PostgreSQL 优化器更好 |
| JSON 查询 | ~100ms | ~5ms | PostgreSQL GIN 索引 |
| 全文搜索 | ~50ms | ~5ms | PostgreSQL tsvector |
| 复杂聚合 | ~200ms | ~20ms | PostgreSQL 并行查询 |

### 5.3 适用场景

| 场景 | SQLite | PostgreSQL |
|------|--------|------------|
| 单机/个人使用 | ✅ 推荐 | 过度设计 |
| 开发/测试环境 | ✅ 推荐 | ✅ 推荐 |
| 小团队 (<10 人) | ✅ 可接受 | ⚠️ 可选 |
| 中团队 (10-50 人) | ⚠️ 性能瓶颈 | ✅ 推荐 |
| 生产环境/多租户 | ❌ 不推荐 | ✅ 推荐 |
| 需要高可用 | ❌ 不支持 | ✅ 推荐 |
| 需要全文搜索 | ⚠️ 基础支持 | ✅ 推荐 |
| 需要复杂分析 | ⚠️ 性能差 | ✅ 推荐 |

### 5.4 项目需求匹配

根据 Open ACE 项目特点分析：

| 项目特点 | SQLite 支持 | PostgreSQL 支持 | 推荐 |
|----------|-------------|-----------------|------|
| 多租户架构 | ⚠️ 写锁竞争 | ✅ 并发写入 | PostgreSQL |
| 高频消息写入 | ⚠️ 串行化 | ✅ 并行写入 | PostgreSQL |
| JSON 配额查询 | ❌ 无索引 | ✅ GIN 索引 | PostgreSQL |
| 使用量统计 | ⚠️ 性能一般 | ✅ 窗口函数 | PostgreSQL |
| 会话管理 | ✅ 足够 | ✅ 更好 | 都可以 |
| 权限系统 | ✅ 足够 | ✅ 更好 | 都可以 |

**结论**: 建议迁移到 PostgreSQL，特别是如果：
- 用户数 > 10
- 需要多实例部署
- 需要高可用
- 需要复杂查询/分析

---

## 6. 迁移建议

### 6.1 迁移前准备

#### 6.1.1 添加依赖

```txt
# requirements.txt 添加
psycopg2-binary>=2.9.0  # PostgreSQL 驱动
# 或异步驱动
asyncpg>=0.28.0
```

#### 6.1.2 修改数据库连接

```python
# app/repositories/database.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

def get_database_url() -> str:
    """获取数据库 URL，支持 PostgreSQL 和 SQLite"""
    # 优先使用环境变量
    if os.environ.get('DATABASE_URL'):
        return os.environ['DATABASE_URL']

    # 默认 SQLite
    config_dir = os.path.expanduser("~/.open-ace")
    os.makedirs(config_dir, exist_ok=True)
    return f"sqlite:///{config_dir}/usage.db"

def get_engine_config() -> dict:
    """获取引擎配置"""
    url = get_database_url()

    if url.startswith('postgresql'):
        # PostgreSQL 配置
        return {
            'url': url,
            'pool_size': 10,
            'max_overflow': 20,
            'pool_pre_ping': True,
        }
    else:
        # SQLite 配置
        return {
            'url': url,
            'connect_args': {'check_same_thread': False},
        }

class Database:
    """数据库管理类"""

    def __init__(self, db_url: str = None):
        config = get_engine_config()
        if db_url:
            config['url'] = db_url

        self.engine = create_engine(
            config.pop('url'),
            **config
        )
        self.Session = sessionmaker(bind=self.engine)

    @contextmanager
    def connection(self):
        """连接上下文管理器"""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

### 6.2 数据迁移脚本

```python
#!/usr/bin/env python3
"""
scripts/utils/migrate_to_postgres.py

从 SQLite 迁移数据到 PostgreSQL
"""

import os
import sqlite3
import psycopg2
from psycopg2.extras import execute_values

def get_sqlite_connection():
    """获取 SQLite 连接"""
    db_path = os.path.expanduser("~/.open-ace/usage.db")
    return sqlite3.connect(db_path)

def get_postgres_connection():
    """获取 PostgreSQL 连接"""
    return psycopg2.connect(os.environ['DATABASE_URL'])

def migrate_table(sqlite_conn, pg_conn, table_name: str, columns: list):
    """迁移单个表"""
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    # 读取 SQLite 数据
    cols = ', '.join(columns)
    sqlite_cur.execute(f"SELECT {cols} FROM {table_name}")
    rows = sqlite_cur.fetchall()

    if not rows:
        print(f"  {table_name}: 0 rows (skipped)")
        return

    # 插入到 PostgreSQL
    placeholders = ', '.join(['%s'] * len(columns))
    sql = f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    execute_values(pg_cur, sql, rows)
    pg_conn.commit()

    print(f"  {table_name}: {len(rows)} rows migrated")

def main():
    """主迁移流程"""
    print("Starting migration from SQLite to PostgreSQL...")

    sqlite_conn = get_sqlite_connection()
    pg_conn = get_postgres_connection()

    # 迁移顺序（按外键依赖）
    tables = {
        'users': ['id', 'username', 'email', 'password_hash', 'role', 'is_active', 'created_at', 'last_login'],
        'sessions': ['id', 'session_id', 'user_id', 'created_at', 'expires_at', 'is_active'],
        'tenants': ['id', 'name', 'slug', 'status', 'plan', 'contact_email', 'contact_phone', 'contact_name', 'quota', 'settings', 'created_at', 'updated_at'],
        'tenant_usage': ['id', 'tenant_id', 'date', 'tokens_used', 'requests_made', 'active_users', 'new_users'],
        'daily_usage': ['id', 'date', 'tool_name', 'host_name', 'tokens_used', 'input_tokens', 'output_tokens', 'cache_tokens', 'request_count', 'models_used', 'created_at'],
        'daily_messages': ['id', 'date', 'tool_name', 'host_name', 'message_id', 'parent_id', 'role', 'content', 'full_entry', 'tokens_used', 'input_tokens', 'output_tokens', 'model', 'timestamp', 'sender_id', 'sender_name', 'message_source', 'feishu_conversation_id', 'group_subject', 'is_group_chat', 'agent_session_id', 'conversation_id', 'created_at'],
        'content_filter_rules': ['id', 'pattern', 'type', 'severity', 'action', 'is_enabled', 'description', 'created_at', 'updated_at'],
        'user_permissions': ['id', 'user_id', 'permission', 'granted_by', 'granted_at'],
        'quota_usage': ['id', 'user_id', 'date', 'tool_name', 'tokens_used', 'requests_used'],
    }

    for table, columns in tables.items():
        try:
            migrate_table(sqlite_conn, pg_conn, table, columns)
        except Exception as e:
            print(f"  {table}: ERROR - {e}")

    sqlite_conn.close()
    pg_conn.close()

    print("Migration completed!")

if __name__ == '__main__':
    main()
```

### 6.3 Docker 部署配置

```yaml
# docker-compose.yml 已有配置，启用 PostgreSQL

# 启动命令
# docker-compose --profile database up -d

# 或使用环境变量
export DATABASE_URL="postgresql://openace:password@postgres:5432/openace"
```

### 6.4 迁移检查清单

- [ ] 备份 SQLite 数据库
- [ ] 安装 PostgreSQL 驱动
- [ ] 配置 DATABASE_URL 环境变量
- [ ] 运行 Alembic 迁移创建表结构
- [ ] 执行数据迁移脚本
- [ ] 验证数据完整性
- [ ] 更新应用配置
- [ ] 运行测试套件
- [ ] 切换生产流量
- [ ] 监控性能指标

---

## 7. 执行计划

### 7.1 阶段划分

| 阶段 | 任务 | 优先级 | 风险 | 预计时间 |
|------|------|--------|------|----------|
| **阶段 0** | 🔴 **迁移文件修复（必须首先执行）** | 🔴 最高 | 中 | 2 天 |
| **阶段 1** | 修复索引问题 | 🟡 中 | 低 | 1 天 |
| **阶段 2** | 添加外键约束 | 🟡 中 | 中 | 1 天 |
| **阶段 3** | JSON 字段拆分 | 🟢 低 | 高 | 3 天 |
| **阶段 4** | 迁移到 PostgreSQL | 🟢 低 | 高 | 3 天 |

> **重要**: 阶段 0 是前置条件，必须先完成迁移文件修复，否则后续阶段无法正常执行。

### 7.2 详细执行步骤

#### 阶段 0: 迁移文件修复（🔴 必须首先执行）

```bash
# 1. 备份现有数据库
cp ~/.open-ace/usage.db ~/.open-ace/usage.db.backup

# 2. 创建迁移文件补充缺失的表
alembic revision -m "add_missing_tables"

# 3. 创建迁移文件修复 users 表
alembic revision -m "fix_users_table_fields"

# 4. 创建迁移文件修复 sessions 表
alembic revision -m "fix_sessions_table_fields"

# 5. 执行所有迁移
alembic upgrade head

# 6. 验证表结构
sqlite3 ~/.open-ace/usage.db ".tables"
sqlite3 ~/.open-ace/usage.db ".schema users"
sqlite3 ~/.open-ace/usage.db ".schema sessions"

# 7. 移除动态建表代码
# 编辑以下文件，移除 _ensure_tables 方法调用：
# - app/repositories/tenant_repo.py
# - app/repositories/governance_repo.py
# - app/modules/governance/quota_manager.py
# - app/modules/governance/audit_logger.py

# 8. 运行测试验证
python -m pytest tests/
```

#### 阶段 1: 索引优化

```bash
# 1. 创建迁移脚本
alembic revision -m "optimize_indexes"

# 2. 编辑迁移文件，添加索引变更

# 3. 执行迁移
alembic upgrade head

# 4. 验证索引
sqlite3 ~/.open-ace/usage.db ".indices"
```

#### 阶段 2: 外键约束

```bash
# 1. 创建迁移脚本
alembic revision -m "add_foreign_keys"

# 2. 执行迁移
alembic upgrade head

# 3. 验证约束
sqlite3 ~/.open-ace/usage.db "PRAGMA foreign_key_list(sessions)"
```

#### 阶段 6: PostgreSQL 迁移

```bash
# 1. 安装依赖
pip install psycopg2-binary

# 2. 启动 PostgreSQL
docker-compose --profile database up -d postgres

# 3. 配置环境变量
export DATABASE_URL="postgresql://openace:password@localhost:5432/openace"

# 4. 运行迁移
alembic upgrade head

# 5. 迁移数据
python scripts/utils/migrate_to_postgres.py

# 6. 验证
python -m pytest tests/
```

### 7.3 回滚方案

每个阶段都应准备回滚脚本：

```bash
# 回滚到指定版本
alembic downgrade -1

# 恢复备份
cp ~/.open-ace/usage.db.backup ~/.open-ace/usage.db
```

---

## 附录

### A. 推荐索引清单

```sql
-- ============================================
-- daily_usage 表
-- ============================================
CREATE INDEX idx_usage_date ON daily_usage(date);
CREATE INDEX idx_usage_tool ON daily_usage(tool_name);
CREATE INDEX idx_usage_host ON daily_usage(host_name);
CREATE INDEX idx_usage_date_tool ON daily_usage(date, tool_name);

-- ============================================
-- daily_messages 表
-- ============================================
CREATE INDEX idx_messages_date ON daily_messages(date);
CREATE INDEX idx_messages_sender ON daily_messages(sender_name);
CREATE INDEX idx_messages_feishu_conv ON daily_messages(feishu_conversation_id);
CREATE INDEX idx_messages_date_conv ON daily_messages(date, feishu_conversation_id);
CREATE INDEX idx_messages_query ON daily_messages(date, tool_name, host_name, timestamp DESC);

-- ============================================
-- users 表
-- ============================================
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_active ON users(is_active);

-- ============================================
-- sessions 表
-- ============================================
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_token ON sessions(token);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

-- ============================================
-- tenants 表
-- ============================================
CREATE INDEX idx_tenants_slug ON tenants(slug);
CREATE INDEX idx_tenants_status ON tenants(status);

-- ============================================
-- tenant_usage 表
-- ============================================
CREATE INDEX idx_tenant_usage_tenant ON tenant_usage(tenant_id);
CREATE INDEX idx_tenant_usage_date ON tenant_usage(date);
CREATE INDEX idx_tenant_usage_tenant_date ON tenant_usage(tenant_id, date);

-- ============================================
-- content_filter_rules 表
-- ============================================
CREATE INDEX idx_filter_rules_type ON content_filter_rules(type);
CREATE INDEX idx_filter_rules_enabled ON content_filter_rules(is_enabled);

-- ============================================
-- quota_usage 表
-- ============================================
CREATE INDEX idx_quota_usage_user ON quota_usage(user_id);
CREATE INDEX idx_quota_usage_date ON quota_usage(date);
CREATE INDEX idx_quota_usage_user_date ON quota_usage(user_id, date);

-- ============================================
-- quota_alerts 表
-- ============================================
CREATE INDEX idx_quota_alerts_user ON quota_alerts(user_id);
CREATE INDEX idx_quota_alerts_created ON quota_alerts(created_at);
CREATE INDEX idx_quota_alerts_unack ON quota_alerts(acknowledged, created_at);

-- ============================================
-- audit_logs 表
-- ============================================
CREATE INDEX idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX idx_audit_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_action ON audit_logs(action);
CREATE INDEX idx_audit_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_severity ON audit_logs(severity);
```

### B. 参考资料

- [PostgreSQL 官方文档](https://www.postgresql.org/docs/)
- [SQLite 官方文档](https://www.sqlite.org/docs.html)
- [Alembic 迁移指南](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Use The Index, Luke](https://use-the-index-luke.com/) - SQL 索引优化指南

---

*文档结束*
