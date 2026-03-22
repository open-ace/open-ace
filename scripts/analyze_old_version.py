#!/usr/bin/env python3
"""
分析旧版本页面元素
访问 http://127.0.0.1:5002/ 并截图分析 Dashboard、Messages、Analysis、Conversation History 页面
"""

import asyncio
from playwright.async_api import async_playwright
import os
import json
from datetime import datetime

OLD_VERSION_URL = "http://127.0.0.1:5002/"
USERNAME = "admin"
PASSWORD = "admin123"
OUTPUT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/old_version_analysis"

async def analyze_pages():
    """分析旧版本页面"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()
        
        # 存储页面元素信息
        page_elements = {}
        
        # 1. 访问登录页面
        print("1. 访问登录页面...")
        await page.goto(OLD_VERSION_URL, wait_until='networkidle', timeout=30000)
        current_url = page.url
        print(f"   当前URL: {current_url}")
        await page.screenshot(path=f"{OUTPUT_DIR}/01_login.png", full_page=True)
        
        # 登录
        print("   正在登录...")
        username_input = await page.query_selector('input[name="username"]')
        password_input = await page.query_selector('input[name="password"]')
        
        if username_input and password_input:
            await username_input.fill(USERNAME)
            await password_input.fill(PASSWORD)
            
            # 点击登录按钮
            submit_btn = await page.query_selector('button[type="submit"]')
            if submit_btn:
                await submit_btn.click()
                print("   点击登录按钮")
                
                # 等待导航完成
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                except:
                    pass
                
                # 额外等待
                await page.wait_for_timeout(3000)
                current_url = page.url
                print(f"   登录后URL: {current_url}")
        
        await page.screenshot(path=f"{OUTPUT_DIR}/02_after_login.png", full_page=True)
        
        # 保存登录后的页面内容
        after_login_html = await page.content()
        with open(f"{OUTPUT_DIR}/02_after_login.html", 'w', encoding='utf-8') as f:
            f.write(after_login_html)
        
        # 获取导航菜单
        nav_items = await page.query_selector_all('nav a, nav button, .sidebar a, .nav-link, [class*="sidebar"] a')
        print(f"\n   找到 {len(nav_items)} 个导航项:")
        nav_links = []
        for item in nav_items:
            try:
                text = await item.inner_text()
                href = await item.evaluate('el => el.href')
                onclick = await item.evaluate('el => el.getAttribute("onclick")')
                data_page = await item.evaluate('el => el.getAttribute("data-page")')
                print(f"   - {text.strip()}: href={href}, data-page={data_page}")
                nav_links.append({"text": text.strip(), "href": href, "data_page": data_page, "onclick": onclick})
            except:
                pass
        page_elements["Navigation"] = nav_links
        
        # 2. 分析 Dashboard 页面 (使用 hash 路由)
        print("\n2. 分析 Dashboard 页面...")
        # 尝试点击 Dashboard 链接
        try:
            dashboard_link = await page.query_selector('a[data-page="dashboard"], a:has-text("Dashboard")')
            if dashboard_link:
                await dashboard_link.click()
                await page.wait_for_timeout(3000)
            else:
                # 直接访问 hash 路由
                await page.evaluate('window.location.hash = "#dashboard"')
                await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"   导航错误: {e}")
            await page.goto(f"{OLD_VERSION_URL}#dashboard", wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(3000)
        
        print(f"   当前URL: {page.url}")
        await page.screenshot(path=f"{OUTPUT_DIR}/03_dashboard.png", full_page=True)
        
        dashboard_html = await page.content()
        with open(f"{OUTPUT_DIR}/03_dashboard.html", 'w', encoding='utf-8') as f:
            f.write(dashboard_html)
        
        dashboard_elements = await extract_page_elements(page, "Dashboard")
        page_elements["Dashboard"] = dashboard_elements
        
        # 3. 分析 Messages 页面
        print("\n3. 分析 Messages 页面...")
        try:
            messages_link = await page.query_selector('a[data-page="messages"], a:has-text("Messages")')
            if messages_link:
                await messages_link.click()
                await page.wait_for_timeout(3000)
            else:
                await page.evaluate('window.location.hash = "#messages"')
                await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"   导航错误: {e}")
        
        print(f"   当前URL: {page.url}")
        await page.screenshot(path=f"{OUTPUT_DIR}/04_messages.png", full_page=True)
        
        messages_html = await page.content()
        with open(f"{OUTPUT_DIR}/04_messages.html", 'w', encoding='utf-8') as f:
            f.write(messages_html)
        
        messages_elements = await extract_page_elements(page, "Messages")
        page_elements["Messages"] = messages_elements
        
        # 4. 分析 Analysis 页面
        print("\n4. 分析 Analysis 页面...")
        try:
            analysis_link = await page.query_selector('a[data-page="analysis"], a:has-text("Analysis")')
            if analysis_link:
                await analysis_link.click()
                await page.wait_for_timeout(3000)
            else:
                await page.evaluate('window.location.hash = "#analysis"')
                await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"   导航错误: {e}")
        
        print(f"   当前URL: {page.url}")
        await page.screenshot(path=f"{OUTPUT_DIR}/05_analysis.png", full_page=True)
        
        analysis_html = await page.content()
        with open(f"{OUTPUT_DIR}/05_analysis.html", 'w', encoding='utf-8') as f:
            f.write(analysis_html)
        
        analysis_elements = await extract_page_elements(page, "Analysis")
        page_elements["Analysis"] = analysis_elements
        
        # 5. 分析 Conversation History 页面
        print("\n5. 分析 Conversation History 页面...")
        try:
            conversation_link = await page.query_selector('a[data-page="conversation-history"], a:has-text("Conversation"), a:has-text("对话")')
            if conversation_link:
                await conversation_link.click()
                await page.wait_for_timeout(3000)
            else:
                await page.evaluate('window.location.hash = "#conversation-history"')
                await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"   导航错误: {e}")
        
        print(f"   当前URL: {page.url}")
        await page.screenshot(path=f"{OUTPUT_DIR}/06_conversation_history.png", full_page=True)
        
        conversation_html = await page.content()
        with open(f"{OUTPUT_DIR}/06_conversation_history.html", 'w', encoding='utf-8') as f:
            f.write(conversation_html)
        
        conversation_elements = await extract_page_elements(page, "Conversation History")
        page_elements["Conversation History"] = conversation_elements
        
        await browser.close()
        
        # 保存元素信息到 JSON 文件
        with open(f"{OUTPUT_DIR}/page_elements.json", 'w', encoding='utf-8') as f:
            json.dump(page_elements, f, ensure_ascii=False, indent=2)
        
        print(f"\n分析完成！截图和元素信息已保存到: {OUTPUT_DIR}")
        return page_elements

async def extract_page_elements(page, page_name):
    """提取页面元素"""
    print(f"   提取 {page_name} 页面元素...")
    elements = {
        "charts": [],
        "tables": [],
        "search_inputs": [],
        "dropdowns": [],
        "checkboxes": [],
        "buttons": [],
        "other_inputs": [],
        "cards": [],
        "tabs": [],
        "filters": [],
        "date_pickers": [],
        "pagination": [],
        "modals": [],
        "stats_cards": []
    }
    
    try:
        # 提取图表 (canvas, svg, 或带有图表类名的元素)
        charts = await page.query_selector_all('canvas, [class*="chart"], [class*="Chart"], [class*="graph"], [class*="Graph"], [class*="recharts"], [class*="echarts"]')
        for chart in charts:
            try:
                tag = await chart.evaluate('el => el.tagName')
                class_name = await chart.evaluate('el => el.className')
                id_attr = await chart.evaluate('el => el.id')
                width = await chart.evaluate('el => el.offsetWidth')
                height = await chart.evaluate('el => el.offsetHeight')
                elements["charts"].append({
                    "tag": tag,
                    "class": class_name,
                    "id": id_attr,
                    "size": f"{width}x{height}"
                })
                print(f"   - 图表: {tag}, size={width}x{height}")
            except Exception as e:
                print(f"   - 图表提取错误: {e}")
        
        # 提取表格
        tables = await page.query_selector_all('table, [class*="table"]:not([class*="tablet"]), [class*="Table"]')
        for table in tables:
            try:
                class_name = await table.evaluate('el => el.className')
                id_attr = await table.evaluate('el => el.id')
                # 获取表头
                headers = await table.query_selector_all('th')
                header_texts = []
                for h in headers:
                    text = await h.inner_text()
                    header_texts.append(text.strip())
                # 获取行数
                rows = await table.query_selector_all('tbody tr')
                row_count = len(rows)
                elements["tables"].append({
                    "class": class_name,
                    "id": id_attr,
                    "headers": header_texts,
                    "row_count": row_count
                })
                print(f"   - 表格: {len(header_texts)} 列, {row_count} 行, headers={header_texts[:5]}")
            except Exception as e:
                print(f"   - 表格提取错误: {e}")
        
        # 提取搜索框
        search_inputs = await page.query_selector_all('input[type="search"], input[placeholder*="搜索"], input[placeholder*="Search"], input[placeholder*="查找"], input[placeholder*="filter"], input[placeholder*="Filter"], input[placeholder*="关键词"]')
        for inp in search_inputs:
            try:
                placeholder = await inp.evaluate('el => el.placeholder')
                name = await inp.evaluate('el => el.name')
                class_name = await inp.evaluate('el => el.className')
                elements["search_inputs"].append({
                    "placeholder": placeholder,
                    "name": name,
                    "class": class_name
                })
                print(f"   - 搜索框: placeholder={placeholder}")
            except:
                pass
        
        # 提取下拉列表 (select 和自定义下拉)
        dropdowns = await page.query_selector_all('select, [class*="select"]:not([class*="selected"]), [class*="Select"], [class*="dropdown"], [class*="Dropdown"]')
        for dropdown in dropdowns:
            try:
                tag = await dropdown.evaluate('el => el.tagName')
                class_name = await dropdown.evaluate('el => el.className')
                # 获取选项
                options = await dropdown.query_selector_all('option')
                option_texts = []
                for opt in options:
                    text = await opt.inner_text()
                    option_texts.append(text.strip())
                # 获取当前选中值
                selected = await dropdown.evaluate('el => el.value')
                elements["dropdowns"].append({
                    "tag": tag,
                    "class": class_name,
                    "options": option_texts[:10],  # 只取前10个选项
                    "selected": selected
                })
                print(f"   - 下拉列表: {tag}, {len(option_texts)} 个选项, 当前={selected}")
            except:
                pass
        
        # 提取勾选框
        checkboxes = await page.query_selector_all('input[type="checkbox"], [class*="checkbox"], [class*="Checkbox"]')
        for checkbox in checkboxes:
            try:
                label = await checkbox.evaluate('el => el.labels ? Array.from(el.labels).map(l => l.textContent).join(", ") : ""')
                name = await checkbox.evaluate('el => el.name')
                class_name = await checkbox.evaluate('el => el.className')
                checked = await checkbox.evaluate('el => el.checked')
                elements["checkboxes"].append({
                    "label": label.strip() if label else "",
                    "name": name,
                    "class": class_name,
                    "checked": checked
                })
                print(f"   - 勾选框: label={label.strip() if label else 'N/A'}, checked={checked}")
            except:
                pass
        
        # 提取按钮
        buttons = await page.query_selector_all('button, input[type="button"], input[type="submit"], [role="button"], .btn')
        for btn in buttons:
            try:
                text = await btn.inner_text()
                class_name = await btn.evaluate('el => el.className')
                btn_type = await btn.evaluate('el => el.type')
                elements["buttons"].append({
                    "text": text.strip()[:50],
                    "class": class_name,
                    "type": btn_type
                })
            except:
                pass
        print(f"   - 按钮: {len(elements['buttons'])} 个")
        
        # 提取其他输入框
        other_inputs = await page.query_selector_all('input:not([type="checkbox"]):not([type="button"]):not([type="submit"]):not([type="search"]):not([type="hidden"])')
        for inp in other_inputs:
            try:
                inp_type = await inp.evaluate('el => el.type')
                placeholder = await inp.evaluate('el => el.placeholder')
                name = await inp.evaluate('el => el.name')
                class_name = await inp.evaluate('el => el.className')
                elements["other_inputs"].append({
                    "type": inp_type,
                    "placeholder": placeholder,
                    "name": name,
                    "class": class_name
                })
                print(f"   - 输入框: type={inp_type}, placeholder={placeholder}, name={name}")
            except:
                pass
        
        # 提取卡片
        cards = await page.query_selector_all('[class*="card"], [class*="Card"]')
        for card in cards:
            try:
                class_name = await card.evaluate('el => el.className')
                text = await card.inner_text()
                elements["cards"].append({
                    "class": class_name,
                    "text_preview": text[:100].replace('\n', ' ')
                })
            except:
                pass
        print(f"   - 卡片: {len(elements['cards'])} 个")
        
        # 提取统计卡片 (带有数字的卡片)
        stats_cards = await page.query_selector_all('[class*="stat"], [class*="Stat"], [class*="metric"], [class*="Metric"], [class*="counter"], [class*="Counter"]')
        for sc in stats_cards:
            try:
                class_name = await sc.evaluate('el => el.className')
                text = await sc.inner_text()
                elements["stats_cards"].append({
                    "class": class_name,
                    "text_preview": text[:100].replace('\n', ' ')
                })
            except:
                pass
        print(f"   - 统计卡片: {len(elements['stats_cards'])} 个")
        
        # 提取标签页
        tabs = await page.query_selector_all('[class*="tab"]:not([class*="table"]), [class*="Tab"], [role="tab"], .nav-tabs li, .nav-pills li')
        for tab in tabs:
            try:
                text = await tab.inner_text()
                class_name = await tab.evaluate('el => el.className')
                elements["tabs"].append({
                    "text": text.strip()[:50],
                    "class": class_name
                })
            except:
                pass
        print(f"   - 标签页: {len(elements['tabs'])} 个")
        
        # 提取过滤器相关元素
        filters = await page.query_selector_all('[class*="filter"], [class*="Filter"]')
        for f in filters:
            try:
                class_name = await f.evaluate('el => el.className')
                text = await f.inner_text()
                elements["filters"].append({
                    "class": class_name,
                    "text_preview": text[:100].replace('\n', ' ')
                })
            except:
                pass
        print(f"   - 过滤器: {len(elements['filters'])} 个")
        
        # 提取日期选择器
        date_pickers = await page.query_selector_all('[class*="date"], [class*="Date"], [class*="calendar"], [class*="Calendar"], [class*="picker"], input[type="date"]')
        for dp in date_pickers:
            try:
                class_name = await dp.evaluate('el => el.className')
                tag = await dp.evaluate('el => el.tagName')
                elements["date_pickers"].append({
                    "tag": tag,
                    "class": class_name
                })
            except:
                pass
        print(f"   - 日期选择器: {len(elements['date_pickers'])} 个")
        
        # 提取分页
        pagination = await page.query_selector_all('[class*="pagination"], [class*="Pagination"], [class*="pager"], [class*="Pager"]')
        for pg in pagination:
            try:
                class_name = await pg.evaluate('el => el.className')
                text = await pg.inner_text()
                elements["pagination"].append({
                    "class": class_name,
                    "text": text.strip()[:100]
                })
            except:
                pass
        print(f"   - 分页: {len(elements['pagination'])} 个")
    except Exception as e:
        print(f"   提取元素错误: {e}")
    
    return elements

if __name__ == "__main__":
    asyncio.run(analyze_pages())