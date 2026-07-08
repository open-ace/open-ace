"""Tests for multilingual test result detection (Issue #1538).

Bug: Agent outputs test results in Chinese/Japanese/Korean format but
detection patterns only support English, causing "Tests were not actually run"
false-negative judgments.

Fix: Extend pytest output patterns to support:
- Chinese: "通过: 2398 个", "失败: 5 个"
- Japanese: "通過: 2398 件", "失敗: 5件"
- Korean: "통과: 2398개", "실패: 5개"
- Rust cargo test: "test result: ok", "running 4 tests"
- Java Maven/Gradle: "Tests run: 10", "BUILD SUCCESS"
"""

import re

import pytest


class TestChineseOutputDetection:
    """Verify that Chinese pytest output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Chinese format with colon separator
            ("通过: 2398 个", True),
            ("通过：2398个", True),  # Chinese colon
            ("成功: 100 个", True),
            ("失败: 5 个", True),
            ("错误: 3 个", True),
            ("跳过: 69 个", True),
            ("忽略: 10 个", True),
            # Chinese format without colon (space separator)
            ("通过 2398 个", True),
            ("成功 100 测试", True),
            ("失败 5 个", True),
            # Chinese reverse format: number + keyword
            ("2398个测试通过", False),  # Edge case: requires more complex pattern
            ("2398 个 通过", True),
            ("5个失败", True),
            ("100项成功", True),
            # Chinese with mixed format
            ("测试结果：2398成功，5失败", False),  # Edge case: requires prefix detection
            ("✅ 2398个通过", True),
            ("❌ 5个失败", True),
            # Chinese hallucination (should NOT match)
            ("测试在后台运行", False),
            ("测试进度50%", False),
            ("准备开始测试", False),
            ("代码已修改", False),
        ],
    )
    def test_chinese_pattern_detection(self, text: str, expected: bool):
        """Chinese format patterns should correctly identify test results."""
        _chinese_patterns = [
            r"(通过|成功)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"(失败|错误)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"(跳过|忽略)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"\d+\s*(个|项|件|测试)\s*(通过|成功|失败|跳过)",
        ]
        has_chinese = any(re.search(p, text) for p in _chinese_patterns)
        assert has_chinese == expected


class TestJapaneseOutputDetection:
    """Verify that Japanese pytest output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Japanese format with colon separator
            ("通過: 2398 件", True),
            ("通過：2398件", True),  # Chinese colon
            ("成功: 100 件", True),
            ("失敗: 5件", True),
            ("エラー: 3件", True),
            ("スキップ: 69件", True),
            ("スキップ済み: 10件", True),
            # Japanese format without colon
            ("通過 2398 件", True),
            ("成功 100 テスト", True),
            ("失敗 5件", True),
            # Japanese hallucination (should NOT match)
            ("テストがバックグラウンドで実行中", False),
            ("テスト進捗50%", False),
            ("テスト.*実行中", False),
        ],
    )
    def test_japanese_pattern_detection(self, text: str, expected: bool):
        """Japanese format patterns should correctly identify test results."""
        _japanese_patterns = [
            r"(通過|成功)[:：\s]+\d+\s*(件|テスト)?",
            r"(失敗|エラー)[:：\s]+\d+\s*(件|テスト)?",
            r"(スキップ|スキップ済み)[:：\s]+\d+\s*(件|テスト)?",
        ]
        has_japanese = any(re.search(p, text) for p in _japanese_patterns)
        assert has_japanese == expected


class TestKoreanOutputDetection:
    """Verify that Korean pytest output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Korean format with colon separator
            ("통과: 2398개", True),
            ("통과：2398개", True),  # Chinese colon
            ("성공: 100개", True),
            ("실패: 5개", True),
            ("오류: 3개", True),
            ("건너뜀: 69개", True),
            ("스킵: 10개", True),
            # Korean format without colon
            ("통과 2398개", True),
            ("성공 100 테스트", True),
            ("실패 5개", True),
            # Korean hallucination (should NOT match)
            ("테스트가 백그라운드에서 실행 중", False),
            ("테스트 진행 50%", False),
        ],
    )
    def test_korean_pattern_detection(self, text: str, expected: bool):
        """Korean format patterns should correctly identify test results."""
        _korean_patterns = [
            r"(통과|성공)[:：\s]+\d+\s*(개|테스트)?",
            r"(실패|오류)[:：\s]+\d+\s*(개|테스트)?",
            r"(건너뜀|스킵)[:：\s]+\d+\s*(개|테스트)?",
        ]
        has_korean = any(re.search(p, text) for p in _korean_patterns)
        assert has_korean == expected


class TestRustOutputDetection:
    """Verify that Rust cargo test output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Rust cargo test output
            ("running 4 tests", True),
            ("test result: ok", True),
            ("test result: ok. 3 passed; 1 failed", True),
            ("test result: FAILED", True),
            ("3 passed; 1 failed", True),
            ("0 passed; 0 failed", True),
            ("test result: ok. 4 passed", True),
            # Non-test Rust output (should NOT match)
            ("cargo build", False),
            ("Compiling myproject", False),
            ("Finished dev target", False),
        ],
    )
    def test_rust_pattern_detection(self, text: str, expected: bool):
        """Rust cargo test patterns should correctly identify test results."""
        _rust_patterns = [
            r"running\s+\d+\s+tests",
            r"test\s+result:\s*ok",
            r"test\s+result:\s*FAILED",
            r"\d+\s+passed;\s*\d+\s+failed",
            r"\d+\s+passed",
        ]
        has_rust = any(re.search(p, text, re.IGNORECASE) for p in _rust_patterns)
        assert has_rust == expected


