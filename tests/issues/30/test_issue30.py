"""
测试 Issue 30: Messages 页面工具标签显示了两次，且背景颜色不一致
"""
import asyncio
from playwright.async_api import async_playwright
import os

BASE_URL = "http://127.0.0.1:5001"
USERNAME = "admin"
PASSWORD = "admin123"
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots"

async def test_issue30():
    """测试工具标签显示问题"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()
        
        results = []
        
        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=5000)
            print("   ✓ 登录成功")
            
            # 2. 导航到 Messages 页面
            print("2. 导航到 Messages 页面...")
            await page.click('#nav-messages')
            await page.wait_for_selector('#messages-container', timeout=5000)
            await asyncio.sleep(2)  # 等待消息加载
            print("   ✓ 已进入 Messages 页面")
            
            # 3. 截图 - 初始状态
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_messages_initial.png")
            print("   ✓ 截图保存: issue30_messages_initial.png")
            
            # 4. 检查工具标签是否只显示一次
            print("3. 检查工具标签显示...")
            
            # 获取所有消息项
            message_items = await page.query_selector_all('.message-item')
            print(f"   找到 {len(message_items)} 条消息")
            
            # 检查每条消息的工具标签
            duplicate_count = 0
            for i, item in enumerate(message_items[:10]):  # 只检查前10条
                # 获取消息元数据区域
                meta_div = await item.query_selector('.message-meta')
                if meta_div:
                    # 获取所有 span 元素
                    spans = await meta_div.query_selector_all('span')
                    span_texts = []
                    for span in spans:
                        text = await span.inner_text()
                        span_texts.append(text.strip())
                    
                    # 检查是否有重复的工具名称
                    tool_names = ['openclaw', 'qwen', 'claude']
                    for tool in tool_names:
                        count = sum(1 for t in span_texts if t.lower() == tool)
                        if count > 1:
                            duplicate_count += 1
                            print(f"   ⚠ 消息 {i+1}: 工具 '{tool}' 显示了 {count} 次")
            
            if duplicate_count == 0:
                print("   ✓ 没有发现工具标签重复显示的问题")
                results.append(("工具标签不重复", "通过"))
            else:
                print(f"   ✗ 发现 {duplicate_count} 条消息有工具标签重复显示")
                results.append(("工具标签不重复", "失败"))
            
            # 5. 检查工具标签背景颜色
            print("4. 检查工具标签背景颜色...")
            
            # 检查 CSS 样式是否存在
            css_check = await page.evaluate('''() => {
                const styles = document.styleSheets;
                let hasQwenStyle = false;
                let hasClaudeStyle = false;
                let hasOpenclawStyle = false;
                
                for (let sheet of styles) {
                    try {
                        for (let rule of sheet.cssRules) {
                            if (rule.selectorText === '.message-source.qwen') hasQwenStyle = true;
                            if (rule.selectorText === '.message-source.claude') hasClaudeStyle = true;
                            if (rule.selectorText === '.message-source.openclaw') hasOpenclawStyle = true;
                        }
                    } catch (e) {}
                }
                return { hasQwenStyle, hasClaudeStyle, hasOpenclawStyle };
            }''')
            
            print(f"   CSS 样式检查:")
            print(f"   - .message-source.openclaw: {'✓ 存在' if css_check['hasOpenclawStyle'] else '✗ 不存在'}")
            print(f"   - .message-source.qwen: {'✓ 存在' if css_check['hasQwenStyle'] else '✗ 不存在'}")
            print(f"   - .message-source.claude: {'✓ 存在' if css_check['hasClaudeStyle'] else '✗ 不存在'}")
            
            if css_check['hasQwenStyle'] and css_check['hasClaudeStyle'] and css_check['hasOpenclawStyle']:
                print("   ✓ 所有工具标签样式都已定义")
                results.append(("工具标签样式完整", "通过"))
            else:
                print("   ✗ 部分工具标签样式缺失")
                results.append(("工具标签样式完整", "失败"))
            
            # 6. 检查实际显示的工具标签样式
            print("5. 检查实际显示的工具标签...")
            
            # 获取所有工具标签元素
            tool_badges = await page.query_selector_all('.message-source')
            print(f"   找到 {len(tool_badges)} 个工具标签")
            
            tool_colors = {}
            for badge in tool_badges[:20]:  # 检查前20个
                class_name = await badge.get_attribute('class')
                text = await badge.inner_text()
                text = text.strip().lower()
                
                if text in ['openclaw', 'qwen', 'claude']:
                    # 获取计算后的背景颜色
                    bg_color = await badge.evaluate('el => window.getComputedStyle(el).backgroundColor')
                    if text not in tool_colors:
                        tool_colors[text] = bg_color
            
            print("   工具标签背景颜色:")
            for tool, color in tool_colors.items():
                # 检查是否有背景颜色（不是透明）
                is_colored = color != 'rgba(0, 0, 0, 0)' and color != 'transparent'
                status = "✓ 有颜色" if is_colored else "✗ 无颜色"
                print(f"   - {tool}: {color} ({status})")
            
            # 检查是否所有工具都有颜色
            all_colored = all(
                color != 'rgba(0, 0, 0, 0)' and color != 'transparent' 
                for color in tool_colors.values()
            )
            
            if all_colored and len(tool_colors) > 0:
                print("   ✓ 所有工具标签都有背景颜色")
                results.append(("工具标签有背景色", "通过"))
            elif len(tool_colors) == 0:
                print("   ⚠ 没有找到工具标签元素")
                results.append(("工具标签有背景色", "跳过"))
            else:
                print("   ✗ 部分工具标签没有背景颜色")
                results.append(("工具标签有背景色", "失败"))
            
            # 7. 最终截图
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_messages_final.png", full_page=True)
            print("   ✓ 最终截图保存: issue30_messages_final.png")
            
        except Exception as e:
            print(f"   ✗ 测试出错: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_error.png")
            results.append(("测试执行", "失败"))
        
        finally:
            await browser.close()
        
        # 打印测试报告
        print("\n" + "="*50)
        print("Issue 30 测试报告")
        print("="*50)
        for name, status in results:
            symbol = "✓" if status == "通过" else "✗" if status == "失败" else "○"
            print(f"  {symbol} {name}: {status}")
        print("="*50)
        
        # 返回是否全部通过
        return all(status == "通过" or status == "跳过" for _, status in results)

if __name__ == "__main__":
    asyncio.run(test_issue30())
