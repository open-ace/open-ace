# UI 测试指南

本文档介绍如何使用 Playwright 进行 UI 自动化测试，包含常用模式和最佳实践。

## 目录

1. [环境配置](#环境配置)
2. [基础模板](#基础模板)
3. [常用操作](#常用操作)
4. [调试技巧](#调试技巧)
5. [测试报告](#测试报告)
6. [完整示例](#完整示例)

---

## 环境配置

### 安装依赖

```bash
pip install playwright
playwright install chromium
```

### 环境变量

```bash
export BASE_URL="http://localhost:5001/"
export USERNAME="admin"
export PASSWORD="admin123"
```

---

## 基础模板

```python
#!/usr/bin/env python3
"""
测试 Issue #XX: <问题描述>
"""

import asyncio
from playwright.async_api import async_playwright
import os
from datetime import datetime

# 配置
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001/')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
SCREENSHOT_DIR = 'screenshots'


async def test_issue_xx():
    """测试 <功能描述>"""
    
    # 确保截图目录存在
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    
    # 生成时间戳（用于截图文件名）
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    async with async_playwright() as p:
        # 启动浏览器（headless=True 不显示浏览器窗口）
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()
        
        # 测试结果列表
        results = []
        
        try:
            # ... 测试步骤 ...
            pass
            
        except Exception as e:
            print(f"   ✗ 测试出错: {str(e)}")
            results.append(("测试执行", False, str(e)))
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue_xx_error_{timestamp}.png', full_page=True)
            
        finally:
            await browser.close()
        
        # 打印测试报告
        print_test_report(results)
        
        return all(r[1] for r in results)


def print_test_report(results):
    """打印测试报告"""
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    print(f"测试用例: {len(results)} 个")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    print("-" * 60)
    
    for name, success, error in results:
        status = "✓ 通过" if success else f"✗ 失败 ({error})"
        print(f"  {name}: {status}")
    
    print("=" * 60)


if __name__ == '__main__':
    success = asyncio.run(test_issue_xx())
    exit(0 if success else 1)
```

---

## 常用操作

### 1. 登录系统

```python
# 导航到登录页面
await page.goto(f'{BASE_URL}login')
await page.wait_for_load_state('networkidle')

# 填写表单
await page.fill('#username', USERNAME)
await page.fill('#password', PASSWORD)

# 点击提交
await page.click('button[type="submit"]')
await page.wait_for_load_state('networkidle')
await asyncio.sleep(2)  # 等待页面稳定

# 记录结果
print("   ✓ 登录成功")
results.append(("登录", True, ""))
```

### 2. 页面导航

```python
# 方式1: 使用文本选择器
await page.click('text=Analysis')

# 方式2: 使用 ID 选择器
await page.click('#nav-analysis')

# 方式3: 使用 JavaScript
await page.evaluate('''() => {
    const tab = document.getElementById('conversation-history-tab');
    if (tab) tab.click();
}''')

# 等待页面加载
await page.wait_for_load_state('networkidle')
await asyncio.sleep(2)
```

### 3. 等待元素

```python
# 等待元素出现（带超时）
await page.wait_for_selector('#my-element', timeout=10000)

# 等待元素可见
await page.wait_for_selector('#my-element', state='visible', timeout=10000)

# 等待元素隐藏
await page.wait_for_selector('#loading', state='hidden', timeout=10000)

# 等待 Tabulator 表格行
await page.wait_for_selector('#my-table .tabulator-row', timeout=15000)
```

### 4. 检查元素状态

```python
# 检查元素是否可见
is_visible = await page.is_visible('#my-element')

# 检查元素是否存在
element = await page.query_selector('#my-element')
exists = element is not None

# 检查元素数量
count = await page.evaluate('''() => {
    return document.querySelectorAll('.my-class').length;
}''')

# 获取元素文本
text = await page.text_content('#my-element')

# 获取元素 HTML
html = await page.inner_html('#my-element')
```

### 5. 表单操作

```python
# 填写输入框
await page.fill('#input-id', 'value')

# 选择下拉框
await page.select_option('#select-id', 'option-value')

# 勾选复选框
await page.check('#checkbox-id')

# 取消勾选
await page.uncheck('#checkbox-id')

# 使用 JavaScript 设置值
await page.evaluate('''() => {
    document.getElementById('date-input').value = '2026-03-13';
    onDateChange();  // 触发回调
}''')
```

### 6. 点击按钮

```python
# 方式1: 直接点击
await page.click('#button-id')

# 方式2: 使用 JavaScript 点击（适用于动态生成的元素）
clicked = await page.evaluate('''() => {
    const buttons = document.querySelectorAll('button[onclick*="myFunction"]');
    if (buttons.length > 0) {
        buttons[0].click();
        return true;
    }
    return false;
}''')

# 方式3: 点击包含特定文本的按钮
await page.click('button:has-text("Submit")')
```

### 7. 处理 Modal

```python
# 点击按钮打开 Modal
await page.click('#open-modal-btn')
await asyncio.sleep(1)

# 检查 Modal 是否打开
modal_visible = await page.is_visible('#myModal.show')

# 检查 Modal 内容
modal_text = await page.text_content('#myModal .modal-body')

# 关闭 Modal
await page.click('#myModal .btn-close')
await page.wait_for_selector('#myModal.show', state='hidden', timeout=3000)
```

### 8. 截图

```python
# 全页截图
await page.screenshot(path=f'{SCREENSHOT_DIR}/screenshot_{timestamp}.png', full_page=True)

# 元素截图
element = await page.query_selector('#my-element')
await element.screenshot(path=f'{SCREENSHOT_DIR}/element_{timestamp}.png')

# 调试时截图
await page.screenshot(path=f'{SCREENSHOT_DIR}/debug_step1_{timestamp}.png')
```

---

## 调试技巧

### 1. 分步截图

```python
# 在关键步骤前后截图，便于调试
await page.screenshot(path=f'{SCREENSHOT_DIR}/before_click_{timestamp}.png')
await page.click('#my-button')
await page.screenshot(path=f'{SCREENSHOT_DIR}/after_click_{timestamp}.png')
```

### 2. 打印元素状态

```python
# 检查元素是否存在
element = await page.query_selector('#my-element')
print(f"   元素存在: {element is not None}")

# 检查元素是否可见
is_visible = await page.is_visible('#my-element')
print(f"   元素可见: {is_visible}")

# 打印元素内容
html = await page.evaluate('''() => {
    const el = document.getElementById('my-element');
    return el ? el.innerHTML : 'not found';
}''')
print(f"   元素内容: {html[:200]}...")
```

### 3. 使用 try-except 处理超时

```python
try:
    await page.wait_for_selector('#my-element', timeout=10000)
    print("   ✓ 元素已加载")
    results.append(("元素加载", True, ""))
except:
    # 超时后检查元素状态
    is_visible = await page.is_visible('#my-element')
    if is_visible:
        print("   ✓ 元素存在（可能样式不同）")
        results.append(("元素加载", True, "元素存在"))
    else:
        print("   ✗ 元素未找到")
        results.append(("元素加载", False, "元素未找到"))
```

### 4. 非无头模式调试

```python
# 设置 headless=False 可以看到浏览器操作过程
browser = await p.chromium.launch(headless=False)

# 添加暂停，便于观察
await asyncio.sleep(5)
```

---

## 测试报告

### 标准报告格式

```python
def print_test_report(results):
    """打印测试报告"""
    print("\n" + "=" * 60)
    print("Issue #XX 测试报告")
    print("=" * 60)
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    print(f"测试用例: {len(results)} 个")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    print("-" * 60)
    
    for name, success, error in results:
        status = "✓ 通过" if success else f"✗ 失败 ({error})"
        print(f"  {name}: {status}")
    
    print("=" * 60)
```

### 结果格式

```python
# 结果元组: (测试名称, 是否通过, 错误信息)
results.append(("登录", True, ""))
results.append(("导航到页面", True, ""))
results.append(("元素检查", False, "元素未找到"))
```

---

## 完整示例

### 示例1: 测试表格列是否存在

```python
async def test_table_columns():
    """测试表格列"""
    
    # ... 登录和导航代码 ...
    
    # 获取表格列头
    headers = await page.query_selector_all('#my-table th')
    header_texts = []
    for header in headers:
        text = await header.text_content()
        header_texts.append(text.strip() if text else '')
    
    print(f"   表格列头: {header_texts}")
    
    # 验证特定列是否存在
    has_column = any('column name' in h.lower() for h in header_texts)
    
    if has_column:
        print("   ✗ 失败: 表格中包含不该存在的列")
        results.append(("验证列不存在", False, "列仍然存在"))
    else:
        print("   ✓ 成功: 列已移除")
        results.append(("验证列不存在", True, ""))
```

### 示例2: 测试 Modal 功能

```python
async def test_modal():
    """测试 Modal 功能"""
    
    # ... 前置步骤 ...
    
    # 点击按钮打开 Modal
    button_found = await page.evaluate('''() => {
        const btn = document.querySelector('#open-modal-btn');
        if (btn) {
            btn.click();
            return true;
        }
        return false;
    }''')
    
    if not button_found:
        results.append(("打开 Modal", False, "按钮未找到"))
        return False
    
    await asyncio.sleep(1)
    
    # 验证 Modal 已打开
    modal_visible = await page.is_visible('#myModal.show')
    if modal_visible:
        print("   ✓ Modal 已打开")
        results.append(("Modal 打开", True, ""))
    else:
        print("   ✗ Modal 未打开")
        results.append(("Modal 打开", False, "Modal 未显示"))
        return False
    
    # 截图
    await page.screenshot(path=f'{SCREENSHOT_DIR}/modal_opened_{timestamp}.png')
    
    # 验证 Modal 内容
    content = await page.text_content('#myModal .modal-body')
    print(f"   Modal 内容: {content[:100]}...")
    
    return True
```

### 示例3: 测试 Tabulator 表格

```python
async def test_tabulator_table():
    """测试 Tabulator 表格"""
    
    # ... 前置步骤 ...
    
    # 等待表格加载
    try:
        await page.wait_for_selector('#my-table .tabulator-row', timeout=15000)
        print("   ✓ 表格已加载")
    except:
        # 检查表格容器是否存在
        table_exists = await page.is_visible('#my-table')
        if table_exists:
            print("   表格容器存在，但无数据")
            # 可以继续测试其他功能
        else:
            print("   ✗ 表格未找到")
            return False
    
    # 获取表格行数
    row_count = await page.evaluate('''() => {
        return document.querySelectorAll('#my-table .tabulator-row').length;
    }''')
    print(f"   表格行数: {row_count}")
    
    # 点击表格中的按钮
    clicked = await page.evaluate('''() => {
        const btn = document.querySelector('#my-table button.action-btn');
        if (btn) {
            btn.click();
            return true;
        }
        return false;
    }''')
```

---

## 最佳实践

1. **使用环境变量** - 避免硬编码 URL、用户名、密码
2. **添加截图** - 关键步骤都截图，便于调试和报告
3. **合理等待** - 使用 `wait_for_selector` 而非固定 `sleep`
4. **错误处理** - 使用 try-except 捕获异常，记录失败原因
5. **结果记录** - 每个测试步骤都记录结果，便于生成报告
6. **命名规范** - 截图文件名包含 issue 编号和时间戳
7. **独立测试** - 每个测试脚本应该能独立运行

---

## 常见问题

### Q: 元素选择器找不到元素？

A: 尝试以下方法：
1. 使用 `page.evaluate()` 在浏览器上下文中查找
2. 增加等待时间 `await asyncio.sleep(2)`
3. 检查元素是否在 iframe 中
4. 使用更宽松的选择器

### Q: Tabulator 表格加载慢？

A: 使用更长的超时时间：
```python
await page.wait_for_selector('#my-table .tabulator-row', timeout=30000)
```

### Q: Modal 动画导致检测失败？

A: 等待动画完成后再检查：
```python
await page.click('#open-modal')
await asyncio.sleep(1)  # 等待动画
modal_visible = await page.is_visible('#myModal.show')
```

---

## 参考资源

- [Playwright 官方文档](https://playwright.dev/python/)
- [Playwright API 参考](https://playwright.dev/python/docs/api/class-page)
- [Tabulator 文档](http://tabulator.info/)