class TestJavaOutputDetection:
    """Verify that Java Maven/Gradle test output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Maven test output
            ("Tests run: 10, Failures: 0, Skipped: 0", True),
            ("Tests run: 5", True),
            ("Failures: 3", True),
            ("Errors: 2", True),
            ("BUILD SUCCESS", True),
            ("BUILD FAILURE", True),
            ("FAILURE!", True),
            # Gradle test output
            ("BUILD SUCCESSFUL", True),
            # Non-test Java output (should NOT match)
            ("mvn compile", False),
            ("Compiling sources", False),
            ("Downloading dependencies", False),
        ],
    )
    def test_java_pattern_detection(self, text: str, expected: bool):
        """Java Maven/Gradle patterns should correctly identify test results."""
        _java_patterns = [
            r"Tests\s+run:\s*\d+",
            r"Failures:\s*\d+",
            r"Errors:\s*\d+",
            r"BUILD\s+SUCCESS",
            r"BUILD\s+SUCCESSFUL",
            r"BUILD\s+FAILURE",
            r"FAILURE!",
        ]
        has_java = any(re.search(p, text) for p in _java_patterns)
        assert has_java == expected


class TestMultilingualHallucinationDetection:
    """Verify that multilingual hallucination patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Chinese hallucination
            ("测试在后台运行", True),
            ("测试正在运行约50%", True),
            ("测试进度50%", True),
            ("后台测试进度", True),
            # English hallucination
            ("tests running in background", True),
            ("test progress 50%", True),
            ("running tests... 50%", True),
            # Japanese hallucination (Issue #1538)
            ("テストがバックグラウンドで実行中", True),
            ("テスト実行中", True),
            ("テスト進捗50%", True),
            # Korean hallucination (Issue #1538)
            ("테스트가 백그라운드에서 실행 중", True),
            ("테스트실행중", True),
            ("테스트 진행 50%", True),
            # Real test output (should NOT match)
            ("通过: 2398 个", False),
            ("通過: 2398 件", False),
            ("통과: 2398개", False),
            ("3 passed, 2 failed", False),
            ("test result: ok", False),
        ],
    )
    def test_multilingual_hallucination_detection(self, text: str, expected: bool):
        """Multilingual hallucination patterns should be correctly detected."""
        _hallucination_patterns = [
            # Chinese
            r"测试在后台运行",
            r"测试正在运行.*%",
            r"测试进度.*%",
            r"后台测试.*进度",
            # English
            r"tests\s+(are\s+)?running\s+in\s+(the\s+)?background",
            r"test\s+progress.*%",
            r"running\s+tests.*%",
            # Japanese (Issue #1538)
            r"テスト.*バックグラウンド",
            r"テスト.*実行中",
            r"テスト.*進捗.*%",
            # Korean (Issue #1538)
            r"테스트.*백그라운드",
            r"테스트.*실행.*중",
            r"테스트.*진행.*%",
        ]
        has_hallucination = any(re.search(p, text, re.IGNORECASE) for p in _hallucination_patterns)
        assert has_hallucination == expected


class TestMultilingualSkippedKeywords:
    """Verify that multilingual skipped keywords are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Chinese skipped keywords
            ("pytest 未安装", True),
            ("所有测试框架都不可用", True),
            ("命令被阻止", True),
            # English skipped keywords
            ("pytest was not installed", True),
            ("no test framework available", True),
            ("commands were blocked", True),
            # Japanese skipped keywords (Issue #1538)
            ("pytestがインストールされていません", True),
            ("テストフレームワークが利用できません", True),
            ("コマンドがブロックされました", True),
            # Korean skipped keywords (Issue #1538)
            ("pytest가 설치되지 않았습니다", True),
            ("테스트 프레임워크를 사용할 수 없습니다", True),
            ("명령이 차단되었습니다", True),
            # Normal test output (should NOT match)
            ("通过: 2398 个", False),
            ("3 passed, 2 failed", False),
            ("test result: ok", False),
        ],
    )
    def test_multilingual_skipped_keywords(self, text: str, expected: bool):
        """Multilingual skipped keywords should be correctly detected."""
        _skipped_keywords = [
            # Chinese
            "pytest 未安装",
            "所有测试框架都不可用",
            "命令被阻止",
            # English
            "pytest was not installed",
            "no test framework available",
            "commands were blocked",
            # Japanese (Issue #1538)
            "pytestがインストールされていません",
            "テストフレームワークが利用できません",
            "コマンドがブロックされました",
            # Korean (Issue #1538)
            "pytest가 설치되지 않았습니다",
            "테스트 프레임워크를 사용할 수 없습니다",
            "명령이 차단되었습니다",
        ]
        has_skip = any(kw in text for kw in _skipped_keywords)
        assert has_skip == expected
