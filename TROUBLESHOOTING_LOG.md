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

## 问题 3：远程机器注册时 code-server 安装失败（非 ASCII 路径编码问题）

- **处理时间**：2026-08-01
- **故障现象**：
  在远程机器（Windows）上执行注册令牌生成的 PowerShell 安装命令后，agent 文件下载成功、Python 依赖安装成功、CLI 工具（qwen-code-cli）安装成功，但在安装 code-server 阶段报错：`[ERROR] Script failed: interval.sys の yyloader: p1 を 24 行`。脚本因此中止，远程机器注册流程未完成。

- **诊断结论**：
  经代码审查和错误截图分析，确认存在 Windows 路径编码兼容性问题：
  1. 远程机器的 Windows 用户名包含非 ASCII 字符（如中文/日文），导致 `$env:USERPROFILE` 路径（如 `C:\Users\用户名`）含非 ASCII 字符。
  2. Windows 上 `npm install -g` 默认将全局包安装到 `%APPDATA%\npm`（位于用户目录下），因此 npm 全局安装路径也包含非 ASCII 字符。
  3. `code-server` npm 包依赖 `yyloader`（Node.js 原生模块），该模块在非 ASCII 路径下加载时因编码问题失败。
  4. 相比之下，`qwen-code-cli` 包的依赖较简单，不涉及原生模块，因此安装成功。

- **根本原因**：
  `remote-agent/install.ps1` 脚本中 `npm install -g code-server` 和 `npm install -g @qwen-code/qwen-code@latest` 直接使用默认的 npm 全局前缀（位于非 ASCII 路径下），导致 Node.js 原生模块（yyloader）无法正确加载。

- **解决办法**：
  修改 `install.ps1`，添加非 ASCII 路径检测和自动规避机制：
  1. 在脚本开头添加路径检测逻辑：检查 `$env:USERPROFILE` 和 `$env:APPDATA` 是否包含非 ASCII 字符（正则 `^[\x00-\x7F]+$`）。
  2. 若检测到非 ASCII 字符，设置 `$needAsciiNpmPrefix = $true`，创建 ASCII-only 的 npm 全局前缀目录（`C:\npm-global`），并设置 `$env:NPM_CONFIG_PREFIX` 环境变量指向该目录。
  3. Step 5（CLI 工具安装）和 Step 5.5（code-server 安装）的 `npm install -g` 命令无需修改——`NPM_CONFIG_PREFIX` 环境变量会自动重定向全局安装到 ASCII 路径。
  4. code-server 的 `Start-Job` 脚本块不继承当前会话环境变量，因此通过 `-ArgumentList` 显式传递 prefix 值，在 job 内部设置 `$env:NPM_CONFIG_PREFIX`。
  5. 安装完成后，将 ASCII npm 前缀目录（注意：Windows 上 npm 将 .cmd 脚本直接放在 prefix 目录，而非 `bin/` 子目录）添加到用户 PATH 环境变量。
  6. 当 code-server 安装到自定义前缀后，脚本增加了 PATH 未更新情况下的降级提示逻辑（检查 `code-server.cmd` 是否存在）。

- **修改的文件**：
  1. `remote-agent/install.ps1`
     - 新增非 ASCII 路径检测逻辑和 `NPM_CONFIG_PREFIX` 环境变量设置（第 53-80 行）
     - Step 5 CLI 工具安装：添加 PATH 更新（使用 prefix 目录本身，非 `bin/` 子目录）
     - Step 5.5 code-server 安装：Start-Job 传递 prefix 参数并在内部设置环境变量，安装后添加 PATH 及降级提示

- **状态**：未解决（NPM_CONFIG_PREFIX 方案无效，已回退修改，待重新诊断）

---
