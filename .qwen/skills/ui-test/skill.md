---
name: ui-test
description: 使用 Playwright 进行 UI 功能自动化测试，验证页面元素和交互功能是否正常工作
---

# UI 功能测试 Skill

此 skill 用于对 Web 应用进行自动化 UI 测试，验证功能是否真正实现。

## 使用方法

直接调用此 skill，AI 会根据当前上下文自动生成测试用例并执行。

```bash
# AI 会自动执行以下步骤：
1. 分析当前任务/问题，确定需要测试的功能
2. 生成测试用例（检查元素、点击按钮、验证交互）
3. 执行测试脚本
4. 生成测试报告和截图
```

## 重要：测试脚本复用流程

**在开始编写新测试脚本之前，必须执行以下流程：**

### Step 1: 检查现有脚本
使用 `glob` 工具检查 `tests/ui/` 目录下是否存在可复用的测试脚本：

```
glob pattern: tests/ui/*.py
```

### Step 2: 询问用户
使用 `ask_user_question` 工具询问用户是否要复用现有脚本：

```
问题: "tests/ui/ 目录下有以下测试脚本，是否要复用？"
选项:
- "是，选择一个脚本复用" 
- "否，创建新的测试脚本"
```

### Step 3: 根据用户选择执行

**如果用户选择"是"：**
1. 列出相关脚本（根据当前任务筛选，排除完全不相干的）
2. 让用户选择要复用的脚本
3. 基于选中的脚本进行测试或扩展

**如果用户选择"否"：**
1. 根据当前任务创建新的测试脚本
2. 新脚本应保存到 `tests/ui/` 目录以便未来复用

### 脚本筛选规则

列出脚本时，根据以下规则筛选相关脚本：
- 文件名包含当前任务相关的关键词（如 issue 编号、功能名称）
- 脚本描述/注释中提到相关功能
- 排除明显不相关的脚本（如测试完全不同功能的脚本）

### 示例

当前任务：测试 Messages 页面加载性能

**检查到的脚本：**
- `tests/ui/test_messages_page_loading.py` - 测试 Messages 页面加载（相关 ✅）
- `tests/ui/test_login.py` - 测试登录功能（可能相关）
- `tests/ui/test_dashboard_charts.py` - 测试 Dashboard 图表（不相关 ❌）

**提供给用户选择：**
1. test_messages_page_loading.py（推荐）
2. test_login.py
3. 创建新脚本

## 测试流程

### 1. 登录系统
- 自动填写用户名密码
- 点击登录按钮
- 验证登录成功

### 2. 导航到目标页面
- 点击侧边栏导航
- 切换 Tab 页
- 等待页面加载完成

### 3. 验证功能
- 检查元素是否存在
- 检查元素是否可见
- 点击按钮/链接
- 填写表单
- 验证交互效果

### 4. 截图记录
- 关键步骤截图
- 错误状态截图
- 最终效果截图

### 5. 生成报告
- 测试步骤记录
- 通过/失败状态
- 截图路径

## 测试用例示例

```python
# 测试用例格式
test_cases = [
    {
        "name": "测试列选择器功能",
        "steps": [
            {"action": "navigate", "target": "#nav-analysis"},
            {"action": "click", "target": "#session-history-tab"},
            {"action": "check_visible", "target": "#columnSelectorBtn"},
            {"action": "click", "target": "#columnSelectorBtn"},
            {"action": "check_count", "target": "#columnSelectorMenu .form-check-input", "expected": 11},
        ]
    }
]
```

## 支持的操作

| 操作 | 说明 | 示例 |
|------|------|------|
| `navigate` | 导航到页面/区域 | `#nav-analysis` |
| `click` | 点击元素 | `#submit-btn` |
| `fill` | 填写表单 | `#username`, value: `admin` |
| `check_visible` | 检查元素可见 | `#modal-dialog` |
| `check_exists` | 检查元素存在 | `#error-message` |
| `check_count` | 检查元素数量 | `.list-item`, expected: 5 |
| `wait` | 等待时间 | seconds: 2 |
| `screenshot` | 截图 | filename: `step1.png` |

## 配置

在 `scripts/config.py` 中可配置：
- `BASE_URL`: 测试目标 URL（默认：http://localhost:5001/）
- `USERNAME`: 登录用户名
- `PASSWORD`: 登录密码
- `VIEWPORT_SIZE`: 浏览器视口大小
- `HEADLESS`: 是否无头模式

## 输出

测试完成后会生成：
1. 控制台输出测试结果
2. 截图保存在 `screenshots/` 目录
3. HTML 测试报告（可选）

## 示例输出

```
========================================
UI 功能测试报告
========================================
测试时间: 2026-03-12 19:10:00
测试用例: 3 个
通过: 3 个
失败: 0 个
----------------------------------------

测试用例 1: 列选择器功能
  ✓ 导航到 Analysis 页面
  ✓ 点击 Session History Tab
  ✓ 检查列选择器按钮可见
  ✓ 点击列选择器按钮
  ✓ 检查下拉菜单包含 11 个选项
  状态: 通过 ✓

测试用例 2: 全屏功能
  ✓ 检查全屏按钮可见
  ✓ 点击全屏按钮
  ✓ 验证全屏模式
  状态: 通过 ✓

----------------------------------------
截图:
  - screenshots/test_01_initial.png
  - screenshots/test_02_column_selector.png
  - screenshots/test_03_fullscreen.png
========================================
```

## 注意事项

1. 确保目标服务已启动（如 localhost:5001）
2. 确保 Playwright 已安装：`pip install playwright && playwright install chromium`
3. 测试会打开浏览器窗口（非 headless 模式便于观察）
4. 截图默认保存在项目的 `screenshots/` 目录