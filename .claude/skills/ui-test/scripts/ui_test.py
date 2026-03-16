#!/usr/bin/env python3
"""
UI 功能自动化测试脚本

使用方法:
    python3 ui_test.py [options]

选项:
    --url URL           目标 URL (默认: http://localhost:5001/)
    --username USER     用户名 (默认: admin)
    --password PASS     密码 (默认: admin123)
    --headless          无头模式（不显示浏览器窗口）
    --output DIR        截图输出目录 (默认: ./screenshots)
    --test TEST         指定测试用例文件 (JSON 格式)
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# Default configuration
DEFAULT_URL = "http://localhost:5001/"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
VIEWPORT_SIZE = {'width': 1400, 'height': 900}
DEFAULT_TIMEOUT = 10000


class TestResult:
    """测试结果"""
    def __init__(self, name: str):
        self.name = name
        self.steps: List[Dict[str, Any]] = []
        self.passed = True
        self.error: Optional[str] = None
        self.screenshots: List[str] = []
    
    def add_step(self, step: str, passed: bool, message: str = ""):
        self.steps.append({
            "step": step,
            "passed": passed,
            "message": message
        })
        if not passed:
            self.passed = False
    
    def add_screenshot(self, path: str):
        self.screenshots.append(path)


class UITester:
    """UI 测试器"""
    
    def __init__(self, url: str, username: str, password: str, 
                 headless: bool = False, output_dir: str = "./screenshots"):
        self.url = url.rstrip('/')
        self.username = username
        self.password = password
        self.headless = headless
        self.output_dir = output_dir
        self.results: List[TestResult] = []
        self.browser = None
        self.page = None
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Import playwright
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright
        except ImportError:
            print("错误: Playwright 未安装")
            print("请运行: pip install playwright && playwright install chromium")
            sys.exit(1)
    
    def start(self):
        """启动浏览器"""
        self.p = self.playwright().start()
        self.browser = self.p.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_viewport_size(VIEWPORT_SIZE)
        print(f"浏览器已启动 (headless: {self.headless})")
    
    def stop(self):
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
        if hasattr(self, 'p'):
            self.p.stop()
        print("浏览器已关闭")
    
    def login(self):
        """登录系统"""
        print(f"\n1. 登录系统 ({self.url}/login)...")
        self.page.goto(f"{self.url}/login")
        self.page.fill('#username', self.username)
        self.page.fill('#password', self.password)
        self.page.click('#login-btn')
        
        try:
            self.page.wait_for_url('**/', timeout=DEFAULT_TIMEOUT)
            print("   ✓ 登录成功")
            return True
        except Exception as e:
            print(f"   ✗ 登录失败: {e}")
            return False
    
    def navigate(self, selector: str, description: str = "") -> bool:
        """导航到指定区域"""
        desc = description or f"点击 {selector}"
        try:
            elem = self.page.locator(selector).first
            if elem.is_visible(timeout=2000):
                elem.click()
                self.page.wait_for_timeout(1000)
                print(f"   ✓ {desc}")
                return True
            else:
                print(f"   ✗ {desc} - 元素不可见")
                return False
        except Exception as e:
            print(f"   ✗ {desc} - {e}")
            return False
    
    def click(self, selector: str, description: str = "") -> bool:
        """点击元素"""
        desc = description or f"点击 {selector}"
        try:
            elem = self.page.locator(selector).first
            if elem.is_visible(timeout=2000):
                elem.click()
                self.page.wait_for_timeout(500)
                print(f"   ✓ {desc}")
                return True
            else:
                print(f"   ✗ {desc} - 元素不可见")
                return False
        except Exception as e:
            print(f"   ✗ {desc} - {e}")
            return False
    
    def fill(self, selector: str, value: str, description: str = "") -> bool:
        """填写表单"""
        desc = description or f"填写 {selector}"
        try:
            self.page.fill(selector, value)
            print(f"   ✓ {desc}: {value}")
            return True
        except Exception as e:
            print(f"   ✗ {desc} - {e}")
            return False
    
    def check_visible(self, selector: str, description: str = "") -> bool:
        """检查元素是否可见"""
        desc = description or f"检查 {selector} 可见"
        try:
            elem = self.page.locator(selector).first
            if elem.is_visible(timeout=2000):
                print(f"   ✓ {desc}")
                return True
            else:
                print(f"   ✗ {desc} - 不可见")
                return False
        except Exception as e:
            print(f"   ✗ {desc} - {e}")
            return False
    
    def check_exists(self, selector: str, description: str = "") -> bool:
        """检查元素是否存在"""
        desc = description or f"检查 {selector} 存在"
        try:
            count = self.page.locator(selector).count()
            if count > 0:
                print(f"   ✓ {desc} (找到 {count} 个)")
                return True
            else:
                print(f"   ✗ {desc} - 不存在")
                return False
        except Exception as e:
            print(f"   ✗ {desc} - {e}")
            return False
    
    def check_count(self, selector: str, expected: int, description: str = "") -> bool:
        """检查元素数量"""
        desc = description or f"检查 {selector} 数量"
        try:
            count = self.page.locator(selector).count()
            if count == expected:
                print(f"   ✓ {desc} (预期: {expected}, 实际: {count})")
                return True
            else:
                print(f"   ✗ {desc} - 数量不匹配 (预期: {expected}, 实际: {count})")
                return False
        except Exception as e:
            print(f"   ✗ {desc} - {e}")
            return False
    
    def wait(self, seconds: float):
        """等待"""
        self.page.wait_for_timeout(int(seconds * 1000))
        print(f"   等待 {seconds} 秒")
        return True
    
    def screenshot(self, filename: str = None) -> str:
        """截图"""
        if not filename:
            filename = f"test_{self.timestamp}_{len(self.results)}.png"
        
        filepath = os.path.join(self.output_dir, filename)
        os.makedirs(self.output_dir, exist_ok=True)
        self.page.screenshot(path=filepath)
        print(f"   📷 截图: {filename}")
        return filepath
    
    def run_test_case(self, test_case: Dict[str, Any]) -> TestResult:
        """运行单个测试用例"""
        result = TestResult(test_case.get("name", "未命名测试"))
        print(f"\n{'='*50}")
        print(f"测试用例: {result.name}")
        print(f"{'='*50}")
        
        for step in test_case.get("steps", []):
            action = step.get("action")
            target = step.get("target", "")
            value = step.get("value", "")
            expected = step.get("expected")
            description = step.get("description", "")
            
            passed = False
            message = ""
            
            if action == "navigate":
                passed = self.navigate(target, description)
            elif action == "click":
                passed = self.click(target, description)
            elif action == "fill":
                passed = self.fill(target, value, description)
            elif action == "check_visible":
                passed = self.check_visible(target, description)
            elif action == "check_exists":
                passed = self.check_exists(target, description)
            elif action == "check_count":
                passed = self.check_count(target, expected, description)
            elif action == "wait":
                passed = self.wait(value or 1)
            elif action == "screenshot":
                path = self.screenshot(value)
                result.add_screenshot(path)
                passed = True
            else:
                message = f"未知操作: {action}"
                print(f"   ✗ {message}")
            
            result.add_step(description or f"{action}: {target}", passed, message)
            
            # 如果步骤失败，可以选择继续或停止
            if not passed and step.get("critical", False):
                print(f"   ⚠ 关键步骤失败，停止测试")
                break
        
        status = "✓ 通过" if result.passed else "✗ 失败"
        print(f"\n状态: {status}")
        
        return result
    
    def run_tests(self, test_cases: List[Dict[str, Any]]):
        """运行所有测试用例"""
        print(f"\n{'#'*60}")
        print(f"UI 功能自动化测试")
        print(f"{'#'*60}")
        print(f"目标: {self.url}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"测试用例数: {len(test_cases)}")
        
        self.start()
        
        try:
            # 登录
            if not self.login():
                print("\n登录失败，无法继续测试")
                return
            
            # 运行测试用例
            for test_case in test_cases:
                result = self.run_test_case(test_case)
                self.results.append(result)
        
        finally:
            self.stop()
        
        # 生成报告
        self.generate_report()
    
    def generate_report(self):
        """生成测试报告"""
        print(f"\n{'='*60}")
        print("测试报告")
        print(f"{'='*60}")
        
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        
        print(f"测试用例: {total} 个")
        print(f"通过: {passed} 个")
        print(f"失败: {failed} 个")
        print()
        
        for i, result in enumerate(self.results, 1):
            status = "✓" if result.passed else "✗"
            print(f"{i}. {result.name}: {status}")
            if result.screenshots:
                for s in result.screenshots:
                    print(f"   📷 {s}")
        
        # 保存报告到文件
        report_path = os.path.join(self.output_dir, f"test_report_{self.timestamp}.json")
        report_data = {
            "timestamp": self.timestamp,
            "url": self.url,
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed
            },
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "steps": r.steps,
                    "screenshots": r.screenshots
                }
                for r in self.results
            ]
        }
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n报告已保存: {report_path}")


def main():
    parser = argparse.ArgumentParser(description='UI 功能自动化测试')
    parser.add_argument('--url', default=DEFAULT_URL, help='目标 URL')
    parser.add_argument('--username', default=DEFAULT_USERNAME, help='用户名')
    parser.add_argument('--password', default=DEFAULT_PASSWORD, help='密码')
    parser.add_argument('--headless', action='store_true', help='无头模式')
    parser.add_argument('--output', default='./screenshots', help='输出目录')
    parser.add_argument('--test', help='测试用例 JSON 文件')
    
    args = parser.parse_args()
    
    # 加载测试用例
    if args.test:
        with open(args.test, 'r', encoding='utf-8') as f:
            test_cases = json.load(f)
    else:
        # 默认测试用例（示例）
        test_cases = [
            {
                "name": "示例测试 - 检查导航",
                "steps": [
                    {"action": "navigate", "target": "#nav-analysis", "description": "导航到 Analysis 页面"},
                    {"action": "check_visible", "target": "#analysis-section", "description": "检查 Analysis 区域可见"},
                    {"action": "screenshot", "value": "test_analysis.png"}
                ]
            }
        ]
    
    # 运行测试
    tester = UITester(
        url=args.url,
        username=args.username,
        password=args.password,
        headless=args.headless,
        output_dir=args.output
    )
    tester.run_tests(test_cases)


if __name__ == '__main__':
    main()