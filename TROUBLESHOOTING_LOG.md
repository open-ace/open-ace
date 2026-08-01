# Open ACE 故障问题与解决日志

> 本文件记录项目使用/开发过程中遇到的故障问题、诊断原因、解决办法及处理时间，持续积累。

---

## 问题 1：强制修改密码后未提示成功，需点击 retry 才进入系统

- **处理时间**：2026-08-01
- **故障现象**：
  使用管理员账号（admin/admin123）登录后，进入强制修改密码页面。修改密码提交后，页面未显示修改成功信息，反而仍然显示"需要修改密码"的提示；点击该提示页面上的 retry 链接后，才进入修改密码完成后的系统主页面。

- **诊断结论**：
  经代码审查和浏览器测试，确认存在两个独立的 bug：
  1. **红色横幅不消失、需手动点击 retry**：前端在密码修改成功后，React Query 缓存未失效，导致之前因 403 `password_change_required` 失败的查询没有自动重试，Dashboard 数据未加载。
  2. **无成功提示 Toast**：前端未在密码修改成功后显示 Toast 提示。即使添加了 `toast.success()` 调用，Toast 仍然不显示。通过控制台诊断日志确认 `toast.success()` 被调用了，但 Toast 组件未渲染。进一步排查发现 Vite 构建时 `hooks` 和 `components` chunk 之间存在循环依赖（`Circular chunk: hooks -> components -> hooks`），导致 `useToastStore` Zustand store 在运行时存在多个实例——调用方更新的 store 实例与 `ToastHost` 订阅的 store 实例不同，Toast 永远不会显示。

- **根本原因**：
  1. **React Query 缓存未失效**：`frontend/src/hooks/useAuth.ts` 的 `changePasswordMutation` 的 `onSuccess` 回调中没有调用 `queryClient.invalidateQueries()`。
  2. **循环依赖导致 store 多实例**：`useToastStore` 原本定义在 `components/common/Toast.tsx` 中，但 `hooks/useAuth.ts` 也需要导入它。Vite 的 `manualChunks` 配置将 `src/hooks/` 分到 `hooks` chunk、`src/components/common/` 分到 `components` chunk，两者相互引用形成循环依赖，导致模块在运行时被实例化多次，`useToastStore` 不再是单例。

- **解决办法**：
  1. **在 `useAuth.ts` 的 `changePasswordMutation` 的 `onSuccess` 回调中添加 `queryClient.invalidateQueries()`**：使所有因 403 失败的查询自动重试，红色横幅自动消失，Dashboard 自动加载。
  2. **在 `ForceChangePasswordModal.tsx` 中添加 `toast.success()` 调用**：显示密码修改成功提示（使用 i18n 多语言）。
  3. **在 `i18n/index.ts` 中添加 `passwordChangedSuccess` 多语言键值**：支持中文、英文、日语、韩语。
  4. **将 `useToastStore` 从 `components/common/Toast.tsx` 移到新建的 `store/toastStore.ts`**：打破循环依赖。`hooks` 和 `components` chunk 都从 `store` chunk 导入 `useToastStore`，`store` chunk 无循环依赖，确保 `useToastStore` 是单例。`components/common/Toast.tsx` 和 `components/common/index.ts` 改为从 `store/toastStore` 导入/重新导出。

- **修改的文件**：
  1. `frontend/src/hooks/useAuth.ts` — `changePasswordMutation.onSuccess` 中添加 `queryClient.invalidateQueries()`
  2. `frontend/src/components/features/ForceChangePasswordModal.tsx` — 密码修改成功后调用 `toast.success()`
  3. `frontend/src/i18n/index.ts` — 添加 `passwordChangedSuccess` 多语言键值（英/中/日/韩）
  4. `frontend/src/store/toastStore.ts` — 新建，将 `useToastStore` 定义从 `Toast.tsx` 移至此文件
  5. `frontend/src/components/common/Toast.tsx` — 改为从 `@/store/toastStore` 导入 `useToastStore`，移除本地定义
  6. `frontend/src/components/common/index.ts` — 改为从 `@/store/toastStore` 重新导出 `useToastStore` 及类型

- **状态**：已解决

---

## 问题 2：保存 API Key 时报错（时间戳/语法错误）

- **处理时间**：2026-08-01
- **故障现象**：
  在系统中配置 API Key（提供商选 OpenAI，密钥名称、基础 URL 等填入 DeepSeek 的 API 数据，支持的 CLI 工具选 Qwen Code），点击保存时后端报错，错误信息提示时间戳相关语法错误（PostgreSQL 语法错误）。

- **诊断结论**：
  经代码审查定位到 `app/modules/workspace/api_key_proxy.py` 中 `APIKeyProxyService` 的 `store_key` 和 `update_key` 方法。当 API Key 配置包含 `base_url` 时，代码会调用 `resolve_and_store_ips(base_url)` 解析域名 IP 用于 DNS 重绑定保护。解析成功后，将 `resolved_at` 变量赋值为字符串 `"CURRENT_TIMESTAMP"`，随后通过参数化查询（`%s`/`?` 占位符）将其作为参数传入 SQL 的 `INSERT`/`UPDATE` 语句。

  PostgreSQL 将绑定参数 `'CURRENT_TIMESTAMP'` 当作普通字符串字面量，而非 SQL 关键字 `CURRENT_TIMESTAMP`，尝试将字符串赋给 `timestamp` 类型字段时发生类型/语法错误。

- **根本原因**：
  `api_key_proxy.py` 中两处将 `resolved_at` 赋值为字符串 `"CURRENT_TIMESTAMP"`，然后通过参数化查询传入 SQL：
  1. `store_key` 方法（原第 597 行）：`resolved_at = "CURRENT_TIMESTAMP"`
  2. `update_key` 方法（原第 938 行）：`resolved_at = "CURRENT_TIMESTAMP"`

  参数化查询会将占位符 `%s` 替换为带引号的字符串值 `'CURRENT_TIMESTAMP'`，而不是 SQL 关键字 `CURRENT_TIMESTAMP`，导致 PostgreSQL 报错。

