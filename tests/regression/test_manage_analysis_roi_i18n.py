#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - ROI 优化建议国际化 (i18n)

验证内容（覆盖 en/zh/ja/ko 四语言）：
1. 优化建议区段标题按当前语言本地化（无英文/原始 key 残留）
2. 切换语言后页面文本立即更新
3. 不出现未替换的占位符 {xxx} 或原始 i18n key（如 suggestionModelSwitchTitle）

相关缺陷：
- D1 priority/impact 枚举本地化
- D2 ja/ko 缺失 suggestion key
- D3 动态参数插值
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import re

from playwright.sync_api import sync_playwright

from tests.regression.test_helpers import (
    TestRunner,
    create_browser_context,
    login,
    navigate_to,
    save_screenshot,
)

MODULE_NAME = "manage_analysis_roi_i18n"

# 语言下拉菜单顺序与 Header 一致：en, zh, ja, ko
LANGUAGES = [
    ("en", 0),
    ("zh", 1),
    ("ja", 2),
    ("ko", 3),
]

# 优化建议区段标题在四种语言下的预期文本
EXPECTED_OPT_TITLE = {
    "en": "Optimization Suggestions",
    "zh": "优化建议",
    "ja": "最適化の提案",
    "ko": "최적화 제안",
}

# 不应作为可见文本出现的原始 key / 占位符（出现即说明翻译缺失或插值失败）
LEAK_PATTERNS = [
    re.compile(r"optimizationSuggestions"),
    re.compile(r"suggestionModelSwitch"),
    re.compile(r"priorityHigh|impactHigh"),
    re.compile(r"\{[a-z_]+\}"),
]


def _switch_language(page, index):
    """通过 Header 的语言下拉切换语言。"""
    page.locator("button.dropdown-toggle .bi-globe").click()
    page.wait_for_timeout(300)
    page.locator(".dropdown-menu .dropdown-item").nth(index).click()
    page.wait_for_timeout(500)


def test_roi_suggestions_localized_per_language():
    """切换四种语言，验证优化建议区段标题正确本地化、无原始 key 残留。"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/roi")

            for lang, idx in LANGUAGES:
                _switch_language(page, idx)
                # 等待重新渲染
                page.wait_for_timeout(1000)

                body_text = page.locator("body").inner_text()

                # 1. 区段标题按语言本地化
                expected = EXPECTED_OPT_TITLE[lang]
                assert (
                    expected in body_text
                ), f"[{lang}] 期望出现本地化标题 '{expected}'，但未找到。"

                # 2. 无原始 key / 未替换占位符残留
                for pattern in LEAK_PATTERNS:
                    match = pattern.search(body_text)
                    assert match is None, f"[{lang}] 检测到 i18n 残留：'{match.group(0)}'"

                save_screenshot(page, MODULE_NAME, f"{lang}_localized")
                print(f"  ✓ [{lang}] 优化建议本地化正确")

            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 ROI i18n 回归测试"""
    runner = TestRunner("Manage 模式 - Analysis - ROI 国际化")
    runner.print_header()

    tests = [
        ("四语言优化建议本地化", test_roi_suggestions_localized_per_language),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
