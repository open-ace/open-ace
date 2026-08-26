# Test encoding handling for hostname (Issue #3081)
#
# This script tests the encoding handling for various hostname types
# to verify that the fix for Issue #3081 works correctly.
#
# Usage:
#   .\test-encoding.ps1
#
# Requirements:
#   - PowerShell 5.1 or PowerShell 7+
#   - No external dependencies

# ============================================================
# Test helper functions
# ============================================================

function Test-JsonEncoding {
    param(
        [string]$TestName,
        [string]$Hostname
    )

    Write-Host "`n[TEST] $TestName" -ForegroundColor Cyan
    Write-Host "  Input: '$Hostname'" -ForegroundColor White

    # Save original encoding
    $originalOutputEncoding = [Console]::OutputEncoding
    $originalPsEncoding = $OutputEncoding

    try {
        # Set UTF-8 encoding
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $OutputEncoding = [System.Text.Encoding]::UTF8

        # Create test object
        $testObject = @{
            hostname = $Hostname
            timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        }

        # Convert to JSON
        $json = $testObject | ConvertTo-Json -Compress
        Write-Host "  JSON: $json" -ForegroundColor Gray

        # Write to temp file
        $tempFile = "$env:TEMP\test-encoding-$([System.Guid]::NewGuid()).json"

        if ($PSVersionTable.PSVersion.Major -ge 7) {
            $json | Out-File -FilePath $tempFile -Encoding utf8NoBOM -NoNewline
        } else {
            $utf8NoBomEncoding = New-Object System.Text.UTF8Encoding $False
            [System.IO.File]::WriteAllText($tempFile, $json, $utf8NoBomEncoding)
        }

        # Verify no BOM
        $fileBytes = [System.IO.File]::ReadAllBytes($tempFile)
        $hasBom = $fileBytes.Length -ge 3 -and $fileBytes[0] -eq 0xEF -and $fileBytes[1] -eq 0xBB -and $fileBytes[2] -eq 0xBF

        if ($hasBom) {
            Write-Host "  ❌ FAIL: File contains UTF-8 BOM" -ForegroundColor Red
        } else {
            Write-Host "  ✓ No BOM detected" -ForegroundColor Green
        }

        # Read back and verify
        $readBack = Get-Content $tempFile -Raw
        $parsedObject = $readBack | ConvertFrom-Json
        $hostnameAfter = $parsedObject.hostname

        Write-Host "  Read back: '$hostnameAfter'" -ForegroundColor Gray

        if ($hostnameAfter -eq $Hostname) {
            Write-Host "  ✅ PASS: Hostname preserved correctly" -ForegroundColor Green
        } else {
            Write-Host "  ❌ FAIL: Hostname corrupted" -ForegroundColor Red
            Write-Host "    Expected: '$Hostname'" -ForegroundColor Red
            Write-Host "    Got: '$hostnameAfter'" -ForegroundColor Red
        }

        # Clean up
        Remove-Item $tempFile -ErrorAction SilentlyContinue

    } finally {
        # Restore original encoding
        [Console]::OutputEncoding = $originalOutputEncoding
        $OutputEncoding = $originalPsEncoding
    }
}

function Test-EncodingFunctions {
    Write-Host "`n============================================================" -ForegroundColor Cyan
    Write-Host "Testing Encoding Helper Functions (Issue #3081)" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan

    # Test Set-JsonEncoding
    Write-Host "`n[TEST] Set-JsonEncoding function" -ForegroundColor Cyan
    $originalEncoding = [Console]::OutputEncoding
    Write-Host "  Original encoding: $($originalEncoding.EncodingName)" -ForegroundColor Gray

    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $OutputEncoding = [System.Text.Encoding]::UTF8

        if ([Console]::OutputEncoding.EncodingName -eq "Unicode (UTF-8)") {
            Write-Host "  ✅ PASS: UTF-8 encoding set successfully" -ForegroundColor Green
        } else {
            Write-Host "  ❌ FAIL: Encoding not set correctly" -ForegroundColor Red
        }
    } finally {
        [Console]::OutputEncoding = $originalEncoding
        $OutputEncoding = $originalEncoding
    }

    # Test Restore-JsonEncoding
    Write-Host "`n[TEST] Restore-JsonEncoding function" -ForegroundColor Cyan
    $restoredEncoding = [Console]::OutputEncoding

    if ($restoredEncoding.EncodingName -eq $originalEncoding.EncodingName) {
        Write-Host "  ✅ PASS: Encoding restored successfully" -ForegroundColor Green
    } else {
        Write-Host "  ❌ FAIL: Encoding not restored correctly" -ForegroundColor Red
    }
}

# ============================================================
# Main test execution
# ============================================================

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Encoding Test Script for Issue #3081" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "PowerShell Version: $($PSVersionTable.PSVersion)" -ForegroundColor White
Write-Host "Current Console Encoding: $([Console]::OutputEncoding.EncodingName)" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan

# Test encoding functions
Test-EncodingFunctions

# Test various hostname types
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "Testing Various Hostname Types" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$testCases = @(
    @{ Name = "Simple ASCII hostname"; Hostname = "WIN-PC1" },
    @{ Name = "Chinese hostname (simplified)"; Hostname = "我的电脑" },
    @{ Name = "Chinese hostname (traditional)"; Hostname = "我的電腦" },
    @{ Name = "Japanese hostname"; Hostname = "マイコンピュータ" },
    @{ Name = "Korean hostname"; Hostname = "내컴퓨터" },
    @{ Name = "Mixed Chinese-English"; Hostname = "My电脑-测试" },
    @{ Name = "Hostname with numbers"; Hostname = "电脑123" },
    @{ Name = "Long Chinese hostname"; Hostname = "测试服务器主机名" }
)

$passCount = 0
$failCount = 0

foreach ($testCase in $testCases) {
    try {
        Test-JsonEncoding -TestName $testCase.Name -Hostname $testCase.Hostname
        $passCount++
    } catch {
        Write-Host "  ❌ ERROR: $($_.Exception.Message)" -ForegroundColor Red
        $failCount++
    }
}

# Summary
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "Test Summary" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Total tests: $($testCases.Count)" -ForegroundColor White
Write-Host "Passed: $passCount" -ForegroundColor Green
Write-Host "Failed: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Green" })

if ($failCount -eq 0) {
    Write-Host "`n✅ All tests passed! Issue #3081 fix is working correctly." -ForegroundColor Green
} else {
    Write-Host "`n⚠️  Some tests failed. Please review the output above." -ForegroundColor Yellow
}

Write-Host "`n============================================================" -ForegroundColor Cyan
