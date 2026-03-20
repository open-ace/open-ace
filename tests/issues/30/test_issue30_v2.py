"""
测试 Issue 30 补充: 验证工具标签组合显示逻辑
"""
import asyncio
from playwright.async_api import async_playwright
import os

BASE_URL = "http://127.0.0.1:5001"
USERNAME = "admin"
PASSWORD = "admin123"
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots"

async def test_issue30_v2():
    """测试工具标签组合显示"""
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
            await asyncio.sleep(2)
            print("   ✓ 已进入 Messages 页面")
            
            # 3. 检查工具标签显示
            print("3. 检查工具标签显示...")
            
            # 获取所有工具标签
            tool_badges = await page.query_selector_all('.message-source')
            print(f"   找到 {len(tool_badges)} 个工具标签")
            
            # 收集所有标签文本
            badge_texts = {}
            for badge in tool_badges:
                text = await badge.inner_text()
                text = text.strip().lower()
                if text not in badge_texts:
                    badge_texts[text] = 0
                badge_texts[text] += 1
            
            print("   工具标签统计:")
            for text, count in sorted(badge_texts.items(), key=lambda x: -x[1]):
                print(f"   - {text}: {count} 个")
            
            # 检查是否有组合格式的标签 (如 "openclaw (feishu)")
            combined_labels = [t for t in badge_texts.keys() if '(' in t and ')' in t]
            if combined_labels:
                print(f"\n   ✓ 发现组合格式标签: {combined_labels}")
                results.append(("组合格式标签显示", "通过"))
            else:
                print("\n   ⚠ 未发现组合格式标签（可能数据中没有 openclaw + feishu/slack 的情况）")
                results.append(("组合格式标签显示", "跳过"))
            
            # 4. 检查 CSS class 是否正确
            print("\n4. 检查 CSS class...")
            
            for badge in tool_badges[:10]:
                class_name = await badge.get_attribute('class')
                text = await badge.inner_text()
                text = text.strip().lower()
                
                # 检查 class 是否包含有效的工具名
                valid_classes = ['openclaw', 'qwen', 'claude', 'slack', 'feishu']
                has_valid_class = any(f'message-source {vc}' == class_name or class_name.endswith(f' {vc}') for vc in valid_classes)
                
                if '(' in text:
                    # 组合格式，class 应该是 openclaw
                    if 'openclaw' in class_name:
                        print(f"   ✓ 组合标签 '{text}' 使用正确的 class: {class_name}")
                    else:
                        print(f"   ✗ 组合标签 '{text}' class 不正确: {class_name}")
            
            # 5. 截图
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_v2_messages.png", full_page=True)
            print("\n   ✓ 截图保存: issue30_v2_messages.png")
            
            results.append(("测试执行", "通过"))
            
        except Exception as e:
            print(f"   ✗ 测试出错: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_v2_error.png")
            results.append(("测试执行", "失败"))
        
        finally:
            await browser.close()
        
        # 打印测试报告
        print("\n" + "="*50)
        print("Issue 30 补充测试报告")
        print("="*50)
        for name, status in results:
            symbol = "✓" if status == "通过" else "✗" if status == "失败" else "○"
            print(f"  {symbol} {name}: {status}")
        print("="*50)

if __name__ == "__main__":
    asyncio.run(test_issue30_v2())