- **解决办法**：
  将两处 `resolved_at = "CURRENT_TIMESTAMP"` 改为 `resolved_at = datetime.now()`（Python `datetime` 对象）。`datetime` 对象会被 psycopg2 正确序列化为 PostgreSQL 的 `timestamp` 值，同时也兼容 SQLite。

- **修改的文件**：
  1. `app/modules/workspace/api_key_proxy.py`
     - `store_key` 方法（第 597 行）：`resolved_at = "CURRENT_TIMESTAMP"` → `resolved_at = datetime.now()`
     - `update_key` 方法（第 938 行）：`resolved_at = "CURRENT_TIMESTAMP"` → `resolved_at = datetime.now()`

- **状态**：已解决

---

## 问题 3：远程机器注册时 code-server 安装失败

- **处理时间**：2026-08-01
- **故障现象**：
  在远程机器（Windows, Node.js v24.16.0）上执行注册令牌生成的 PowerShell 安装命令后，agent 文件下载成功、Python 依赖安装成功、CLI 工具（qwen-code-cli）安装成功，但在检查 code-server 是否已安装时脚本崩溃。错误信息：`node:internal/modules/cjs/loader:1503` + `Error: Cannot find module '...\node_modules\code-server\out\node\entry.js'`。

- **诊断结论**：
  经多轮诊断，确认存在以下多层问题（逐层暴露）：

  1. **code-server 安装不完整（残留 shim）**：之前失败的 `npm install -g code-server` 留下了 `code-server.ps1` 命令文件，但 `node_modules\code-server\` 目录不存在。脚本检查 `Get-Command code-server` 找到了 shim，执行 `code-server --version` 时因 entry.js 缺失报错，`$ErrorActionPreference = "Stop"` + `2>&1` 将 stderr 包装为终止性错误，触发 `trap` 导致脚本退出。

  2. **Node.js v24 与 code-server 不兼容**：code-server@4.3.0（npm 上 Node 24 解析到的版本）依赖 argon2@0.28.4，需要 node-gyp 编译 C++ 原生模块。argon2@0.28.4 的 `binding.gyp` 引用 `node-addon-api` v4，但 Node.js v24 的 npm 依赖提升行为导致 `node-addon-api` 不可见，`binding.gyp` 报 `Undefined variable module_name`。

  3. **Node.js 18 下安装 code-server 需要多个 VS 组件**：通过 nvm-windows 安装 Node.js 18 LTS 后，code-server@4.89.1 的 postinstall 脚本需要 `sh`（Git for Windows 提供），其 VSCode 依赖包含 6 个原生模块（native-watchdog、@vscode/windows-process-tree、@vscode/spdlog 等）需要编译。编译需要：
     - VC++ toolset v143（已有）
     - Windows 11 SDK（需安装）
     - **Spectre 缓解库**（`MSVC v143 - VS 2022 C++ x64/x86 Spectre 缓解库`，需手动安装）— `native-watchdog` 和 `@vscode/windows-registry` 报 `MSB8040: 此项目需要缓解了 Spectre 漏洞的库`

  4. **nvm 符号链接导致 wrapper 路径错误**：nvm-windows 使用 junction（`C:\nvm4w\nodejs`）指向当前激活版本。脚本用 `(Get-Command node).Source` 获取的路径是符号链接路径，`nvm use 24` 后该路径指向 Node.js 24，导致 wrapper 脚本用错误的 Node.js 版本启动 code-server。

- **根本原因**：
  1. install.ps1 的 code-server 版本检查无容错处理，损坏的 shim 导致脚本崩溃
  2. Node.js v24 与 code-server npm 包的原生模块不兼容
  3. code-server 原生模块编译需要 Spectre 缓解库，VS Build Tools 默认不包含
  4. nvm 符号链接在版本切换后路径变化，wrapper 需要解析到真实路径

- **解决办法**：
  1. **用 nvm-windows 安装 Node.js 18 LTS**：解决 Node.js v24 与 code-server 不兼容问题。code-server@4.89.1 在 Node.js 18 下可正确安装。
  2. **安装 VS Build Tools 完整组件**：VC++ toolset v143 + Windows 11 SDK + Spectre 缓解库（通过 VS Installer 手动安装）。
  3. **添加 Git 的 sh.exe 到 PATH**：code-server postinstall 脚本需要 `sh` 命令，由 Git for Windows 的 `bin\sh.exe` 提供。
  4. **创建 wrapper 脚本**：在 `%APPDATA%\npm` 下创建 `code-server.cmd` 和 `code-server.ps1`，硬编码 Node.js 18 的真实路径（非 nvm 符号链接）来启动 code-server，使 code-server 始终用 Node.js 18 运行，其他工具继续用 Node.js 24。
  5. **解析 nvm 符号链接**：脚本在切换回 Node.js 24 之前，解析 nvm junction 到真实路径（如 `C:\Users\nuc\AppData\Local\nvm\v18.20.8`），用真实路径更新 wrapper 脚本。

- **修改的文件**：
  1. `fix-code-server.ps1`（项目根目录辅助脚本）— 完整的 code-server 安装修复脚本，包含 nvm 安装、Node 18 安装、Spectre 库检测、code-server 安装、wrapper 创建、符号链接解析
  2. `remote-agent/install.ps1` — 待修改：添加 code-server 版本检查容错、清理残留 shim、安装失败降级处理

- **状态**：已解决（code-server 安装成功并可通过 wrapper 验证）

---
