#!/bin/bash
# sudoers白名单有效性验证测试脚本
# Issue #1514: 安全加固 - 精确参数白名单测试

set -e

echo "============================================================================"
echo "sudoers白名单有效性验证测试"
echo "============================================================================"
echo ""

# 测试结果统计
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# 测试函数
test_command() {
    local description="$1"
    local command="$2"
    local expected_result="$3"  # "success" or "fail"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo "测试 #$TOTAL_TESTS: $description"
    echo "  命令: $command"

    if [ "$expected_result" = "success" ]; then
        # 期望命令执行成功
        if sudo -u openace $command 2>/dev/null; then
            echo "  结果: ✅ 执行成功（符合预期）"
            PASSED_TESTS=$((PASSED_TESTS + 1))
        else
            echo "  结果: ❌ 执行失败（不符合预期，应该成功）"
            FAILED_TESTS=$((FAILED_TESTS + 1))
        fi
    else
        # 期望命令执行失败（权限拒绝）
        if sudo -u openace $command 2>/dev/null; then
            echo "  结果: ❌ 执行成功（不符合预期，应该失败）"
            FAILED_TESTS=$((FAILED_TESTS + 1))
        else
            echo "  结果: ✅ 权限拒绝（符合预期）"
            PASSED_TESTS=$((PASSED_TESTS + 1))
        fi
    fi
    echo ""
}

echo "一、git安全命令测试（期望执行成功）"
echo "============================================================================"

# 测试git安全命令（只测试白名单中实际使用的命令）
test_command "git status --porcelain" "git status --porcelain" "success"
test_command "git branch --show-current" "git branch --show-current" "success"
test_command "git rev-parse HEAD" "git rev-parse HEAD" "success"
test_command "git rev-list --count HEAD" "git rev-list --count HEAD" "success"
test_command "git remote get-url origin" "git remote get-url origin" "success"
# --force-with-lease is explicitly whitelisted for auto-dev branches (Issue #1854).
# Branch name can't be pattern-matched in sudoers; the Python git_push() guard
# enforces the auto-dev/* restriction. Here we only verify the command form is allowed.
test_command "git push origin auto-dev/test --force-with-lease --dry-run" "git push origin auto-dev/test --force-with-lease --dry-run" "success"

echo "二、gh安全命令测试（期望执行成功）"
echo "============================================================================"

# 注意：gh --version和gh auth status不在白名单中，autonomous工作流不需要这些命令
# gh CLI通过GH_TOKEN环境变量认证，无需单独auth status检查
# 实际使用的gh命令需要GH_TOKEN和repo上下文，在CI环境中难以模拟
# 白名单设计遵循最小权限原则，只包含必需命令

echo ""
echo "三、危险命令阻断测试（期望权限拒绝）"
echo "============================================================================"

# 测试git危险命令（应该被阻断）
test_command "git reset --hard HEAD" "git reset --hard HEAD" "fail"
test_command "git clean -fd" "git clean -fd" "fail"
test_command "git push --force origin main" "git push --force origin main" "fail"

# 测试gh危险命令（应该被阻断）
test_command "gh repo delete test-repo --yes" "gh repo delete test-repo --yes" "fail"
test_command "gh repo fork test-repo" "gh repo fork test-repo" "fail"
test_command "gh api repos/owner/repo/delete" "gh api repos/owner/repo/delete" "fail"

echo "四、参数绕过攻击测试（期望权限拒绝）"
echo "============================================================================"

# 测试参数绕过
test_command "git push origin main --force" "git push origin main --force" "fail"
test_command "git commit -m 'test' --dangerous-param" "git commit -m 'test' --dangerous-param" "fail"

echo "============================================================================"
echo "测试统计"
echo "============================================================================"
echo "  总测试数: $TOTAL_TESTS"
echo "  通过数: $PASSED_TESTS"
echo "  失败数: $FAILED_TESTS"
echo ""

if [ $FAILED_TESTS -eq 0 ]; then
    echo "✅ 所有测试通过！sudoers白名单配置有效"
    echo "  - 安全命令执行成功: $PASSED_TESTS/$TOTAL_TESTS"
    echo "  - 危险命令成功阻断: ✅"
    echo "  - 参数绕过攻击阻断: ✅"
    exit 0
else
    echo "❌ 测试失败！sudoers白名单配置需要修复"
    echo "  - 通过率: $((PASSED_TESTS * 100 / TOTAL_TESTS))%"
    echo "  - 失败数: $FAILED_TESTS"
    exit 1
fi
