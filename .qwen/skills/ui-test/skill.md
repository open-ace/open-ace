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