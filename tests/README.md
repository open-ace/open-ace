# Tests 目录说明

本目录包含 Open ACE 项目的所有测试代码，按类型和用途进行组织。

## 目录结构

```
tests/
├── conftest.py              # pytest 配置和共享 fixtures
├── __init__.py              # Python 包初始化
│
├── unit/                    # 单元测试
│   ├── test_config.py       # config 模块测试
│   ├── test_db.py           # db 模块测试
│   ├── test_utils.py        # utils 模块测试
│   └── test_workspace_modules.py
│
├── ui/                      # UI 功能测试
│   └── test_*.py            # Playwright UI 测试（不带 issue 编号）
│
├── issues/                  # Issue 修复验证测试
│   └── {issue_number}/      # 按 GitHub issue 编号组织
│       └── test_*.py        # 验证特定 issue 的修复
│
├── regression/              # 回归测试
│   ├── test_login.py        # 登录功能
│   ├── test_navigation.py   # 导航功能
│   ├── test_manage_overview_dashboard.py
│   ├── test_manage_analysis_*.py  # Analysis 子页面 (5个)
│   ├── test_manage_governance_*.py # Governance 子页面 (4个)
│   ├── test_manage_users_*.py     # Users 子页面 (2个)
│   ├── test_manage_settings_sso.py
│   ├── test_work_*.py       # Work 模式页面 (3个)
│   └── run_regression.py    # 回归测试运行器
```

## 测试类型说明

### 单元测试 (`unit/`)
- 测试单个函数、类或模块
- 不依赖外部服务（数据库、网络等）
- 运行速度快，适合 CI/CD

### UI 测试 (`ui/`)
- 使用 Playwright 进行浏览器自动化测试
- 测试通用 UI 功能（不特定于某个 issue）
- 需要启动应用服务器

### Issue 测试 (`issues/`)
- 验证特定 GitHub issue 的修复
- 按 issue 编号组织，每个 issue 一个目录
- 用于确保修复有效且不会再次出现

### 回归测试 (`regression/`)
- 测试核心功能，确保每次修改后系统仍然正常工作
- 包含登录、导航、各页面功能等关键测试
- 建议在每次发布前运行

**测试文件命名规范：**
```
test_模式_一级菜单_二级菜单.py
```

例如：
- `test_manage_governance_audit.py` - Manage 模式 Governance 菜单 Audit 页面
- `test_work_sessions.py` - Work 模式 Sessions 页面

**回归测试覆盖的页面：**

| 模式 | 一级菜单 | 二级菜单 | 测试文件 |
|------|----------|----------|----------|
| Manage | Overview | Dashboard | test_manage_overview_dashboard.py |
| Manage | Analysis | Trend | test_manage_analysis_trend.py |
| Manage | Analysis | Anomaly | test_manage_analysis_anomaly.py |
| Manage | Analysis | ROI | test_manage_analysis_roi.py |
| Manage | Analysis | Conversation History | test_manage_analysis_conversation_history.py |
| Manage | Analysis | Messages | test_manage_analysis_messages.py |
| Manage | Governance | Audit | test_manage_governance_audit.py |
| Manage | Governance | Quota | test_manage_governance_quota.py |
| Manage | Governance | Compliance | test_manage_governance_compliance.py |
| Manage | Governance | Security | test_manage_governance_security.py |
| Manage | Users | Management | test_manage_users_management.py |
| Manage | Users | Tenants | test_manage_users_tenants.py |
| Manage | Settings | SSO | test_manage_settings_sso.py |
| Work | - | Workspace | test_work_workspace.py |
| Work | - | Sessions | test_work_sessions.py |
| Work | - | Prompts | test_work_prompts.py |

## 运行测试

### 运行所有测试
```bash
pytest
```

### 运行特定类型的测试
```bash
# 单元测试
pytest tests/unit/ -m unit

# UI 测试
pytest tests/ui/ -m ui

# 回归测试
pytest tests/regression/ -m regression
python tests/regression/run_regression.py
```

### 运行特定 issue 的测试
```bash
# 运行 issue 50 的测试
pytest tests/issues/50/

# 运行 issue 52 的测试
pytest tests/issues/52/
```

### 运行单个测试文件
```bash
pytest tests/ui/test_sessions_page.py
pytest tests/regression/test_login.py
```

## 测试命名规范

### 文件命名
- 测试文件：`test_*.py`
- 脚本文件：任意名称（放在 `scripts/` 目录）

### Issue 测试目录命名
- 必须使用 issue 编号作为目录名
- 例如：`tests/issues/50/` 对应 GitHub issue #50
- 禁止使用范围命名（如 `73-78`）
- 禁止使用描述性命名（如 `theme-btn-fix`）

### 测试函数命名
- 使用 `test_` 前缀
- 使用描述性名称，如 `test_login_success()`

## 截图目录

测试截图应放在 `screenshots/` 目录：
- 通用截图：`screenshots/`
- Issue 截图：`screenshots/issues/{issue_number}/`
- 回归测试截图：`screenshots/regression/`

## 环境变量

测试支持以下环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BASE_URL` | 应用服务器地址 | `http://localhost:5001` |
| `TEST_USERNAME` | 测试用户名 | `admin` |
| `TEST_PASSWORD` | 测试密码 | `admin123` |
| `HEADLESS` | 无头模式 | `true` |

## 最佳实践

1. **隔离测试数据**：所有数据库测试使用临时数据库，不写入生产数据
2. **使用 fixtures**：通过 `conftest.py` 共享测试配置和辅助函数
3. **添加标记**：使用 pytest markers 标记测试类型
4. **保持独立**：每个测试应独立运行，不依赖其他测试的结果
5. **清理资源**：测试结束后清理创建的临时文件和数据库