#!/usr/bin/env python3
"""
E2E 回归：ROI 分析页 i18n 字面量泄漏 (issues #819/#820)

背景：ja/ko 此前缺失效率报告相关 i18n key，导致 ROI 页直接渲染裸 camelCase
key（如 efficiencyReport / efficiencyScore / roiDataAnomaly /
tokenAccumulationWarning / roiNegativeHint / avgTokensPerRequest）。

本用例以管理员登录，分别将 UI 切到 ja、ko，打开 /manage/analysis/roi，断言
这些裸 key 不再以字面量出现在页面可见文本中。模式参考
tests/e2e/manage/test_manage_full_audit.py。

按项目 E2E 规范：默认 headless 先跑通；HEADLESS=false 可演示。
依赖：BASE_URL (默认 http://localhost:19888)、TEST_USERNAME/TEST_PASSWORD (默认 admin/admin123)。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

# ROI 页消费的 key，绝不能以裸 camelCase 字面量泄漏进 DOM。
# 均为本 PR 之前在 ja/ko 缺失（或四语言全缺）的项。
LEAK_KEYS = [
    "efficiencyReport",
    "efficiencyScore",
    "roiDataAnomaly",
    "dataAnomalyDetected",
    "tokenAccumulationWarning",
    "roiNegativeHint",
    "roiAnalysis",
    "roiTrend",
    "costBreakdown",
    "dailyCosts",
    "totalCost",
    "totalSavings",
    "avgTokensPerRequest",
    "avgCostPerRequest",
    "overallEfficiency",
    "wastePercentage",
    "roiPercentage",
]

# 语言 → 该语言下 ROI 标题必然出现的片段（用于确认语言确实生效，软断言）。
LANG_TITLE_TOKEN = {"ja": "分析", "ko": "분석"}
LANGS = ["ja", "ko"]

failures = []


def login(page):
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_timeout(3000)
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


def check_language(page, lang):
    # 持久化语言，使下一次整页加载时 initLanguage() 读取到目标语言。
    page.evaluate("(lang) => localStorage.setItem('language', lang)", lang)
    page.goto(
        f"{BASE_URL}/manage/analysis/roi",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_timeout(6000)
    body = page.inner_text("body")

    # 软断言：语言确实生效（标题含该语言片段）。仅提示，不判失败——
    # 测试环境可能无 ROI 数据导致标题渲染差异。
    token = LANG_TITLE_TOKEN[lang]
    if token in body:
        print(f"  [ok] 语言生效检测通过（含 '{token}'）")
    else:
        print(f"  [warn] 未在页面中找到 '{token}'，可能无 ROI 数据或语言未切换")

    leaked = [k for k in LEAK_KEYS if k in body]
    if leaked:
        for k in leaked:
            failures.append(f"[{lang}] 裸 i18n key 泄漏进 DOM: {k}")
            print(f"  [FAIL][{lang}] 泄漏 key: {k}")
    else:
        print(f"  [ok] 无裸 key 泄漏（检查 {len(LEAK_KEYS)} 个 key）")


def run():
    print("=" * 70)
    print("ROI i18n 字面量泄漏 E2E 回归 (issues #819/#820)")
    print(f"BASE_URL={BASE_URL} HEADLESS={HEADLESS}")
    print("=" * 70)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        try:
            login(page)
            print("[ok] 登录成功")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] 登录失败: {e}")
            browser.close()
            return 1
        for lang in LANGS:
            print(f"\n[check] language={lang}")
            check_language(page, lang)
        browser.close()

    if failures:
        print("\n" + "=" * 70)
        print(f"E2E i18n 泄漏检查 失败（{len(failures)} 项）:")
        for f in failures:
            print(" -", f)
        return 1
    print("\n[ok] E2E i18n 泄漏检查通过：ja/ko 无裸 key 残留。")
    return 0


if __name__ == "__main__":
    sys.exit(run())
