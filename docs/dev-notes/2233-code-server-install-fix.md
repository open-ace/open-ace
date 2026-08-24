# Issue #2233: 远程机器注册时 code-server 安装失败修复

## 问题背景

远程机器注册时 code-server 安装失败，原因为：
1. Node.js 版本不兼容导致安装失败，但无明确错误提示
2. 残留 shim 文件导致新安装失败
3. 缺少权限预检，安装到无写入权限的目录失败
4. 安装成功但运行失败的情况未检测
5. npm registry 问题导致网络环境不佳时安装慢或失败
6. native 编译工具缺失导致安装失败

## 解决方案

### 核心改动

修改 `remote-agent/install.ps1` 第 272-318 行的 code-server 安装逻辑，从 467 行扩展到 908 行。

### 新增辅助函数

1. **Get-NpmGlobalPrefix**: 动态获取 npm 全局目录
   - 使用 `npm config get prefix` 获取实际路径
   - 失败时使用默认值 `$env:APPDATA\npm`

2. **Test-NpmGlobalWritable**: 检测全局目录写入权限
   - 通过创建临时文件验证写入权限
   - 失败时提前跳过安装并打印建议

3. **Test-NodeVersionCompatibility**: Node.js 版本兼容性检查
   - 支持 v18.17.0、18.17.0、18.17 等多种格式
   - v16 及以下、v17：不兼容
   - v18：兼容但建议升级
   - v20/v22：完全兼容

4. **Clear-CodeServerResidue**: 清理残留文件和进程
   - 检测运行中的 code-server 进程
   - 清理 npm 全局目录下的残留文件
   - 防止误删其他 node_modules

5. **Test-CodeServerExecution**: 安装后运行验证
   - 执行 `code-server --version` 验证
   - 安装成功但运行失败时标记为不可用

6. **Get-NpmRegistry**: 获取当前 npm registry
   - 用于诊断建议

7. **Invoke-NpmInstall**: 改进的安装流程
   - 使用 `System.Diagnostics.Process` 替代 `Start-Job`
   - 支持超时控制
   - 捕获 stdout/stderr 用于诊断

8. **Show-InstallationFailureDiagnosis**: 失败诊断
   - 识别常见错误模式（EACCES、ENOGIT、gyp ERR! 等）
   - 提供针对性的修复建议

### 安装流程改进

```
SkipCodeServer → 已安装检查(带运行验证) → npm prefix 动态获取
    → 权限预检 → Node.js 版本检查 → 清理残留 → Process 类安装
    → 安装后运行验证 → 失败诊断 → 降级处理
```

### 配置记录

- 在 `config.json` 中记录 `code_server_available` 字段
- 在 `capabilities` 中使用正确的可用性标记

## 验证结果

### 测试通过

```
tests/unit/test_remote_agent_installer.py::test_install_script_reports_missing_python PASSED
tests/unit/test_remote_agent_installer.py::test_shell_installer_downloads_all_runtime_agent_files PASSED
tests/unit/test_remote_agent_installer.py::test_powershell_installer_downloads_all_runtime_agent_files PASSED
tests/unit/test_remote_agent_installer.py::test_shell_installer_downloads_all_cli_adapters PASSED
tests/unit/test_remote_agent_installer.py::test_powershell_installer_downloads_all_cli_adapters PASSED

tests/unit/test_remote_agent_start_scripts.py (24 tests) PASSED
```

### 向后兼容

- `-SkipCodeServer` 参数行为保持一致
- 文件下载列表不变
- 其他安装步骤不变

## 关联资源

- Issue: #2233
- 修改文件: `remote-agent/install.ps1`
- 测试文件: `tests/unit/test_remote_agent_installer.py`
