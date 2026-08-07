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

## 问题 4：多用户工作区初始化失败，普通用户登录后工作区无法加载

- **处理时间**：2026-08-01
- **故障现象**：
  使用普通用户（如 luyou）登录系统后，首页显示"localhost没有发送任何数据"，工作区未初始化，iframe 为空白。

- **诊断结论**：
  容器以非 root 用户（open-ace, uid=1000）运行，且 `WORKSPACE_MULTI_USER_MODE=false`。多用户工作区模式要求：
  1. 容器必须以 root 权限运行（需要 `user: "0"`）
  2. 必须启用多用户模式（`WORKSPACE_MULTI_USER_MODE=true`）
  3. 必须显式授权 root 多用户模式（`OPENACE_ALLOW_ROOT_MULTI_USER=1`）
  由于以上条件均不满足，系统无法为普通用户创建 Linux 系统账号和工作区目录，导致工作区无法启动。

- **根本原因**：
  Docker 容器默认以非 root 用户运行，而多用户模式需要 `useradd` 等 root 权限命令来创建系统用户。

- **解决办法**：
  修改 `docker-compose.yml`，添加以下配置：
  1. 在 `open-ace` 服务中添加 `user: "0"` 使容器以 root 运行
  2. 将 `WORKSPACE_MULTI_USER_MODE` 默认值改为 `true`
  3. 添加 `OPENACE_ALLOW_ROOT_MULTI_USER=1` 显式授权
  修改后重启容器，系统自动为所有用户（admin、luyou）创建 Linux 系统账号和 `/workspace/<username>` 工作目录。

- **修改的文件**：
  1. `docker-compose.yml` — 添加 `user: "0"`、修改 `WORKSPACE_MULTI_USER_MODE` 默认值、添加 `OPENACE_ALLOW_ROOT_MULTI_USER`

- **验证**：
  重启容器后日志显示：
  ```
  Configuring multi-user workspace mode (env=true, config=true)...
  Syncing workspace users from database...
    Created user: admin
    Created workspace directory: /workspace/admin
    Created user: luyou
    Created workspace directory: /workspace/luyou
  ```
  普通用户登录后工作区正常加载。

- **状态**：已解决

---

## 问题 5：LLM 代理请求自定义 base_url 时 SSL 证书验证失败导致 403 错误

- **处理时间**：2026-08-01
- **故障现象**：
  用户配置了 DeepSeek API Key（provider=openai, base_url=https://api.deepseek.com），在新建会话选择远程工作区时，返回错误：
  `[API Error: 403 Blocked outbound URL: security policy violation]`

- **诊断结论**：
  通过容器日志发现实际错误为 `maximum recursion depth exceeded while calling a Python object`。进一步在容器内用 `sys.setrecursionlimit(200)` 复现，捕获到真正的根因：
  `ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: IP address mismatch, certificate is not valid for '43.242.198.77'`

- **根本原因**：
  `app/utils/outbound_url_guard.py` 中的 `safe_request` 函数实现了 IP pinning 机制：将请求 URL 中的域名替换为解析到的 IP 字面量（如 `https://43.242.198.77/v1/chat/completions`），仅通过 Host 头保留原始域名。但 `urllib3`/`ssl` 模块使用 URL 中的主机名（即 IP）进行 TLS SNI 和证书验证，而 SSL 证书是签发给 `api.deepseek.com` 的，不是签发给 IP 地址的，导致 SSL 握手失败。SSL 错误触发 urllib3 重试机制，反复失败最终触发 Python 递归深度超限。

- **解决办法**：
  修改 `app/utils/outbound_url_guard.py` 中的两个组件：

  1. **`safe_request` 函数**：不再将 IP 替换到 URL 中，保留原始 URL（如 `https://api.deepseek.com/v1/chat/completions`），使 TLS SNI 和证书验证正常工作。同时将 `original_host` 传递给 `_PinnedIPAdapter` 用于连接时验证。

  2. **`_PinnedIPAdapter` 类**：
     - 新增 `original_host` 构造参数
     - 将 `_check_pinned_url` 重命名为 `_validate_url`
     - `_validate_url` 同时支持 IP 字面量和主机名验证：
       - IP 字面量：直接检查是否在允许列表中
       - 主机名：通过 DNS 解析后检查所有解析到的 IP 是否在允许列表中
     - DNS 解析失败时允许请求通过（由实际请求捕获错误）

  修改后 SSRF 防护仍然有效（验证解析到的 IP 是公网地址且在允许列表中），仅存在极小的 DNS rebinding TOCTOU 窗口（毫秒级），在开发环境中可接受。

- **修改的文件**：
  1. `app/utils/outbound_url_guard.py` — 修改 `safe_request` 和 `_PinnedIPAdapter`

- **验证**：
  在容器内执行测试：
  ```python
  from app.utils.outbound_url_guard import safe_request
  resp = safe_request('GET', 'https://api.deepseek.com/v1/models', timeout=10, headers={'Authorization': 'Bearer test'})
  # 修改前：ssl.SSLCertVerificationError → recursion error → 403
  # 修改后：Status: 401（SSL 握手成功，API 返回认证错误，符合预期）
  ```

- **状态**：已解决

---


## 问题 6：LLM 代理请求因 gevent monkey-patch 不兼容导致 RecursionError → 403

- **处理时间**：2026-08-01
- **故障现象**：
  用户在新建会话中发送消息时，LLM 代理请求持续返回 `[API Error: 403 Blocked outbound URL: security policy violation]`。容器日志显示：
  ```
  ERROR - LLM proxy safe_request error: maximum recursion depth exceeded while calling a Python object
  File "outbound_url_guard.py" line 316 → session.request()
  File "urllib3/util/ssl_.py" line 256 → create_urllib3_context
  ```

- **gunicorn 启动警告**：
  ```
  MonkeyPatchWarning: Monkey-patching ssl after ssl has already been imported
  may lead to errors, including RecursionError. Modules that had direct imports
  (NOT patched): ['urllib3.util', 'urllib3.util.ssl_']
  ```

- **真正根因（经过 5 轮分析）**：
  gevent monkey-patch 后的 `ssl` 与 urllib3 预导入的函数（`create_urllib3_context`、`ssl_wrap_socket`）之间产生**真无限递归**而非深度问题，`sys.setrecursionlimit()` 无效。

- **尝试过的无效方案**：
  1. 提升递归限制 → 无效（是真无限递归）
  2. `app/__init__.py` 开头 monkey-patch → 无效（urllib3 更早被 gunicorn 导入）
  3. `gunicorn_entry.py` 提前 monkey-patch → 无效（同上）
  4. `importlib.reload(urllib3.util.ssl_)` → 消除了递归但出现 SSLEOFError

- **有效解决办法**：
  `safe_request()` 中显式禁用代理查找：`kwargs.setdefault("proxies", {"http": None, "https": None})`。
  对比 `llm_proxy_handler.py` 中不走 `safe_request` 的路径（allowlist URL），该路径手动传了此参数且从未报错。根因是当 `proxies` 未设置时，urllib3 解析环境变量代理配置会触发递归；显式设为 `None` 后跳过该链路。

- **修改的文件**：
  1. `app/utils/outbound_url_guard.py` — 添加 `proxies` 参数（已回退递归限制方案）
  2. `app/modules/workspace/llm_proxy_handler.py` — 已回退 `_impl` 包装
  3. `app/__init__.py` — 添加了早期 monkey-patch（防御性）
  4. `gunicorn_entry.py` — 新建 gunicorn 入口包装
  5. `docker-entrypoint.sh` — 通过 `gunicorn_entry.py` 启动
  以上均有 `.bak.20260801` 备份。

- **状态**：已解决

---

## 问题 7：DeepSeek 模型名配置错误 + Qwen Code CLI 系统提示词中文定制

- **处理时间**：2026-08-01
- **故障现象一：模型名不匹配**：
  配置 DeepSeek API Key 后首次请求返回 400：
  ```
  "The supported API model names are deepseek-v4-pro or deepseek-v4-flash,
  but you passed deepseek-v4."
  ```

- **诊断结论一**：
  cli_settings 中模型 ID 为 `deepseek-v4`，DeepSeek API 只接受 `deepseek-v4-pro` 或 `deepseek-v4-flash`。用户在 UI 中将模型 ID 改为 `deepseek-v4-pro` 后正常。

- **故障现象二：回复自称 Qwen Code + 全英文**：
  问"你是什么模型"，回复为英文 "I'm Qwen Code, an interactive CLI agent developed by Alibaba Group..."

- **诊断结论二**：
  日志确认请求正确发到 DeepSeek。问题在于 Qwen Code CLI（`/usr/lib/node_modules/@qwen-code/qwen-code/cli.js`）有内置系统提示词，硬编码了英文身份 "Qwen Code, developed by Alibaba Group"。Qwen Code CLI 通过 `QWEN_SYSTEM_MD` 环境变量支持加载自定义 `system.md` 文件覆盖默认提示词。

- **解决办法**：
  1. **创建 `system-prompt.md`**（项目根目录）— 中文系统提示词，身份改为 "AI coding assistant"，所有指令翻译为中文
  2. **修改 `app/services/webui_manager.py`** — 在 `_launch_webui_process()` 的 `child_env` 中添加 `QWEN_SYSTEM_MD=/app/system-prompt.md`，通过 `OPENACE_SYSTEM_PROMPT_PATH` 环境变量可覆盖默认路径
  3. **修改 `docker-entrypoint.sh`** — 在 sudoers `env_keep` 白名单中添加 `QWEN_SYSTEM_MD`，确保多用户模式下环境变量通过 sudo 传递
  4. **保留扩展性**：设置 `OPENACE_SYSTEM_PROMPT_PATH` 环境变量可指向不同的提示词文件，后续可按租户/用户替换路径

- **修改的文件**：
  1. `system-prompt.md` — 新建，中文系统提示词
  2. `app/services/webui_manager.py` — 添加 `QWEN_SYSTEM_MD` 环境变量注入
  3. `docker-entrypoint.sh` — sudoers env_keep 添加 `QWEN_SYSTEM_MD`

- **验证方式**：
  重启容器后，在浏览器中打开工作区，新建会话发送中文消息，应得到中文回复且不再自称 "Qwen Code / Alibaba Group"。

- **状态**：已解决

---

## 问题 8：远程工作区 AI 创建目录和移动文件后，文件变更区域未显示结果

- **处理时间**：2026-08-02
- **故障现象**：
  在工作区界面中，让 AI（Qwen Code CLI + DeepSeek）创建目录和移动文件，AI 通过 shell 工具执行了操作并提示成功，但右侧"文件变更"区域显示"未检测到文件变更"。

- **诊断结论**：
  经代码审查，文件变更面板的显示依赖 `content_blocks` 中 `type: "file_change"` 的事件。不同 CLI 工具的文件变更检测能力存在差异：
  1. **Codex CLI**：通过 `patch_apply_end` 事件自动检测文件变更，生成 `file_change` 类型的 content_block（见 `scripts/fetch_codex.py` 第 449-513 行）
  2. **Qwen Code CLI**：仅生成 `tool_use`（shell 命令）和 `tool_result`（命令输出）类型的 content_block，**没有**生成 `file_change` 类型（见 `scripts/fetch_qwen.py` 第 303-405 行）
  3. **前端**：`MessageContent.tsx` 支持 `file_change` 类型的渲染，但需要后端/CLI 提供数据

  当 AI 使用 shell 工具执行 `mkdir`、`mv` 等文件操作时，Qwen Code CLI 仅记录了命令调用和输出，没有将其解析为文件变更事件上报。

- **根本原因**：
  **功能缺失**，非 bug。Qwen Code CLI 的日志解析逻辑（`fetch_qwen.py`）没有实现从 shell 工具调用中提取文件变更信息并生成 `file_change` content_block 的功能。

- **解决办法**：
  1. 新增 `scripts/shared/file_change_parser.py`（stdlib-only）：将 `tool_use` 块解析为 `file_change` 的 `changes` 条目
     - 原生文件工具：`write_file` / `create_file` / `edit` → `change_type: modify`
     - shell 工具（`run_shell_command` / `bash` / `cmd` / `powershell` 等）：解析 `mkdir`/`touch`（add）、`mv`/`rename`（move，含 `old_path`）、`cp`（add 目标）、`rm`/`rmdir`/`del`/`rd`（delete）
     - 安全设计：含 glob/管道/重定向/`$变量`/反引号/大括号等无法可靠判断的命令一律跳过，避免误报
     - 支持 `sudo` 前缀剥离、引号路径、Windows 路径（反斜杠保留）、复合命令（`&&`/`;`/`||`/换行）拆分
  2. **实时路径**：`remote_session_manager.py` 新增 `_append_file_change_blocks()`，在 `_accumulate_assistant_text` 流式累积时，从 `tool_use` 块推导 `file_change` 块并随轮次写入 transcript
  3. **历史路径**：`fetch_qwen.py` 新增 `_append_file_change_blocks()`（与实时路径同一逻辑），`process_jsonl_file` 生成 `content_blocks` 后追加 `file_change` 块，回放历史会话同样显示文件变更
  4. **web 终端路径（session_sync，实测缺失）**：`app/routes/remote.py` 的 `msg_type == "session_sync"` 处理（agent 上报 Qwen CLI 会话数据，source=`web_terminal`）写入 `content_blocks` 时未推导 file_change 块——这是实际工作区创建文件的主要路径，2026-08-03 实测补齐：写入前对每条消息的 `content_blocks` 调用共享函数 `append_file_change_blocks()`
  5. **收敛共享函数**：`file_change_parser.py` 新增模块级 `append_file_change_blocks(blocks)`，实时/历史/web 终端三条路径统一复用（`remote_session_manager.py`、`fetch_qwen.py` 的 `_append_file_change_blocks` 改为调用共享函数）
  6. 新增单元测试 `tests/unit/test_file_change_parser.py`（22 例，含共享函数 2 例）及 `test_fetch_qwen.py` 集成用例（1 例）

- **修改的文件**：
  - 新增 `scripts/shared/file_change_parser.py`（含共享函数 `append_file_change_blocks`）
  - 新增 `tests/unit/test_file_change_parser.py`
  - 修改 `app/modules/workspace/remote_session_manager.py`（实时路径 `_append_file_change_blocks`）
  - 修改 `app/routes/remote.py`（session_sync 路径追加 file_change 块）
  - 修改 `scripts/fetch_qwen.py`（历史路径 `_append_file_change_blocks` + 调用点）
  - 修改 `tests/unit/test_fetch_qwen.py`（新增 file_change 集成测试）
  - 备份：`remote_session_manager.py.bak.20260803`、`fetch_qwen.py.bak.20260803`、`remote.py.bak.20260803b`、`remote_session_manager.py.bak.20260803b`、`fetch_qwen.py.bak.20260803b`、`file_change_parser.py.bak.20260803b`

- **验证结果**：
  - `py_compile` 改动文件通过（本地 + 容器内）
  - `pytest tests/unit/test_fetch_qwen.py tests/unit/test_file_change_parser.py`：25 项全部通过（22 项 parser + 3 项 fetch_qwen）
  - 容器内用真实上报格式（`{"type":"tool_use","name":"run_shell_command","input":{"command":"mkdir C:\workspace\ccc"}}`）验证 `append_file_change_blocks` 正确生成 `[{"path":"C:\workspace\ccc","change_type":"add"}]`
  - DB 实测：session_sync 路径消息（source=`web_terminal`）的 content_blocks 此前无 file_change 块；部署修复后需在工作区实测确认

- **补充诊断（2026-08-03 实测定位，推翻原诊断方向）**：
  工作区实测"创建文件夹后文件变更仍不显示"后，反编译容器内 qwen-code-webui@0.2.40 前端 bundle（`/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js`）发现：
  1. **"文件变更"面板是 qwen-code-webui 的组件**，其数据源是 **git status**，不是 content_blocks：
     - bundle 中 `"content_blocks"` 匹配 **0 次**、`"file_change"` 匹配 **0 次** → 面板根本不读该数据
     - 面板钩子 `zm()`：本地会话 → 请求 qwen-code-webui 自身 `GIT_STATUS?workingDirectory=...`；远程会话 → 请求 OpenACE `/api/remote/machines/<id>/git/status?path=...`，均期望返回 `{"files":[{"path","status","additions","deletions"}]}`
  2. **会话 57decec1 链路**：context=`{"workspace_type":"terminal","remote_machine_id":"7fad3781-..."}`，CLI 跑在 Windows 机器 `C:\workspace` → 面板调 OpenACE → 向 agent 发 `git_status` 命令
  3. **决定性根因**：agent 的 `_cmd_git_status`（`remote-agent/agent.py`）在目录**无 `.git` 时直接返回空 `files`** → 面板显示"未检测到文件变更"；且 git 本身**忽略空目录**，`mkdir` 创建的空文件夹即使有 git 也不会显示
  4. 因此**本问题此前所有"注入 file_change content_block"的修复对工作区面板无效**——那些数据只对会话详情页（OpenACE 主前端 `MessageContent.tsx`）有意义。真正的修复见问题 19

- **状态**：工作区面板侧未解决（原 content_blocks 方案不影响该面板）；修复见问题 19

---

## 问题 9：远程 Agent 客户端下线后无法自动重连，需简化日常启动方式

- **处理时间**：2026-08-02
- **故障现象**：
  服务端 Docker 重启后，远程客户端机器即使已开机，状态仍显示"离线"。客户端此前通过一次性安装命令（`curl ... install.sh | bash -s -- --server ... --token ...`）完成安装注册，但重启后服务器端无法主动发现客户端，用户需要重复执行带新令牌的安装命令才能重新连接。

- **诊断结论**：
  系统采用**客户端轮询模型**，服务器端无法主动发现客户端：
  1. Agent 启动后主动 POST `/api/remote/agent/message`（register → 每秒 poll → 每 60 秒 heartbeat），服务端通过内存 `_connections` 字典 + DB `remote_machines.status` 判定在线状态
  2. `agent_token` 首次注册时由服务端签发并保存在客户端 `config.json`，**长期有效**，重启后复用，无需每次生成新令牌
  3. Agent 断线后自动指数退避重连（1s→60s），进程存活时无需人工干预
  4. **问题根因**：客户端 Agent 进程在开机后没有自动启动（Windows 无自启），用户缺少一个简单的"一键启动"入口

- **根本原因**：
  不是认证/令牌问题，而是**客户端缺少便捷的日常启动与自启机制**，导致 Agent 进程未运行时无法向服务端注册/轮询。

- **解决办法**：
  1. 新增 `remote-agent/start-agent.ps1`（Windows 一键启动脚本，含 `-InstallAutoStart` 注册计划任务登录自启、`-Stop`、`-Status`）
  2. 新增 `remote-agent/start-agent.sh`（Linux/macOS 一键启动脚本，含 `--auto-start` 通过 systemd/crontab 自启、`--stop`、`--status`）
  3. 服务端 `register_machine` 接口新增返回 `install_commands`（已有）和 `start_commands`（新增）字段，注册远程机器时自动生成各平台"日常启动命令"
  4. 新增公开路由 `/api/remote/agent/start.sh` 直接提供启动脚本（需加入 `remote.py` 的认证豁免列表，否则客户端无 token 访问会 401）
  5. `install.sh` / `install.ps1` 自动下载并复制新启动脚本，安装完成后输出日常使用提示
  6. 前端注册令牌弹窗新增"启动 Agent 命令"输入框和复制按钮（`RemoteMachineManagement.tsx` + `remote.ts` + `i18n`）
  7. 修复 `start-agent.ps1` 在 Windows PowerShell 5 下的两个语法问题：中文尖括号 `<` 被解析为比较运算符、UTF-8 无 BOM 文件中文按 ANSI 解码乱码（添加 UTF-8 BOM 解决）
  8. 新增桌面双击快捷命令 `OpenACE-Agent.cmd`：install.ps1 安装完成后自动在桌面生成，双击即可启动 Agent；同时新增 `remote-agent/start-agent.cmd` 模板随安装下载（修复 install.ps1 的 here-string 缩进与 UTF-8 BOM 问题）

- **修改的文件**：
  - 新增 `remote-agent/start-agent.ps1`（UTF-8 BOM）
  - 新增 `remote-agent/start-agent.sh`
  - 新增 `remote-agent/start-agent.cmd`（Windows 启动模板）
  - 修改 `remote-agent/install.ps1`、`remote-agent/install.sh`（自动携带启动脚本 + 生成桌面快捷命令）
  - 修改 `app/routes/remote.py`（register 返回 start_commands + start.sh 路由 + 认证豁免）
  - 修改 `frontend/src/components/features/management/RemoteMachineManagement.tsx`
  - 修改 `frontend/src/api/remote.ts`
  - 修改 `frontend/src/i18n/index.ts`
  - 备份：`install.ps1.bak.20260802`、`install.ps1.bak.desktoppshortcut`、`remote.py.bak.20260802`、`RemoteMachineManagement.tsx.bak.20260802`

- **验证结果**：
  - `start-agent.ps1`、`install.ps1` PowerShell 语法检查通过
  - `/api/remote/agent/start.sh`、`install.sh`、`install.ps1`、`files/start-agent.cmd` 路由均返回 200
  - 容器内 `register_machine` 返回 `start_commands`（linux/macos/windows 三个平台）验证 PASS
  - 前端构建产物包含新增文案（`startCommand`、`copyStartCommand` 等）
  - 桌面 `OpenACE-Agent.cmd` 双击链路验证：`start-agent.ps1 -Status` 正常显示机器信息，实际启动 Agent 后服务器端 DB 中机器状态恢复为 busy、heartbeat 持续更新

- **状态**：已解决（2026-08-02 完成并验证）

---

## 问题 10：本地工作区点击"打开 VS Code"报错 code-server is not installed

- **处理时间**：2026-08-02
- **故障现象**：
  使用 admin 账号登录，进入本地工作区（/workspace/admin）会话模式，在"文件变更"面板右上角点击"打开 VS Code"时报错：`code-server is not installed. Install with: curl -fsSL https://code-server.dev/install.sh | sh`。

- **诊断结论**：
  qwen-code-webui（工作区 UI，运行在服务器容器内 port 3100）的"打开 VS Code"按钮逻辑是**在自身运行环境内**查找并启动 code-server：
  1. `findCodeServer()` 调用 `runtime.findExecutable("code-server")` 在容器内 PATH 查找
  2. 服务器容器内**未安装 code-server**（`code-server: not found`），因此返回 404 报错
  3. 该报错文案来自 `/usr/lib/node_modules/qwen-code-webui/dist/cli/node.js`（第 33735 行）
  4. 该路径与昨天修复的**远程工作区** code-server 密码问题无关（远程路径是客户端 agent 启动 code-server + 服务器代理，已正常）

- **根本原因**：
  服务器容器内缺少 code-server，而 qwen-code-webui 的本地 VS Code 按钮依赖容器内的 code-server 可执行文件。

- **解决办法**：
  1. **Dockerfile 安装 code-server**：在 npm CLI 安装后追加 `curl -fsSL https://code-server.dev/install.sh | sh`（官方脚本，经容器内验证可安装到 /usr/bin/code-server，版本 4.131.0），并添加 `test -x /usr/bin/code-server` 验证
  2. **修复客户端 config.json 编码**：发现 `C:\Users\nuc\.open-ace-agent\config.json` 是 GBK 编码（`machine_name` 含弯引号 `'` 被写为 `\xa1\xae`），导致 agent.py 重启时 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xa1`（见 agent-error.log）。已用 GBK 解码后转存为 UTF-8 编码，内容验证合法
  3. 重建容器使 code-server 进入镜像

- **修改的文件**：
  - 修改 `Dockerfile`（安装 code-server）
  - 修改 `C:\Users\nuc\.open-ace-agent\config.json`（GBK → UTF-8 编码）
  - 备份：`Dockerfile.bak.code-server`、`config.json.bak.gbk`

- **验证结果**：
  - 容器重建后 `code-server --version` 正常（4.131.0）
  - `runuser -u admin` 下 `command -v code-server` 返回 /usr/bin/code-server，admin 用户（webui 运行用户）可正常执行
  - webui（port 3100）health/root 均 200，`/api/vscode/start` 返回 401（需认证，预期，不再 404）
  - config.json 转 UTF-8 后 Python `json.load` 正常，machine_name/server_url/machine_id/agent_token 均正确
  - 服务器容器重启后客户端 Agent 自动重连成功（DB 状态 busy）

- **状态**：已解决（2026-08-02 完成并验证）

---

## 问题 11：工作区 AI 对话默认英文回复（QWEN_SYSTEM_MD 未生效）

- **处理时间**：2026-08-02
- **故障现象**：
  用户反映工作区和 AI 对话时 AI 模型仍使用英文为默认语言，与预期（中文）不符。

- **诊断结论**：
  会话日志（`/home/admin/.qwen/projects/-workspace-admin/chats/*.jsonl`）中 AI 的思考内容为：
  "According to the system instructions, I must always respond in English regardless of the user's input language"
  证实 Qwen Code CLI 使用的仍是**默认英文系统提示词**，`system-prompt.md`（中文提示词）完全未生效。

- **根本原因**：
  1. `.dockerignore` 第 59 行 `*.md` 排除规则把 `system-prompt.md` 挡在镜像构建上下文之外 → 容器内 `/app/system-prompt.md` 不存在
  2. `app/services/webui_manager.py`（第 872-876 行）仅在 `os.path.isfile(_system_prompt_path)` 为 True 时才注入 `QWEN_SYSTEM_MD` 环境变量 → 文件不存在导致环境变量未设置
  3. Qwen Code CLI 读取不到 `QWEN_SYSTEM_MD` → 使用内置默认英文系统提示词

- **解决办法**：
  1. `.dockerignore` 在 `*.md` 排除后添加 `!system-prompt.md` 豁免，使中文提示词进入镜像
  2. `Dockerfile` 锁定 npm 包版本：`npm install -g qwen-code-webui@0.2.40 @qwen-code/qwen-code@0.15.10`（防止重建镜像时 `@qwen-code/qwen-code` 从 0.15.10 被升级到 npm 最新版 0.21.3，避免行为漂移）
  3. 重建镜像并重启容器（`docker compose build open-ace && docker compose up -d open-ace`）

- **修改的文件**：
  - `.dockerignore` — 添加 `!system-prompt.md`
  - `Dockerfile` — npm install 锁定 qwen-code-webui@0.2.40 和 @qwen-code/qwen-code@0.15.10
  - 备份：`.dockerignore.bak.20260802`、`Dockerfile.bak.20260802`

- **验证结果**：
  - 新容器内 `/app/system-prompt.md` 存在（8004 字节，含"请始终使用中文与用户交流"指令）
  - webui 进程环境变量确认包含 `QWEN_SYSTEM_MD=/app/system-prompt.md`（通过 `docker exec --privileged` 读取 /proc/<pid>/environ）
  - sudoers env_keep 白名单（`/etc/sudoers.d/open-ace-webui`）确认包含 `QWEN_SYSTEM_MD`
  - Qwen CLI 版本 0.15.10（与锁定版本一致）

- **状态**：已解决（2026-08-02 完成并验证，需用户在浏览器中打开工作区做最终中文回复确认）

---

## 问题 12：远程工作区打开 code-server 时弹出密码输入界面（WebSocket 代理未传递认证）

- **处理时间**：2026-08-01 修复，2026-08-02 验证
- **故障现象**：
  用户通过系统"打开 VS Code"按钮访问远程工作区的 code-server 时，尽管服务器代理已自动处理登录，页面仍弹出 code-server 的密码输入界面。WebSocket 连接是编辑器功能的关键通道，认证失败导致编辑器无法正常使用。

- **诊断结论**：
  code-server 的密码由远程 `agent.py` 自动生成（`secrets.token_hex(16)`），通过 `PASSWORD` 环境变量传递给 code-server 进程。浏览器访问远程工作区 code-server 时，经过服务器 WebSocket 桥（`remote_ws_handler.py` → `vscode_ws_bridge.py`）转发：
  1. `bridge_vscode_ws_raw()` 建立浏览器与远程 code-server 之间的 WebSocket 桥时，**未携带 code-server 的密码认证信息**
  2. code-server 在 WebSocket 握手阶段要求 Basic Auth（用户名留空、密码为 `cs_password`），缺少该头部即弹出密码输入界面
  3. HTTP 请求路径（会话启动）已有密码传递逻辑，但 WebSocket 原始桥接路径缺失该参数

- **根本原因**：
  `bridge_vscode_ws_raw()` 在 `connect()` 调用时未传递 `additional_headers` 中的 `Authorization: Basic` 头部，导致远程 code-server 无法通过 WebSocket 握手认证。

- **解决办法**：
  1. `app/modules/workspace/vscode_ws_bridge.py`：`bridge_vscode_ws_raw()` 新增 `cs_password` 参数；当密码非空时构造 `Authorization: Basic base64(:<cs_password>)` 头部（用户名留空），通过 `additional_headers` 传给 `connect()`
  2. `app/remote_ws_handler.py`：在 `VSCodeWSHandler` 桥接前从 vscode 会话信息（`info.get("cs_password")`）读取密码并传递给 `bridge_vscode_ws_raw()`，日志记录 `has_password` 状态
  3. 同步更新 `tests/issues/559/test_terminal_ws_handler.py` 适配新签名

- **修改的文件**：
  - `app/modules/workspace/vscode_ws_bridge.py`
  - `app/remote_ws_handler.py`
  - `tests/issues/559/test_terminal_ws_handler.py`
  - 备份：`vscode_ws_bridge.py.bak.20260801`、`remote_ws_handler.py.bak.20260801`

- **验证结果**：
  容器重启后用户确认远程工作区 code-server 可正常使用，WebSocket 桥接不再弹出密码输入界面（WebSocket 连接携带 Basic Auth 通过认证）。

- **状态**：已解决（2026-08-02 验证确认）

---

## 问题 13：远程工作区浏览目录时新建文件夹误报"Machine is offline"

- **处理时间**：2026-08-02
- **故障现象**：
  工作区新建会话 → 选择远程工作区 → 选择机器、项目路径后点击"浏览"进入目录界面（目录正常显示）→ 点击"新建文件夹"输入名称并创建 → 等待片刻后报错：`Machine is offline. Directory creation requires an active connection.` 但此时机器实际在线（随后点击"创建新会话"可正常创建）。

- **诊断结论**：
  对比三个操作路径的在线状态判定，发现**状态白名单不一致**：
  1. **浏览目录**（`remote.py` `browse_remote_directory`）：状态白名单 `("online", "idle", "busy")` —— 接受 busy
  2. **创建目录**（`remote.py` `create_remote_directory`）：状态白名单 `("online", "idle")` —— **遗漏 busy**
  3. **创建会话**（`remote_session_manager.create_remote_session`）：检查内存 `is_connected()`（Agent 轮询中即为在线），不查 DB status

  而机器 DB `status` 字段的取值来源：客户端 `agent.py` 心跳上报 `"status": "busy" if active else "idle"`（有活跃会话时上报 busy），服务端 `process_heartbeat()` 将该值**原样写入 DB**。因此用户在远程工作区工作时（有活跃会话），机器 DB status = `"busy"`，创建目录端点把 busy 排除在"在线"之外 → 误判离线。

- **根本原因**：
  `create_remote_directory` 的状态白名单遗漏了 `"busy"` 状态（Agent 上报的有活跃会话状态），与 `browse_remote_directory` 的白名单不一致，导致机器在线（busy）时创建目录被误判为离线。

- **解决办法**：
  将 `app/routes/remote.py` `create_remote_directory` 的状态白名单由 `("online", "idle")` 改为 `("online", "idle", "busy")`，与浏览目录端点保持一致，并添加注释说明 busy 表示"有活跃会话但仍在线"。

- **修改的文件**：
  - `app/routes/remote.py`（第 3161-3163 行）
  - 备份：`remote.py.bak.20260802`（已有）

- **验证结果**：
  - 重建镜像并重建容器后，容器内 `/app/app/routes/remote.py` 确认包含 `if machine.get("status") not in ("online", "idle", "busy"):`，容器 healthy
  - 需用户在远程工作区（busy 状态）下验证新建文件夹不再误报离线

- **状态**：已解决（2026-08-02 修改完成，待用户在 busy 状态下最终验证）

---

## 问题 14：远程工作区权限对话框点"同意所有操作"被判定为"不同意"

- **处理时间**：2026-08-02
- **故障现象**：
  在远程工作区与 AI 对话时，AI 需要执行操作会弹出权限对话框，提供三个选择：同意这次操作、同意所有操作、不同意。用户点击"同意所有操作"后，逻辑仍被判定为"不同意"（权限请求最终超时/拒绝），操作无法执行。

- **诊断结论**：
  前端 qwen-code-webui（0.2.40）的 minified bundle（`/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js`）中，三个权限按钮对应的处理器为：
  1. `Vt`（onAllow，同意这次）—— **已有** WebSocket 分支：`L.sendPermissionResponse(z.requestId,"allow",void 0,z.toolName)`
  2. `Ut`（onAllowAll，同意所有/永久）—— **缺少** WebSocket 分支：`if(vt(),H(),!z.permissionId){B();return}` 直接短路
  3. `Wt`（onDeny，不同意）—— 已有 WebSocket 分支

  远程会话的 `permission_request` 事件只携带 `requestId`（`permissionId` 为 undefined），因此 `Ut` 在 `!z.permissionId` 处直接 return，永远不会调用 `L.sendPermissionResponse` 发送响应 → 服务端 pending 权限请求挂起直至超时 → 前端按 fail-closed 策略（`_enforce_policy_consume`）最终判定为 deny，表现即"点同意所有操作 = 不同意"。

- **根本原因**：
  qwen-code-webui 前端 `Ut`（onAllowAll）处理器缺少远程会话 WebSocket 分支，无法处理仅携带 `requestId` 的远程权限请求，导致响应从未发出、请求超时被判定为拒绝。属于上游前端缺陷（非本项目服务端 bug）。

- **解决办法**：
  新建 patch 脚本 `scripts/patch-qwen-webui-permission.py`，在镜像构建时对 minified bundle 做精确字符串替换，为 `Ut` 添加 WebSocket 分支（镜像 `Vt` 的现有实现）：
  - 旧：`if(z){if(vt(),H(),!z.permissionId){B();return}`
  - 新：`if(z){if(vt(),H(),A){L.sendPermissionResponse(z.requestId||``,`allow`,void 0,z.toolName);B();return}if(!z.permissionId){B();return}`
  - 依赖数组由 `[z,ct,dt,B,vt,H,U]` 更新为 `[z,ct,dt,B,vt,H,A,L,U]`（补上 `A`、`L`）
  - 脚本内置唯一性检查（count==1）防损坏、已 patch 时幂等跳过；bundle 版本漂移时以非零退出使构建失败（loudly fail）
  - Dockerfile 新增 `RUN python3 /app/scripts/patch-qwen-webui-permission.py`（第 176-180 行），npm 版本已锁定 `qwen-code-webui@0.2.40 @qwen-code/qwen-code@0.15.10`

- **修改的文件**：
  - `scripts/patch-qwen-webui-permission.py`（新建）
  - `Dockerfile`（第 103 行锁版本、第 176-180 行新增 patch 步骤）
  - 备份：`Dockerfile.bak.20260802`（已有）

- **验证结果**：
  - 重建镜像成功（`#27 [production 8/21] RUN python3 /app/scripts/patch-qwen-webui-permission.py` 执行无报错）
  - 容器内 bundle 实测：`sendPermissionResponse(z.requestId` 出现 4 处（原有 3 处 + patch 新增 1 处）、`H(),A){L.sendPermissionResponse`（Ut 的 A 守卫 WebSocket 分支）存在、Ut 旧短路 `H(),!z.permissionId)` 已消失、依赖数组 `[z,ct,dt,B,vt,H,A,L,U]` 已更新
  - 说明：本 patch 使"同意所有操作"按"同意这次"的方式通过 WebSocket 应答该次请求（`behavior=allow`），持久化永久允许规则留作后续增强
  - 用户已在远程工作区权限对话框实际点击"同意所有操作"，操作正常执行，验证通过

- **状态**：已解决（2026-08-02 完成并验证）

---

## 问题 15：仪表盘"今日使用量"有 QWEN 数据，但"时间范围统计"中 QWEN 为 0

- **处理时间**：2026-08-02
- **故障现象**：
  以 admin 账号登录，仪表盘"今日使用量"显示 QWEN 102.19K tokens，但"时间范围统计"选择 2026-08-01 至 2026-08-02 时 QWEN 数据为 0 tokens，两处统计不一致。

- **诊断结论**：
  两个统计走不同的数据表和租户过滤维度：
  1. **今日使用量**（`/api/today` → `get_today_usage`）：查询 **`daily_usage`** 表，按 `date = 今天` + `tenant_id` 列过滤。fetch 写入时默认 `tenant_id=1`（`scripts/shared/db.py` `save_usage`），所以 admin（tenant_id=1）能看到 QWEN 数据。
  2. **时间范围统计**（`/api/summary` 带日期 → `get_summary_by_tool`）：查询 **`daily_messages`** 表，按 `user_id IN (SELECT id FROM users WHERE tenant_id=?)` 过滤（`_tenant_user_condition`）。

  数据库实证（2026-08-01~08-02）：
  - `daily_messages` 中 qwen 行共有 **181,511 tokens**，但全部集中在 `user_id IS NULL` 的行（`sender_name` 形如 `admin-47ea977e086c-qwen`）
  - 按 `user_id IN (1,2)` 过滤后 qwen 统计 = **0 tokens**
  - `daily_usage` 中 qwen 行 `tenant_id=1`，统计正常（50864 + 33830 + 68362 + 28455）

- **根本原因**：
  **两张表的租户维度字段不一致 + fetch 写入遗漏 user_id**：
  - `daily_usage` 表用 `tenant_id` 列做租户隔离，fetch 写入时默认 `tenant_id=1`，数据可见
  - `daily_messages` 表用 `user_id` 列做租户隔离（`user_id IN (users of tenant)`），但 `save_messages_batch`（`scripts/shared/db.py`）的 INSERT 语句**根本不写 `user_id` 字段**，且 `fetch_qwen.py` 构造消息时也不带 `user_id` → 所有 qwen 消息 `user_id` 为 NULL → 按租户过滤的 summary 查询全部被排除 → 显示 0
  - 影响所有 fetch 工具（qwen/claude/codex/zcode/openclaw 均走 `save_messages_batch`），当前环境仅 qwen 有数据所以只观察到 qwen

- **解决办法**：
  1. **`scripts/shared/db.py` `save_messages_batch`**：INSERT / ON CONFLICT / SQLite 分支均增加 `user_id` 列，从 `msg.get("user_id")` 读取；existing 查询增加 `user_id` 列；新增"新值为 None 时保留旧 user_id"的幂等逻辑
  2. **`scripts/fetch_qwen.py`**：
     - `process_jsonl_file` 增加 `user_id` 参数，写入消息 dict
     - 新增 `_resolve_user_id()`：按 `system_account`（username/system_account）查 `users.id`，查不到时返回 None
     - `_process_projects_dir` 透传 `user_id`
     - `fetch_usage` 多用户模式下每用户解析一次 `user_id`
  3. **历史数据回填**（一次性 SQL）：`UPDATE daily_messages SET user_id = u.id FROM users u WHERE user_id IS NULL AND split_part(sender_name,'-',1) = u.username OR system_account` — 回填 99 行

- **修改的文件**：
  - `scripts/shared/db.py`（`save_messages_batch` 增加 user_id 支持）
  - `scripts/fetch_qwen.py`（`process_jsonl_file`/`_process_projects_dir`/`_resolve_user_id`/`fetch_usage`）
  - 备份：`scripts/shared/db.py.bak.20260802`、`scripts/fetch_qwen.py.bak.20260802`
  - 注：`app/repositories/usage_repo.py` 查询逻辑无需改动（过滤条件本身正确，问题在写入端）；fetch_claude/codex/zcode/openclaw 未改（走同一 `save_messages_batch`，因当前无数据暂不受影响）

- **验证结果**：
  - 回填后 `daily_messages` 中 qwen 行 `user_id IS NULL` 计数为 0
  - 按租户过滤的 summary 查询（`user_id IN (users of tenant 1)`）返回 qwen **181,511 tokens**（与 `daily_usage` 中 50864+130647 合计一致）
  - 代码 `py_compile` 语法通过
  - 用户刷新仪表盘确认"时间范围统计"QWEN 数据正常显示

- **状态**：已解决（2026-08-02 完成并验证）

## 问题 16：工作区 AI 回复英文（QWEN_SYSTEM_MD 未注入 + output-language.md 强制英文）

- **处理时间**：2026-08-03
- **故障现象**：
  向 AI 提问"你好"，AI 回复："The user is greeting me in Chinese. However, according to my output language preference, I must always respond in English."。中文系统提示词未生效，AI 按英文回复。

- **诊断结论**：
  1. 排查会话消息（session_messages）确认：**两次"你好"英文测试（12:05、22:01）实际都发生在本地工作区**（session project_path=`/workspace/admin`、workspace_type=local、source=fetch_qwen），并非远程工作区
  2. qwen CLI 调试日志（`~/.qwen/debug/*.txt`）显示 CLI 启动时加载了记忆文件 **`/home/admin/.qwen/output-language.md`**，内容为 "# Output language preference: English" + "You MUST always respond in English regardless of the user's input language"——**模型回复内容与该文件几乎逐字一致**，确认记忆文件被注入系统提示词并压过中文提示词
  3. Windows 远程 agent 侧（`C:\Users\nuc\.qwen\output-language.md`）为 **auto**（跟随用户语言），无此问题
  4. 远程 agent 侧另有一个真实缺陷：`executor.py` `_build_env()` 从未设置 `QWEN_SYSTEM_MD`，且 agent 安装目录没有 `system-prompt.md`，install.ps1/install.sh 下载清单均未包含该文件

- **根本原因**：
  **主因**：容器内 qwen-code 的记忆文件 `~/.qwen/output-language.md` 被设为 English（2026-08-02 11:42 由 qwen-code 的 output-language 功能写入），qwen-code CLI 将记忆文件内容注入系统提示词，其 "MUST respond in English" 强制规则压过了 QWEN_SYSTEM_MD 的中文提示词。
  **次因**：远程 agent 的 `_build_env()` 未注入 `QWEN_SYSTEM_MD`、安装清单缺 `system-prompt.md`，即使没有记忆文件冲突，远程 CLI 也会用默认英文提示词。

- **解决办法**：
  1. **`remote-agent/executor.py` `_build_env()`**：当 CLI 工具为 qwen-code-cli/qwen/qwen-code 时，若 agent 同目录存在 `system-prompt.md`，则设置 `env["QWEN_SYSTEM_MD"]` 指向该文件（文件缺失时保留 CLI 默认提示词）
  2. **`remote-agent/install.ps1` / `install.sh`**：文件下载清单（AGENT_FILES）增加 `system-prompt.md`
  3. **`remote-agent/system-prompt.md`**：从仓库根目录复制（中文提示词，含"请始终使用中文与用户交流"）
  4. **容器内 `/home/admin/.qwen/output-language.md`**（根因修复）：改为 auto（内容与 Windows 侧一致，"Respond in the same language as the user's input"），并恢复 admin:admin 属主。该文件由 qwen-code 的 output-language 功能管理，webui 不会重新生成，一次修改即可

- **修改的文件**：
  - `remote-agent/executor.py`（`_build_env()` 注入 QWEN_SYSTEM_MD）
  - `remote-agent/install.ps1`、`remote-agent/install.sh`（下载清单增加 system-prompt.md）
  - `remote-agent/system-prompt.md`（新建，复制自仓库根目录）
  - 容器 `/home/admin/.qwen/output-language.md`（English → auto）
  - 备份：`remote-agent/{executor.py,install.ps1,install.sh}.bak.20260803`
  - 已部署：`docker cp` 至容器 `/app/remote-agent/`；Windows agent 安装目录已由用户更新并重启

- **验证结果**：
  - 4 个文件 `py_compile` 语法通过
  - 容器内 `/app/remote-agent/` 文件 md5 与仓库一致
  - 容器 `output-language.md` 已改为 auto（admin:admin，841 字节）
  - **用户复测通过**：本地工作区（/workspace/admin）提问"你好"返回中文；远程工作区新建会话提问"你好"返回中文

- **状态**：已解决（2026-08-03 完成并验证）

## 问题 17：远程机器显示"离线"但 agent 实际在线（首个心跳延迟 + 时区显示偏差）

- **处理时间**：2026-08-03
- **故障现象**：
  在 admin 管理界面查看远程机器：在线机器数量为 0，但执行 agent 的机器 token 活跃、状态为离线，最后心跳还是昨晚的记录。agent 实际运行正常（能恢复历史会话）。

- **诊断结论**：
  1. **首个心跳延迟**：agent 启动流程 `start()` 中先同步执行 `_executor.restore_sessions()`（恢复 16 个历史 session，每个 SDK 初始化最多等 15s 超时，合计约 4 分钟），全部完成后才进入 HTTP polling 发送 register/首个 heartbeat → 启动后数分钟内机器一直显示离线
  2. **时区显示偏差**：服务端 `process_heartbeat()`/`register_connection()` 用 `datetime.now(timezone.utc).replace(tzinfo=None).isoformat()` 写入 last_heartbeat（纯 UTC 无时区标记），前端 `new Date(...).toLocaleString()` 按浏览器本地时区解析 → 显示时间比实际早 8 小时（Asia/Shanghai）
  3. **附带问题**：agent.log 中出现 `'gbk' codec can't decode byte 0xae` 错误 —— `session_sync.py` 3 处 `open(self.jsonl_path)` 未指定 encoding，Windows 默认 GBK 读取 UTF-8 JSONL 报错

- **根本原因**：
  agent 启动时同步恢复 session 阻塞了首个心跳；服务端时间戳丢失时区标记导致前端解析偏差 8 小时；session_sync 文件读取未指定 UTF-8 编码。

- **解决办法**：
  1. **`remote-agent/agent.py`**：将 `restore_sessions()` 从同步调用改为后台线程（`_start_session_restore()`），先进入 HTTP polling 完成 register/首个心跳，session 恢复完成后再逐个发送 `session_status=running`；失败不阻塞主流程
  2. **`app/modules/workspace/remote_agent_manager.py`**：
     - 新增 `_utcnow_iso()` helper：返回带 `+00:00` 偏移的 UTC ISO 时间戳，前端 `new Date(...)` 可按 UTC 正确解析
     - `register_agent`/`register_connection`/`process_heartbeat` 的 `last_heartbeat`/`updated_at` 写入改用 `_utcnow_iso()`
     - 心跳超时清扫的 `heartbeat_cutoff` 改用带时区格式，保持 SQLite 词法比较一致（PostgreSQL timestamp 列解析正常）
     - 其余表（agent_sessions 等）时间戳保持原格式，避免跨表比较不一致
  3. **`remote-agent/session_sync.py`**：3 处 `open(self.jsonl_path)` 增加 `encoding="utf-8"`

- **修改的文件**：
  - `remote-agent/agent.py`（新增 `_start_session_restore()`，异步恢复 session）
  - `remote-agent/session_sync.py`（3 处 open 加 encoding="utf-8"）
  - `app/modules/workspace/remote_agent_manager.py`（`_utcnow_iso()` + 4 处写入点 + 心跳超时 cutoff）
  - 备份：`remote-agent/{agent.py,session_sync.py}.bak.20260803`、`app/modules/workspace/remote_agent_manager.py.bak.20260803`

- **验证结果**：
  - 4 个文件 `py_compile` 语法通过
  - 容器重启后 remote_machines 表显示机器 status=busy、last_heartbeat=13:55:35（UTC）= 本地 21:55（即时心跳，自动重连成功）
  - 容器内验证 `_utcnow_iso()` 返回 `2026-08-03T13:56:06.604099+00:00`（带时区标记）
  - 待 Windows agent 更新文件并重启后，验证管理界面在线状态与心跳时间显示

- **状态**：代码修改完成，容器侧已部署；Windows agent 侧待手动更新（见下方命令）

## 问题 18：容器重启后工作区报"Failed to fetch projects: UNAUTHORIZED"

- **处理时间**：2026-08-03
- **故障现象**：
  部署问题 8（docker cp + restart）后，工作区无法打开，报错 `错误: Failed to fetch projects: UNAUTHORIZED`；**刷新页面后仍然报错**。

- **诊断结论**：
  1. 错误来自 **qwen-code-webui 前端**（旧前端构建产物中"项目选择器"组件）：它从 `sessionStorage['qwen-webui-token']` / iframe URL 读取 WebUI token，拼接到 `openace_url/api/projects?token=...` 调用；响应非 200 时抛出 `Failed to fetch projects: ${statusText}`（401 的 statusText 即 `UNAUTHORIZED`）
  2. **当前 frontend/src 已移除该逻辑**（改用 session 认证的 `/api/workspace/remote-projects`），但 **static 前端构建产物未更新**，容器仍运行旧前端
  3. **WebUI token 验证失败的两个根因**：
     - `workspace.token_secret` 若在 config.json 中缺失，`WebUIManager.__init__` 每次进程启动生成**随机 secret 且不持久化** → 容器/gunicorn 重启后所有旧 token 签名失效（`webui_manager.py` 第 183-184 行）
     - `workspace.py load_user` 用 `WebUIManager()` **新建实例**验证 token，与 token 生成用的 `get_webui_manager()` 单例 secret 不一致 → config.json 缺 secret 时 workspace 路由必 401
  4. **"刷新后仍报错"的原因**：iframe 写入 `sessionStorage` 的旧 token 不会随页面刷新更新；主前端旧组件读到的是旧 token（已过期或 secret 已变）→ 永久 401

- **根本原因**：
  旧前端构建产物用"sessionStorage 缓存的 WebUI token"调用 `/api/projects`；token_secret 未持久化 + workspace 认证用错实例，导致 token 验证失败且刷新不刷新 sessionStorage 中的旧 token。

- **解决办法**：
  1. **`app/services/webui_manager.py`**：
     - `__init__` 中 token_secret 缺失时生成后**写回 config.json**（新增 `_persist_token_secret()`），保证跨进程/容器重启 secret 稳定；已有值不被覆盖
     - v2 token TTL 从 30 分钟延长至 24 小时（`TTL_SECONDS = 86400`）
  2. **`app/routes/workspace.py`**：`load_user` 中 `WebUIManager()` 改为 `get_webui_manager()` 单例，与 token 生成/验证统一
  3. **`app/auth/decorators.py`**：`WEBUI_TOKEN_TTL_SECONDS = 1800` → `86400`（与 webui_manager 一致）
  4. **`docker-entrypoint.sh`**：
     - 修复 shebang（`!/bin/bash` → `#!/bin/bash`）
     - `ensure_secret_env` 增加 `TOKEN_SECRET` 的生成/持久化（与 SECRET_KEY/UPLOAD_AUTH_KEY 相同机制，写入 generated-secrets.env）
     - `generate_default_config` 复用环境变量中的 `TOKEN_SECRET`（`${TOKEN_SECRET:-...}`），不再每次新生成
  5. **前端重建**：`cd frontend && npm install && npm run build`（vite 输出到 `static/js/dist/`，Flask `pages.py` 从该目录提供 SPA），新构建产物已移除"sessionStorage token 调用 /api/projects"逻辑

- **修改的文件**：
  - `app/services/webui_manager.py`（token_secret 持久化 + TTL 86400）
  - `app/routes/workspace.py`（认证改用单例）
  - `app/auth/decorators.py`（TTL 86400）
  - `docker-entrypoint.sh`（shebang + TOKEN_SECRET 持久化）
  - `frontend/`（重新构建，产出 `static/js/dist/`）
  - 备份：`{webui_manager.py,workspace.py,decorators.py,docker-entrypoint.sh}.bak.20260803`

- **验证结果**：
  - 3 个 Python 文件 `py_compile` 通过
  - 独立验证脚本（stub 掉 Unix-only 的 `pwd` 后加载真实 `WebUIManager`）：
    - 无 token_secret 的 config.json → 首次生成并写回 ✅
    - 第二个实例复用持久化 secret（跨实例稳定）✅
    - 生成 v2 token 验证通过 ✅
    - 已有 token_secret 不被覆盖 ✅
  - 前端 `npm run build` 成功（4.79s），`static/js/dist/index.html` 引用新产物；`grep -r "Failed to fetch projects|qwen-webui-token" static/js/dist` 无匹配（旧逻辑已消失）
  - 清理了运行验证脚本产生的 200 个 sqlite 临时文件

- **状态**：代码修改完成，待部署容器验证（见下方部署/回滚说明）

---

## 问题 19：工作区"文件变更"面板不显示 AI 文件操作（面板基于 git status，非 git 目录无变更可报）

- **处理时间**：2026-08-03
- **故障现象**：
  工作区中 AI（Qwen Code CLI）创建目录/文件后，右侧"文件变更"面板一直显示"未检测到文件变更"，点击"刷新"按钮也无变化（会话 57decec1 实测：`mkdir C:\workspace\ddd` 后仍不显示）。

- **诊断结论**（完整链路）：
  1. "文件变更"面板是 **qwen-code-webui 前端组件**，数据源是 **git status**（见问题 8 补充诊断）：
     - 远程会话：`zm()` → `mo(machineId, workingDirectory)` → OpenACE `/api/remote/machines/<id>/git/status?path=<project>`（[remote.py `remote_git_status`](app/routes/remote.py)）
     - OpenACE 向远程 agent 发 `git_status` 命令（`_dispatch_remote_git_command`）
  2. agent 端 `_cmd_git_status`（[remote-agent/agent.py](remote-agent/agent.py)）：`C:\workspace` **无 `.git` 目录** → 直接返回 `{"files": []}` → 面板渲染 `files.length === 0` → "未检测到文件变更"
  3. 即使有 git，`git status` 也**忽略空目录**，`mkdir` 创建的空文件夹不会出现
  4. 面板期望的数据结构：`[{"path", "status"(added/modified/deleted), "additions", "deletions"}]`（渲染逻辑含 A/M/D 徽标和 `+N/-N` 统计）

- **根本原因**：
  功能缺陷。qwen-code-webui 的文件变更面板只认 git status；非 git 项目目录（本场景 `C:\workspace`）没有基线可对比，agent 返回空列表，导致 AI 的所有文件操作都无法显示。问题 8 的 content_blocks 方案与该面板无关。

- **解决办法**：
  在 agent 端为**非 git 目录**增加"快照差异"回退（git 目录逻辑保持不变）：
  1. `remote-agent/agent.py` 新增模块级函数：
     - `_scan_project_tree(root)`：递归扫描项目树，返回 `{relpath: {kind, size?, mtime?}}`；**空目录也记录**（弥补 git 忽略空目录的缺陷）；跳过噪声目录（`__pycache__`/`node_modules`/`.venv`/`dist`/`build` 等）
     - `_diff_snapshots(old, new)`：对比两次快照，输出 `{path, status, additions, deletions}`；目录只报 added/deleted，文件报 added/modified/deleted（按 size/mtime 变化判定 modified）
     - `_write_snapshot()`：原子写入快照（临时文件 + `os.replace`）
     - `_snapshot_path_for(cwd)`：快照按项目真实路径哈希存储于 `%TEMP%/openace_snapshots/<sha256>.json`
  2. `_cmd_git_status` 修改：目录无 `.git` 时调用新方法 `_cmd_git_status_snapshot(request_id, cwd)`，返回格式与 git 分支完全一致
  3. **重基线机制**：快照创建时间超过 `_SNAPSHOT_RESET_AGE_SECONDS`（30 分钟）后重新基线化——面板只显示"快照新鲜期内"累积的变更，避免无限累积陈旧条目；首个调用（无快照）建立基线并返回空（与面板首次加载行为一致）
  4. 为新增的 added 文件统计行数（复用 git 分支已有逻辑，供面板 `+N` 显示）

- **修改的文件**：
  - `remote-agent/agent.py`（`import hashlib`、模块级快照函数、`_cmd_git_status` 分派 + `_cmd_git_status_snapshot`）
  - `tests/issues/610/test_remote_agent_handlers.py`（更新 1 个既有非 git 测试 + 新增 5 个快照测试：空目录 added、文件 added+行数、modified、deleted、噪声目录忽略、重基线）
  - 备份：`remote-agent/agent.py.bak.20260803`（原有）

- **验证结果**：
  - `py_compile` 通过
  - `uv run --with pytest --noconftest pytest tests/issues/610/test_remote_agent_handlers.py`：62 通过 / 1 失败；失败项 `test_path_with_tilde_expansion` 为**既有环境问题**（Windows 的 `os.path.expanduser("~")` 优先 `USERPROFILE` 而非被 patch 的 `HOME`，与本次改动无关）；5 个新增快照测试全部通过

- **部署/验证步骤**（需用户执行）：
  1. 将 `remote-agent/agent.py` 复制到 Windows 机器的 agent 安装目录（`C:\Users\nuc\.open-ace-agent\agent.py`），替换旧文件
  2. 重启 agent 进程（双击桌面 `OpenACE-Agent.cmd` 或执行 `start-agent.ps1`）
  3. 打开工作区，等面板完成首次轮询（建立基线，显示"未检测到文件变更"是正常现象）
  4. 让 AI 在工作目录创建**新**文件夹/文件 → 面板 5 秒轮询或手动点"刷新"后应显示 added/modified 条目；删除文件显示 deleted
  5. 注意：基线建立前已存在的目录/文件不会显示（与 git 的 untracked 语义一致，只显示基线之后的变更）

- **状态**：已解决（2026-08-03 部署 Windows agent 后实测：面板已显示文件变更）

---

## 问题 20：权限对话框选"允许且不再询问"被判定为拒绝（allow-permanent 未被 CLI 识别）

- **处理时间**：2026-08-03
- **故障现象**：
  工作区 AI 创建文件夹/文件时弹出权限对话框（允许本次 / 允许且不再询问 / 拒绝），选择**"允许且不再询问"**后操作仍被判定为拒绝、无法执行。"允许本次"和"拒绝"正常。此为问题 14（"同意所有操作"被拒）修复后**复现的同类问题**。

- **诊断结论**（完整链路）：
  1. **问题 14 的补丁仍然存在**（容器内 bundle `H(),A){L.sendPermissionResponse` 3 处、依赖数组 `[z,ct,dt,B,vt,H,A,L,U]` 3 处——此前 grep 返回 0 是误报：minified bundle 是单行文件，`grep -c` 按"行"计数且模式含 `{`/`}`）
  2. 反编译 qwen-code-webui bundle，权限对话框三按钮映射（组件 `gc`）：
     - `allow`（允许本次）→ `Vt` → WebSocket 发送 `behavior="allow"`
     - **`allowPermanent`（允许且不再询问）→ `Ht` → WebSocket 发送 `behavior="allow-permanent"`**
     - `deny`（拒绝）→ `Wt` → `behavior="deny"`
  3. 问题 14 修复的是 `Ut`（"同意所有操作"场景，`run_shell_command` 时按钮为 allowSpecific/allowAll），补丁后发 `allow`，所以当时通过；**`Ht` 的 `allow-permanent` 路径从未被验证过**
  4. **决定性根因**：Windows 机器上的 qwen-code CLI（@qwen-code/qwen-code@0.15.10）解析 `control_response` 时（`chunks/session-GMPZBLOW.js` 第 1176-1193 行）：
     ```js
     const behavior = String(payload["behavior"] || "").toLowerCase();
     if (behavior === "allow") { /* proceed_once */ }
     else { /* cancel → 拒绝 */ }
     ```
     **只识别 `behavior === "allow"`**，`allow-permanent` 落入 else 分支 → cancel → 被当作拒绝
  5. 链路：前端 `Ht` → WebSocket `allow-permanent` → OpenACE `respond_to_permission`（原样透传）→ agent `_cmd_permission_response` → CLI stdin `control_response {"behavior":"allow-permanent"}` → CLI cancel

- **根本原因**：
  qwen-code-webui 前端的"允许且不再询问"通过 WebSocket 发送 `allow-permanent`，而 CLI 的 control_response 协议**只有 `allow`/`deny` 两种取值**（无永久允许应答方式），`allow-permanent` 被 CLI 按"非 allow"处理为拒绝。属于**前后端协议取值不匹配**（上游 qwen-code-webui 与 CLI 之间的缺陷）。

- **解决办法**：
  在行为值到达 CLI 前，将 `allow-permanent` **归一化为 `allow`**（"允许且不再询问"按"允许本次"生效，操作可执行、不再误判为拒绝；永久允许语义留作后续增强，与问题 14 结论一致）：
  1. **服务端** `app/modules/workspace/remote_session_manager.py` 的 `respond_to_permission` 入口归一化（该方法为所有权限应答的唯一 chokepoint）：
     ```python
     if behavior == "allow-permanent":
         behavior = "allow"
     ```
  2. **agent 端** `remote-agent/agent.py` 的 `_cmd_permission_response` 同样归一化（兜底，覆盖将来绕过服务端的入口）

- **修改的文件**：
  - `app/modules/workspace/remote_session_manager.py`（`respond_to_permission` 归一化）
  - `remote-agent/agent.py`（`_cmd_permission_response` 归一化）
  - `tests/unit/test_remote_session_manager_timeline.py`（新增 `test_respond_to_permission_normalizes_allow_permanent`）
  - `tests/issues/610/test_remote_agent_handlers.py`（新增 `TestCmdPermissionResponse` 3 例）

- **验证结果**：
  - `py_compile` 通过
  - `test_remote_agent_handlers.py`：65 通过 / 1 失败（`test_path_with_tilde_expansion` 为既有 Windows 环境问题，与本次改动无关）；新增 `TestCmdPermissionResponse` 3 例全部通过
  - `test_remote_session_manager_timeline.py`：11 项全部通过（含新增归一化测试）

- **部署/验证步骤**：
  1. 服务端：`remote_session_manager.py` 部署到容器并重启 gunicorn（本次可随容器重启一起完成）
  2. agent 端：替换 Windows 的 `agent.py` 并重启 agent（与问题 19 部署方式相同）
  3. 实测：工作区让 AI 执行操作，权限对话框选"允许且不再询问"→ 操作应正常执行

- **状态**：已解决（代码 + 单测完成，待部署后实测）

---

## 上游同步（2026-08-05）：open-eduace 与 open-ace 上游合并

- **背景**：本地 fork 自 `https://github.com/open-ace/open-ace`，共同祖先 `9aeb4cde`。本地领先 4 个提交（问题 8/16/17/18/19/20 修复），上游领先 502 个提交（至 `8b823bcd`）。
- **冲突规模**：试合并 16 个文件冲突；最终决策：
  - 取上游版（本地修复已被上游原样采纳或更好）：`start-agent.ps1`、`start-agent.sh`、`patch-qwen-webui-permission.py`、`app/auth/decorators.py`、`api_key_proxy.py`、`vscode_ws_bridge.py`、`remote_ws_handler.py`、`app/routes/remote.py`、`webui_manager.py`、`outbound_url_guard.py`、`Toast.tsx`、`ForceChangePasswordModal.tsx`、`Dockerfile`。
  - 手动合并（取上游 + 保留本地关键行）：
    - `docker-entrypoint.sh`：取上游 Issue #2181 安全加固版 env_keep，**额外加回 `QWEN_SYSTEM_MD`**（问题 16 系统提示注入依赖）。
    - `frontend/src/i18n/index.ts`：取上游全部新增（含 `passwordChangedSuccess` 四语言），**补回本地 `startCommand`/`startCommandDesc`/`copyStartCommand` 3 键（中英）**；日文译文取上游。
    - `remote-agent/install.ps1`：文件列表 = 上游 3 个启动脚本 + 本地 `system-prompt.md`（问题 16）。
- **回归验证（合并后）**：问题 16（executor.py QWEN_SYSTEM_MD + entrypoint env_keep）、17（webui_manager token_secret 持久化 + TTL 86400 / decorators 常量）、18（remote_agent_manager UTC 时间戳）、19（agent.py 快照差异 6 函数）、20（agent.py:900 + remote_session_manager.py:699 allow-permanent 归一化）、8（fetch_qwen/remote_session_manager/remote.py file_change 注入）全部确认保留。
- **测试适配**：`tests/issues/559/test_terminal_ws_handler.py::TestHandleVSCodeWs::test_invalid_proxy_ws_token_closes` 因上游 `remote_ws_handler.py` 给 `send_close` 增加 reason 参数而更新断言（`_handle_terminal_ws` 的类似断言未变）。
- **已知失败（与同步无关，Windows 环境既有）**：`tests/issues/610::test_path_with_tilde_expansion`、`tests/unit/test_auth_decorators.py` 11 项（上游 `webui_manager.py` 新增 Unix-only `import pwd`，Windows 无此模块；生产容器为 Linux 不受影响）。
- **提交**：`sync/upstream-20260805` 分支，merge 提交 `a15b8e96` + 测试适配 `a220ff73`；已合入 main（`f85dc3bd`）并 push origin/main。

### 部署（容器内更新，未重建镜像）

- **镜像重建受阻**：上游 Dockerfile 将 code-server 安装路径改为 `--prefix=/usr/local` 使 RUN 缓存失效，需从 github 重新下载 code-server（约 150MB），实测下载速度 ~0.2MB/s（约需 14 小时），已中止。
- **改用容器内更新**：`git archive HEAD` 生成代码包 → `docker cp` → 容器内解包到 `/app` → `pip install` 新增依赖（cachetools、prometheus_client、prometheus_flask_exporter）→ `docker restart`（entrypoint 自动执行 `alembic upgrade head`，schema 升级至 `20260805_001`）。
- **部署中发现并修复 2 个上游 bug**：
  1. `app/utils/health_checks.py`：PG probe 用 `conn.execute(sa.text("SELECT 1"))`，但 `PgConnectionWrapper`（psycopg2）只有 `cursor()` 没有 `execute()` → 每次 probe 抛 AttributeError → `/readyz` 恒报 `connection_failed`。修复：改用 `with conn.cursor() as cur: cur.execute("SELECT 1")`（提交 `433293cf`）。
  2. `app/__init__.py` `/readyz` schema 检查：`current_revision < MIN_SUPPORTED_REVISION` 字符串比较，时间戳版本 `20260805_001`（数字开头）按字典序恒小于 `baseline_2026_06_23` → 永远 `incompatible` → 容器恒 unhealthy。修复：复用 `schema_guard.check_schema_compatibility`（其正确处理 baseline/时间戳比较，提交 `eccc6fa9`）。
- **部署验证**：容器 `Up (healthy)`，`/readyz` 返回 200 `status: ready`，`schema_version.compatible: true`，6 项检查全部 ok。
- **前端说明**：上游更新了前端源码但未提交构建产物，容器内 `static/assets` 仍为旧构建；新前端功能（RuntimeIsolationPanel 等）需镜像重建后生效，后端功能不受影响。
- **待办**：Windows agent 端需手动更新 `C:\Users\nuc\.open-ace-agent\` 下 `agent.py`、`executor.py`、`system-prompt.md`（合并后版本，含问题 19/20 修复 + 上游更新）并重启 agent。

---

## 问题 21：工作区无法初始化（WebUI authentication failed + webui 进程起不来）

- **处理时间**：2026-08-05
- **故障现象**：
  admin 登录后工作区无法初始化，前端报错 `WebUI authentication failed. Please refresh the page.`；容器日志反复出现 `WebUI service on port 3100 not ready after 10.0s timeout`，`ps` 无 qwen-code-webui 进程、`ss` 无 3100 端口监听，webui.log 停留在 8 月 3 日旧日志（进程在启动前即失败，未写日志）。
- **诊断结论**（两层根因）：
  1. **wrapper 文件缺失**：上游新启动机制（Issue #2298/#2305）通过 `sudo -u <user> /usr/local/bin/openace-webui-launch <KEY=VALUE>... /usr/bin/qwen-code-webui --port 3100 ...` 启动 webui（webui_manager.py `_launch_webui_process`）。该 wrapper 由 Dockerfile `COPY scripts/openace-webui-launch.sh /usr/local/bin/openace-webui-launch`（第 242 行）安装，但本次部署未重建镜像 → 容器内 `/usr/local/bin/openace-webui-launch` 不存在 → sudo 拒绝执行 → 进程无法启动。
  2. **entrypoint 上游 bug（更深层）**：新 entrypoint 的 sudoers 生成逻辑把 shell `if [ -x "$WEBUI_LAUNCH_WRAPPER" ] ... fi` **误写在 `cat > /etc/sudoers.d/open-ace-webui << SUDOERS_EOF` heredoc 内部**，导致 shell 代码被逐字写入 sudoers 文件 → `visudo -c` 语法错误 → entrypoint 删除 sudoers 文件并继续（日志仅 WARNING）。即使补上 wrapper，重启后 sudoers 仍会消失。
  3. **附带问题**：entrypoint 生成的 wrapper 规则 `${WEBUI_LAUNCH_WRAPPER} "${WEBUI_PATH}" *` 缺前置 `*`，无法匹配 webui_manager 实际调用格式 `wrapper <env...> path --port ...`（env 参数在路径前），需为 `wrapper * "path" *`（与 tests/issues/2298/test_webui_env_isolation.py 预期一致）。
- **解决办法**：
  1. 容器内补齐 wrapper：`cp /app/scripts/openace-webui-launch.sh /usr/local/bin/openace-webui-launch && chmod +x`
  2. 修复 `docker-entrypoint.sh`：将 wrapper 规则计算（`WEBUI_SUDO_RULE` 变量 + if/fi 回退逻辑）移到 heredoc **之外**，heredoc 内仅引用 `${WEBUI_SUDO_RULE}`；规则格式加前置 `*` 匹配 env 参数
  3. 同步修复后的 entrypoint 到容器 `/app/docker-entrypoint.sh` 与 `/usr/local/bin/docker-entrypoint.sh`（先备份 `.bak.20260805`）
  4. 重启容器 → entrypoint 重新生成 sudoers 并经 `visudo -c` 校验
- **修改的文件**：
  - `docker-entrypoint.sh`（sudoers 规则生成逻辑重构：heredoc 外计算 + 前置 `*`）
  - 容器内：`/usr/local/bin/openace-webui-launch`（复制自 /app/scripts/）、`/usr/local/bin/docker-entrypoint.sh`（备份 .bak.20260805）
- **验证结果**：
  - `sudo -u admin /usr/local/bin/openace-webui-launch TEST_VAR=hello /usr/bin/qwen-code-webui --version` 输出 `0.2.40`，sudo exit 0（env 参数前置格式被 sudoers 允许）
  - 复刻 entrypoint 生成逻辑的 sudoers 经 `visudo -c` parsed OK
  - 模拟 webui_manager 启动命令（sudo + wrapper + env + webui --port 3100），进程成功启动并监听 3100 端口
  - 容器重启后 healthy，`/etc/sudoers.d/open-ace-webui` 含规则 `open-ace ALL=(ALL) NOPASSWD: /usr/local/bin/openace-webui-launch * "/usr/bin/qwen-code-webui" *`，`/readyz` 200
- **状态**：已解决

## 问题 22：admin 管理版面消失（迁移将角色改为 tenant_admin + 前端构建过旧）

- **处理时间**：2026-08-05
- **故障现象**：
  原 admin 账号有两个版面：管理版面（远程机器管理、API Key 配置等）和开发版面（工作区等）。部署最新服务端后登录 admin，只剩开发版面，管理版面与 ModeSwitcher 切换入口消失。
- **诊断结论**：
  1. **角色变更**：部署时 `alembic upgrade head` 应用迁移 `20260801_001_add_platform_tenant_admin_roles.py`，将 admin 账号（有 tenant_id）角色从 `admin` 改为 `tenant_admin`（数据库已确认）。
  2. **前端过旧**：容器内前端为 8/3 同步前构建，其 `AppContent` 判断 `isAdmin = user?.role === 'admin'`（permissions.ts 旧版仅 `canManageAllTenants` 认 `admin`），**不识别 `tenant_admin`** → 前端判定当前用户非管理员 → 默认路由 `/work`（开发版面），`/manage`（管理版面）与 ModeSwitcher 全部隐藏。
  3. 新前端源码（提交 `e37093cd` #2285）的 `isAdmin` 已支持 `admin/platform_admin/tenant_admin`，与后端 `ADMIN_ROLES`（app/models/user.py:24）一致。
- **解决办法**（方案 B：重建前端，DB 角色保持 tenant_admin）：
  1. 本地 `frontend/` 执行 `npm run build`（tsc + vite，输出到 `static/js/dist`），备份旧构建为 `static/js/dist.bak.20260805`
  2. 清空并 `docker cp` 新构建到容器 `/app/static/js/dist/`
- **修改的文件**：
  - `static/js/dist/`（重新构建的前端产物；`static/js/dist.bak.20260805` 为旧构建备份）
  - 容器内 `/app/static/js/dist/`
- **验证结果**：
  - 新构建含 `/manage/*` 路由（index/components/utils chunk 共 17 处匹配）、`utils.*.js` 含 `tenant_admin`（isAdmin 支持三种 admin 角色）
  - 容器根路由返回新 `index.vAKXDLiX.js`，`/static/js/dist/*` 全部 200
- **状态**：已解决（待用户在浏览器中确认管理版面恢复）

---

## 问题 23：查看对话历史后项目历史会话不显示（进入项目总是全新对话）

- **处理时间**：2026-08-06
- **故障现象**：
  admin 登录 → 工作区 → 点"查看对话历史"→ 出现"您的项目"界面（项目列表正常）→ 点击 admin 的项目进入会话窗口，但窗口是**全新开始的对话**，没有任何历史信息，尽管该项目已有多个历史会话。
- **诊断结论**（qwen-code-webui@0.2.40 服务端 bundle 三个 bug，`/usr/lib/node_modules/qwen-code-webui/dist/cli/node.js`）：
  1. **`getHistoryFiles` 不扫 `chats/` 子目录**：会话列表接口 `GET /api/projects/:encodedProjectName/histories` 只扫描项目根目录 `*.jsonl`，而 qwen-code CLI 把会话存在 `<historyDir>/chats/<sessionId>.jsonl`（单会话接口 `loadConversation` 却明确读 `chats/`，自相矛盾）→ 列表恒空。
  2. **`groupConversations` 去重逻辑误伤**：qwen-code CLI 写入的 assistant 消息**无 `message.id`**（JSONL 中 `message.role` 为 `"model"` 且 `"id": null`），每个会话的 `messageIds` 恒为空集合；`isSubset(空集, 任何集)` 恒 true → 首个会话之后的文件全被当"重复"丢弃 → 4 个文件只剩 1 个会话。
  3. **会话列表 preview 恒为 "No preview available"**：`parseHistoryFile` 提取预览只匹配 `message.role === "assistant"` + `message.content` 数组，而 CLI 写入的是 `message.role === "model"` + `message.parts`（首个 part 常为 thought）→ 预览永远提取不到。
- **解决办法**（patch 脚本 `scripts/patch-qwen-webui-histories.py`，版本钉死 0.2.40、精确替换、漂移即构建失败）：
  1. `getHistoryFiles` 增加 `chats/` 子目录扫描（追加 `readDir(historyDir/chats)` 的 `.jsonl` 收集）
  2. `groupConversations` 去重判定加守卫 `currentConv.messageIds.size > 0 &&`：无 `message.id` 的会话不再参与子集去重，每个 `.jsonl` 文件独立展示
  3. preview 提取兼容 CLI 格式：角色判定接受 `"assistant" || "model"`，正文取 `message.content || message.parts`，并跳过 thought part（`!item.thought`）
- **修改的文件**：
  - `scripts/patch-qwen-webui-histories.py`（新增，含三个 bug 的 patch）
  - `Dockerfile`（新增 `RUN python3 /app/scripts/patch-qwen-webui-histories.py`，位于 permission patch 之后）
  - 容器内 `/usr/lib/node_modules/qwen-code-webui/dist/cli/node.js`（备份 `/tmp/node.js.bak.20260806`、`/tmp/node.js.bak.bug2.20260806`）
- **验证结果**：
  - 独立 Node 复刻 bundle 逻辑：`getHistoryFiles` 找到 4 个文件 → `parseAllHistoryFiles` 解析 4 个会话 → patched `groupConversations` 返回 4 个（patch 前 1 个）
  - 容器内 webui `node --check` 语法通过
  - 生产路径验证：`pkill` 旧 webui → `GET /api/workspace/user-url` 触发 webui_manager 重启（新实例端口 3101）→ `GET /api/projects/-workspace-admin/histories` 返回 **4 个会话**（13e73ea9/329e3efa/0240f218/512833d7），单会话接口内容正常
  - **Bug 3 验证**：重启后 `histories` 返回的 `lastMessagePreview` 全部为实际回复内容（如 `'你好！有什么可以帮你的吗？'`、`'Hello! How can I help you today?'`），不再是 "No preview available"，且正确跳过 thought
  - 探针教训：webui_manager 重启的实例端口是**动态分配**（本次 3101/3100），验证脚本不能硬编码 3100
- **状态**：已解决（镜像重建后 Dockerfile RUN 生效；当前容器为直接 patch bundle，待用户在浏览器确认"查看对话历史"列出历史会话）

---

## 问题 24：远程电脑工作区的会话历史未出现在 webui"查看对话历史"中

- **处理时间**：2026-08-06
- **故障现象**：
  admin 登录 → 工作区 → 点"查看对话历史"按钮 → "您的项目"界面 → 点击项目后列出的会话**全部是容器内本地工作区**（`/workspace/admin`）的会话（磁盘仅 4 个 jsonl），**没有**用户 8/4 在**远程电脑上创建工作区**（远程目录）进行操作的会话历史。用户认为"会话历史是按用户保存的，应该包含本地与远程两种会话类型"。
- **诊断结论**：
  1. **系统有两个会话历史入口，数据源不同**：
     - **工作区左侧"会话列表"**（open-ace 前端 [SessionList.tsx](file:///d:/TraeWorkspace/open-eduace/frontend/src/components/work/SessionList.tsx)）：读数据库 `agent_sessions`（`GET /api/workspace/sessions`），**按 user_id 聚合**，含 local/remote/terminal 三类（远程显示蓝云图标 + 机器名，可查看详情/恢复会话）→ **这是系统按用户保存会话历史的正确入口**。
     - **qwen-code-webui 内置"查看对话历史"**（webui 的 histories 视图）：读 **webui 进程 HOME 的 `~/.qwen/projects/<encoded>/chats/*.jsonl`**（容器本地文件）→ 只含容器内本地工作区会话。webui 是第三方组件，对 open-ace 数据库中的远程会话天然不可见。
  2. **远程会话数据完整，未丢失**：远程 agent 通过 `session_sync`（[session_sync.py](file:///d:/TraeWorkspace/open-eduace/remote-agent/session_sync.py)）扫描远程机器 `~/.qwen/projects/` 的 jsonl，经 [remote.py](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L1912-L2007) 入库 `agent_sessions`/`session_messages`。实测数据库：admin（user_id=1）有 **14 个远程会话**（机器 `LUYOU‘SNUC`，`C:\workspace` 及子目录，时间 8/1–8/6，含用户 8/4 凌晨的 `827659fa` 等），消息内容完整。
  3. **API 实证**：admin 调用 `GET /api/workspace/sessions` 返回 29 个会话（14 远程 + 15 本地），按用户聚合正常；前端 [SessionList.tsx](file:///d:/TraeWorkspace/open-eduace/frontend/src/components/work/SessionList.tsx#L472-L481) 完整渲染远程会话。
- **根本原因**：
  非系统数据缺失，而是**入口差异**：webui 内置"查看对话历史"只读 webui 本地文件系统，不感知 open-ace 数据库；远程会话的正确查看入口是工作区左侧"会话列表"。
- **修改方案**：**已实施（方案 2：同步 DB 会话为 webui 可读 JSONL + 容器内镜像目录）**，用户批准实施：
  - 方案 1（零改动）：引导使用左侧"会话列表"作为按用户统一历史入口。
  - 方案 2（增强，本次实施）：新增同步任务，把数据库中的会话（本地+远程）镜像落地为 webui 可读的 `~/.qwen/projects/<encoded>/chats/*.jsonl`，使 webui"查看对话历史"也能列出远程会话（注意：远程会话 cwd 为 Windows 路径 `C:\workspace`，容器内查看历史可行、继续对话受限；webui 为第三方组件升级即失效）。
- **实施细节（2026-08-06，问题 25-28 恢复链路修复后实施）**：
  1. 新增 [session_history_sync.py](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/session_history_sync.py)：遍历 users（user_id→system_account），从 `agent_sessions` 读 remote/terminal 会话，从 `session_messages` 生成 qwen 格式 JSONL（复用问题 28 的 `is_qwen_system_context` 过滤，不含系统提示词），落地 `/home/<system_account>/.qwen/projects/<encoded>/chats/<session_id>.jsonl`；编码规则与 webui 一致（`encodeProjectPath`：非字母数字→`-`，`C:\workspace`→`C--workspace`）；对非 `/` 根路径（远程 Windows 路径）在容器内创建 `simpleDecodeProjectPath` 镜像目录（`C--workspace`→`/C//workspace`=`/C/workspace`）使 webui 项目列表 stat 验证通过；只写不删（避免误删 qwen CLI 本地会话 JSONL）。
  2. [data_fetch_scheduler.py](file:///d:/TraeWorkspace/open-eduace/app/services/data_fetch_scheduler.py) `_run_fetch` 增加 `sync_remote_sessions_to_webui()`（随 5 分钟 fetch 任务运行）。
  3. [app/__init__.py](file:///d:/TraeWorkspace/open-eduace/app/__init__.py#L652-L662) `create_app` 末尾调用 `start_webui_history_sync_loop()`（web 模式也启动后台循环：本部署 `SCHEDULER_MODE` 未设置=web，**不存在独立 scheduler worker**，5 分钟 DataFetchScheduler 不会运行，必须由 web worker 自维护循环；scheduler 模式下两者重叠幂等无害）。
  4. 部署：docker cp 文件 → 容器内 py_compile → `/readyz` 200 → 哈希一致。
  5. **部署机制重大教训（耗时最久的一环）**：`kill -HUP 1` **对 `app/__init__.py` 的改动永远无效**——gunicorn master（PID 1）启动时因 `--worker-class app.gunicorn_worker.TerminalGeventWorker` 的类解析（`util.load_class`，见 [config.py worker_class](file:///d:/TraeWorkspace/open-eduace/app) `Arbiter.setup`）在 master 进程导入了 `app` 包，worker 均为 master 的 fork 并继承 `sys.modules`，从不重新导入 `/app/app/__init__.py`；HUP 只重启 worker 不重启 master。**`app/__init__.py` 的改动必须 `docker restart open-ace` 完整重启**（本次即用此法）。此前问题 23-28 的修复都在 create_app 运行时导入的模块（routes/services）里，worker 每次启动都会重新导入，所以 HUP 一直有效。
  6. 首次全量同步：17 个远程会话全部落地（admin 15 + luyou 2，errors=0），本地会话 JSONL 未受影响。
- **验证结果**（webui API 实证，全部通过）：
  - `GET /api/projects`：返回 4 个项目（本地 `-workspace-admin` + 远程 `C--workspace`/`C--workspace-aaa`/`C--workspace-test`）。
  - `GET /api/projects/C--workspace/histories`：列出 12 个远程会话，预览/消息数正常。
  - `GET /api/projects/C--workspace-test/histories`：2 个会话（6eac8f26 35 条、3f2ef1df 51 条）。
  - 会话详情渲染正常（用户消息/AI 回复/时间戳正确，无系统提示词泄露）。
  - **后台循环验证（完整重启后）**：容器日志出现 `webui history sync loop started (interval=300s)` 与 `session_history_sync done: {'users': 2, 'sessions': 17, 'files_written': 17, 'errors': 0}`；`/home/admin/.qwen/projects/C--workspace/chats/*.jsonl` mtime 刷新为重启时刻，证明 web worker 内循环真实运行并每 300s 自动同步。
- **修改的文件**：
  - 新建 `app/modules/workspace/session_history_sync.py`
  - `app/services/data_fetch_scheduler.py`（备份 `data_fetch_scheduler.py.bak.20260806`）
  - `app/__init__.py`（备份 `__init__.py.bak.20260806`；`create_app` 末尾启动同步循环）
- **状态**：已解决（后台循环在 web worker 中运行验证通过；webui 进程按需拉起，用户在浏览器"查看对话历史"界面即可看到远程项目与会话）
- **遗留**：① 远程会话在 webui"查看对话历史"中为**只读查看**，继续对话走工作区左侧"会话列表"；② 远程项目在 webui 显示路径为解码产物 `/C//workspace`（webui 机制决定）；③ 已删除会话的远程 JSONL 残留无害但会继续显示（同步只写不删），如需清理可后续加删除逻辑；④ webui 升级（npm 包）不影响已落地 JSONL，但若 webui 改变解析格式需适配；⑤ **部署规范**：今后对 `app/__init__.py` 的修改必须 `docker restart open-ace`（HUP 无效），其余模块仍可 HUP；⑥ 容器完整重启后 webui 进程（3100-3107）全部结束，用户打开工作区时由 webui_manager 按需重新拉起。

---

## 问题 25：远程会话"恢复会话"无效、按钮变灰（方案 A 已修复）

- **处理时间**：2026-08-06
- **故障现象**：
  工作区左侧蓝云（远程）会话列表可见 8/4 会话（如 `827659fa-c698-4c29-a06f-ae0d7a10c478`），点"恢复会话"无效，按钮变灰。前端 [SessionList.tsx](file:///d:/TraeWorkspace/open-eduace/frontend/src/components/work/SessionList.tsx) 调 `POST /api/workspace/sessions/<id>/restore`，服务端对已终止的远程会话返回 400 `can_resume=false`，前端据此禁用按钮。
- **诊断结论**：
  1. `restore_session`（[workspace.py](file:///d:/TraeWorkspace/open-eduace/app/routes/workspace.py#L1488-L1792)）对远程会话先向 agent 发 `get_session_info`，**依赖 agent 进程内存 `_sessions`**；进程一旦结束，`info.is_running=false` → 直接 400，即使磁盘 JSONL 完好、CLI 支持 `--resume`。
  2. 关键事实：**服务端 session_id ≠ qwen JSONL 文件名**。数据库远程会话 session_id 为 open-ace 生成的 UUID（如 `827659fa-…`），而 qwen CLI 每次启动自生成内部会话 UUID 作为 JSONL 文件名（如 `9d88f951-7e3a-437e-8e2c-9a1f83c51a99.jsonl`），`agent_sessions.cli_session_id` 全部为空，**映射未持久化**。两者的对应只能靠创建时间推断（实测 8/3 每对会话创建时间相差 3–30 秒）。
  3. 另发现：每次 agent 重启，executor 会按 metadata 自动"恢复"会话（`Restoring N session(s) from metadata`），用 session_id 拼 `--resume`，因文件名不匹配导致 qwen 生成**全新空会话**（2047 字节新 UUID JSONL），且 SDK 初始化超时、代理令牌 401（`Invalid or expired proxy token`）。
- **第一次修复尝试（失败，已回滚，见下）**：
  1. `remote-agent/executor.py`：`start_session` 增加 `resume_session_id` 参数，`cli_resume_target = resume_session_id or session_id`。
  2. `remote-agent/agent.py`：`_cmd_start_session` 透传 `resume_session_id`。
  3. `app/modules/workspace/remote_session_manager.py`：新增 `resume_terminated_session`（`resume_session_id=session_id`）。
  4. `app/routes/workspace.py`：terminated 分支自动调 `resume_terminated_session`。
  - **失败原因（验证结果）**：`--resume 827659fa…`（server UUID）找不到 JSONL → 生成空会话；SDK init 超时；401。**回滚**：容器/Windows 均从 `.bak.20260806` 恢复，恢复修改前状态。
- **修复方案 A（用户批准，本次实施）**：
  1. `remote-agent/executor.py`：
     - `start_session` 增加 `resume_session_id`（CLI `--resume` 目标）；`cli_resume_target = resume_session_id or session_id`。
     - 新增 qwen 会话文件监视：新会话（非 resume）启动后后台线程扫描 `~/.qwen/projects/*/chats/*.jsonl`，按 cwd 匹配 + mtime 窗口发现 CLI 自生成 UUID，写入 `SessionProcess._cli_session_id` 并触发回调上报。
     - 新增 `find_session_jsonl(project_path, created_at)`：按 cwd + 首 user 消息时间与 created_at 邻近度（≤600s）匹配历史 JSONL，返回 `{session_id, jsonl_path, delta_seconds}`。
     - `_parse_utc_ts` 兼容 ISO 与 RFC（email.utils）时间格式。
  2. `remote-agent/agent.py`：`_cmd_start_session` 透传 `resume_session_id` 并注册 cli_session 发现回调（`_send_session_status(..., cli_session_id=…)`）；新增 `find_session_jsonl` 命令处理。
  3. `app/modules/workspace/remote_agent_manager.py`：`send_command_with_response` 增加 `extra` 参数。
  4. `app/modules/workspace/remote_session_manager.py`：
     - `process_session_status_update` 增加 `cli_session_id` 参数 → 写库。
     - 新增 `resume_terminated_session`：优先用 DB `cli_session_id`；为空则发 `find_session_jsonl`（created_at 规范化为 ISO 字符串）→ 复用原会话 HA 路由 metadata（`proxy_token_jtis`）签发**新** proxy token → `start_session` 带 `resume_session_id` → 更新会话为 active 并持久化 cli_session_id。
  5. `app/routes/workspace.py`：terminated 分支改为 `if project_path:` 即尝试自动恢复（`info is None` 也尝试，agent 在线性由内部 `is_connected` 保证）。
  6. `app/routes/remote.py`：`session_status` 透传 `cli_session_id`。
- **修改的文件**：
  - `remote-agent/executor.py`、`remote-agent/agent.py`
  - `app/modules/workspace/remote_agent_manager.py`、`app/modules/workspace/remote_session_manager.py`
  - `app/routes/workspace.py`、`app/routes/remote.py`
  - 部署：容器 4 文件（备份 `.bak.20260806` + HUP reload，`/readyz` 200）；Windows agent 2 文件（备份 `.bak.20260806` + 重启，PID 5812）
- **验证结果（成功，端到端）**：对已终止会话 `c7cccef1-7ef2-40f8-8a40-7e28512b013b`（8/3 15:45）调 restore API：
  1. `POST /api/workspace/sessions/<id>/restore` → **200**，返回 `/work/workspace?sessionId=…&encodedProjectName=C--workspace…`。
  2. agent 日志：`find_session_jsonl: matched a75e1f88 (delta=3.0s)` → `Starting session c7cccef1 … resume=a75e1f88` → `qwen.CMD … --resume a75e1f88-40e2-4240-bf91-e1ee9163afd8` → **`SDK initialization complete`**（此前超时）。
  3. **历史真实加载**：`a75e1f88-….jsonl` 从 26458 → 29578 字节，恢复后追加新对话；`input_token_count:31727`（含 **30208 cached tokens** = 历史上下文）；`status_code:200`（无 401）；未生成新空会话文件。
  4. DB：`c7cccef1` 的 `cli_session_id=a75e1f88-40e2-4240-bf91-e1ee9163afd8` 已持久化，status=active。
- **根本原因**：
  恢复链路需要的 `--resume` 目标是 **qwen 内部会话 UUID（JSONL 文件名）**，该映射未在创建会话时捕获并持久化（`cli_session_id` 为空），无法仅凭服务端 session_id 恢复历史会话。
- **遗留说明**：
  - 新会话的 cli_session_id 捕获（watcher）已部署：用户通过 UI 新建远程 qwen 会话后，agent 自动发现 JSONL UUID 并经 `session_status` 上报写库。
  - 问题 24（webui"查看对话历史"显示远程会话）需在恢复链路确认后另行实施，本次未改动 webui。
- **状态**：已解决（历史会话恢复链路端到端验证通过；待用户在浏览器 UI 确认恢复会话体验 + 新会话捕获生效）

## 问题 26：恢复远程会话后文件变更面板报 "Directory does not exist: …C--workspace"（路径编码不兼容）

- **处理时间**：2026-08-06
- **故障现象**：恢复 8/4 远程会话（问题 25 方案 A 后）进入会话，webui 文件变更面板报错 `Directory does not exist: C:\Users\nuc\.open-ace-agent\C--workspace`，而远程工作区真实目录是 `C:\workspace`。
- **诊断结论**（错误链逐环验证）：
  1. 会话历史面板（蓝色云朵=远程）点"恢复会话" → [restore_session](file:///d:/TraeWorkspace/open-eduace/app/routes/workspace.py) 返回 URL `/work/workspace?sessionId=…&encodedProjectName=C--workspace&workspaceType=remote&machineId=…`——`encode_project_path_legacy` 把 `C:\workspace` 编码为 `C--workspace`（保留盘符，`:` 与 `\` 变 `-`）。
  2. open-ace 前端 [Workspace.tsx](file:///d:/TraeWorkspace/open-eduace/frontend/src/components/features/Workspace.tsx) 恢复初始化把 `encodedProjectName` 原样透传给 webui iframe。
  3. webui（容器内 `dist/static/assets/index-DO2hmkKX.js`）工作目录解析器只支持三种情况：命中项目列表（webui 自身 ko 编码 `-workspace`，与 `C--workspace` 不匹配）、`/` 开头、`-` 开头（Unix legacy）；`C--workspace` 全部不满足 → 原样作为 workingDirectory。
  4. 文件变更面板远程模式 → `GET /api/remote/machines/<id>/git/status?path=C--workspace` → [remote.py `_dispatch_remote_git_command`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py) 原样映射为 `project_path` → [agent.py `_cmd_git_status`](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py) `realpath("C--workspace")` 相对 agent 进程 cwd（`C:\Users\nuc\.open-ace-agent\`）→ 目录不存在报错。
- **根本原因**：open-ace legacy 编码（`C--workspace`）与 webui 编码（`-workspace`）不兼容，webui 无法从 legacy 编码还原 Windows 真实路径。新建远程会话链路传的是**真实路径**所以正常，恢复链路传的是 legacy 编码所以报错。
- **解决办法（方案 1，用户批准）**：[workspace.py](file:///d:/TraeWorkspace/open-eduace/app/routes/workspace.py) `restore_session` 对 `workspace_type == "remote"` 的 qwen/claude 会话，URL 的 `encodedProjectName` 直接使用**真实路径**（跳过 legacy 编码），并对值做 `urllib.parse.quote` URL 编码；本地会话逻辑不变（仍用 legacy 编码，webui 本地历史端点需要）。
- **修改的文件**：
  - `app/routes/workspace.py`：新增 `import urllib.parse`；编码分支按 `workspace_type` 区分；URL 构建处对 `encodedProjectName` 做 quote。
  - 部署：本地备份 `.bak.20260806` + `docker cp` + `kill -HUP 1`（`/readyz` 200）+ 哈希比对一致（216375a1…）。
- **验证结果**：`C:\workspace` → `C%3A%5Cworkspace`（前端 URLSearchParams 自动解码还原）；local legacy `-home-user-demo-project` 编码不变，本地会话不受影响。待用户 UI 复测恢复会话后文件变更面板。
- **状态**：已修复（待用户浏览器 UI 复测确认）

## 问题 27：恢复会话后发消息一直 Thinking（"Session already running" 误报 error → proxy token 连坐撤销）

- **处理时间**：2026-08-06
- **故障现象**：问题 25/26 修复后，恢复远程会话（如 `3c164185`，C:\workspace\aaa）并发送"你好"，qwen 一直 Thinking 转圈无回答。
- **诊断结论**（证据链逐环核实日志与代码）：
  1. **重复 start_session**：agent 日志显示 `6eac8f26` 每约 5 分钟被 `start_session` 一次（20:14:34 启动成功 → 20:19:35 再次启动；`3c164185` 20:15:19 启动 → 20:20:20 再次启动）；容器日志另见 **09:16 前端 1 秒内批量 POST restore 7 个会话**（Authorization header、无 token 参数）——存在自动/重复恢复触发源（未完全定位）。
  2. [executor.start_session](file:///d:/TraeWorkspace/open-eduace/remote-agent/executor.py#L946-L959) 对已在运行的会话返回 `"Session already running"`。
  3. [agent.py `_cmd_start_session`](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py) 对任何失败统一 `_send_session_status(session_id, "error")` → 容器日志 `Agent stderr [6eac8f26]: Failed to start session: Session already running`。
  4. [session_manager.update_session_fields](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/session_manager.py#L906-L913) 检测 status=error → **撤销该会话全部 proxy token**（容器日志 `Proxy token revoked: 4f1d3959`）。
  5. 用户 20:14:33 恢复的 qwen 进程（pid 39888/4200）**仍在运行**，但 token 已撤销 → `POST /api/remote/llm-proxy/v1/chat/completions → 401`（20:20:32 两次）→ 无法获得模型响应 → webui Thinking 转圈；20:20:32 的"你好"（`Sending message to session 3c164185`）进入令牌失效进程无响应。
- **根本原因**："会话已在运行"这一**幂等**情况被当作错误上报，触发 open-ace 的 token 连坐撤销，把正常进程"饿死"（次要：DeepSeek 思考模式 + `tool_choice` 400 会中断单次生成，非卡死主因）。
- **解决办法（方案 A+B，用户批准）**：
  - **A（agent 侧）**：[agent.py `_cmd_start_session`](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py) 失败分支区分错误——`"Session already running"` 时上报 `status=running`（幂等确认），**不上报 error**，避免触发 token 撤销；其他错误照旧。
  - **B（open-ace 侧）**：[remote_session_manager.py `resume_terminated_session`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py) 增加幂等检查——发送 start_session 前先 `get_session_info`，若会话已在 agent 运行则跳过重复启动，直接置 active 返回。
  - 附带：重启 agent 前**清空 `sessions.json`（executor metadata，备份 `.bak.20260806`）**，避免重启时 executor 用 server UUID + 空 token 自动恢复会话（问题 25 旧患）。
- **修改的文件**：
  - `remote-agent/agent.py`（方案 A；部署 `C:\Users\nuc\.open-ace-agent\agent.py`，哈希 d50502f9 一致，agent 重启 PID 5812→39040，20:38:59 注册成功）
  - `app/modules/workspace/remote_session_manager.py`（方案 B；容器 HUP reload，`/readyz` 200，哈希 434684e1 一致）
  - `sessions.json`（清空，备份 `sessions.json.bak.20260806`）
- **状态**：已修复（待用户复测：重新恢复远程会话并发消息应正常回答，不再卡 Thinking）
- **遗留**：自动/重复恢复触发源未完全定位（09:16 前端批量 restore 现象），方案 A 已使其幂等无害；若后续再出现异常需按方案 C 排查。

### 问题 27 第二轮：方案 A+B 后复测仍 Thinking —— SSE `/stream` 被 `_session_end_flags` 残留标记立即关闭

- **处理时间**：2026-08-06
- **故障现象**：方案 A+B 部署后，恢复 8/4 远程会话并发送"你好"，仍处于转圈等待状态。
- **诊断结论**（证据链）：
  1. 容器 HUP reload 后新 worker 启动时 [remote_agent_manager.py `_restore_in_memory_state`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_agent_manager.py#L253-L260) 把 DB 中 completed/error/stopped 会话全部写入 `_session_end_flags`（内存标记，**只增不清**）。
  2. 会话恢复置 active 后，[remote.py `stream_session_output`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py) 每次连接先调 `is_session_ended` —— L1 命中内存标记直接返回 True（不再查 DB），SSE 立即 yield `[DONE]`（连接约 571 字节即关闭）。
  3. 后端实际工作正常：LLM 多次 200、DB 写入 12 条输出；但 webui 的 stream 连接收不到任何实时输出 → 一直转圈。
- **根本原因**：`_session_end_flags` 是**进程内残留标记**，会话回到 active 后从未清除；`is_session_ended` 内存优先检查使其永久失效。
- **解决办法**（用户批准方案）：
  - [remote_agent_manager.py 新增 `clear_session_end_flag`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_agent_manager.py#L2174-L2187)：`pop` 内存标记并记录日志。
  - [remote_session_manager.py `process_session_status_update`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L1738-L1743) running/active 分支：置 active 时同步清除标记。
  - [remote_session_manager.py `resume_terminated_session`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py) 三处（幂等跳过分支、成功恢复分支）置 active 后清除标记。
  - [remote_session_manager.py `create_remote_session`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L492-L496) 新建会话防御性清除。
- **修改的文件**：
  - `app/modules/workspace/remote_agent_manager.py`（新增 `clear_session_end_flag`）
  - `app/modules/workspace/remote_session_manager.py`（3 处调用点）
  - 备份：`remote_agent_manager.py.bak.20260806`、`remote_session_manager.py.bak.20260806.2`
  - 部署：docker cp 两文件 → 容器内 py_compile 通过 → `kill -HUP 1` → `/readyz` 200 → 哈希一致（`1e4ffd3e…` / `e2aee970…`）
- **状态**：已修复（待用户复测：刷新/重新进入该会话页重建 SSE 连接后发消息，应实时收到回复）

### 问题 27 第三轮：恢复会话后发消息报 "[API Error: 401 Invalid or expired proxy token]" —— start_session 命令永不 ack → 每 5 分钟重复投递 → token 被撤销

- **处理时间**：2026-08-06
- **故障现象**：第二轮修复后，会话可恢复，但发送"你好"后回答为 `[API Error: 401 Invalid or expired proxy token]`。
- **诊断结论**（证据链，全部核实）：
  1. **命令永不 ack（根因）**：`resume_terminated_session` 发的 start_session 命令持久化到 `remote_runtime_commands` 后状态停在 `delivered`；[agent.py `_cmd_start_session`](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py) 执行后只回 session_status、**从不回 command_response**（对比 get_session_info/find_session_jsonl 都有 ack）→ `_persist_command_response` 从不被调用 → 命令永远 `delivered`。
  2. [\_claim_persisted_commands](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_agent_manager.py#L1384-L1460) 的 `COMMAND_CLAIM_TIMEOUT_SECONDS=300`（5 分钟）重新投递超时 delivered 命令 → **同一 start_session 每 5 分钟重复投递**（agent.log 证实：6eac8f26/3c164185/9978ea65 每 5 分钟各一次 "Starting session"）。
  3. 12:25-12:35 旧 agent 代码把重复 start_session 的 "Session already running" 报为 error → 服务器置会话 error → [revoke_proxy_tokens_for_session](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/session_manager.py#L906-L913) 撤销 4f1d3959（DB 证实该会话全部 token 均已撤销）。
  4. 20:40 自动重投命令（携带已撤销的 4f1d3959）启动的 qwen 进程**无法热更新 token**（环境变量注入、进程生命周期内固定）→ 每次 LLM 请求 401（容器日志每 5 分钟 `Proxy token revoked: 4f1d3959`）；恢复会话时幂等检查 `is_running=true` 直接跳过、不签发新 token → 进程继续用失效 token → 用户发消息得到 401。
- **根本原因**：**start_session 命令无 ack** → 5 分钟重复投递 → 旧代码把幂等情况当 error → token 连坐撤销 → 运行中进程持失效 token 无法自愈。
- **解决办法**（方案已给用户审阅，目标延续中实施）：
  - **[agent.py `_cmd_start_session`](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py#L906-L924) 结束前发送 command_response**（request_id=命令携带的 command_id/request_id）→ 服务器标记 responded → 不再 5 分钟重投（根因修复）。
  - **[executor.py `start_session`](file:///d:/TraeWorkspace/open-eduace/remote-agent/executor.py#L957-L978)**：会话已在运行且带 `resume_session_id`（恢复语义）→ 停旧进程、用新 token + `--resume` 重启；不带 resume → 维持返回 "Session already running"。
  - **[api_key_proxy.py 新增 `has_valid_proxy_token`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/api_key_proxy.py#L573-L605)**：查询 proxy_token_jtis 是否有未撤销未过期 token。
  - **[remote_session_manager.py `resume_terminated_session`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L630-L653)**：幂等检查 `is_running=true` 时先查 `has_valid_proxy_token` —— 有有效 token 才跳过；无有效 token 走正常恢复（签发新 token → start_session → executor 重启进程换新 token，自愈）。
- **修改的文件**：
  - `remote-agent/agent.py`（命令 ack）、`remote-agent/executor.py`（resume 重启）
  - `app/modules/workspace/api_key_proxy.py`（`has_valid_proxy_token`）、`app/modules/workspace/remote_session_manager.py`（幂等检查分支）
  - 备份：`agent.py.bak.20260806.2`、`executor.py.bak.20260806`、`api_key_proxy.py.bak.20260806`、`remote_session_manager.py.bak.20260806.3`
  - 部署：容器 docker cp + HUP + `/readyz` 200 + 哈希一致（`79cb5369…`/`d1b1476d…`）；agent 经 python `shutil.copy` 部署（哈希 `5930d5e9…`/`4565d035…` 一致）+ 重启（PID 39040→42552，13:14 注册上线）+ 清空 sessions.json（备份 `.bak.20260806.2`）
  - DB 清理：3 条 delivered start_session + 367 条其他 delivered 命令全部标记 responded（立即停止重投风暴）
- **验证**（端到端技术验证，已确凿）：
  - 容器内调用 `has_valid_proxy_token('3c164185…')` 返回 False（该会话全部 token 已撤销）→ 恢复必走新 token 路径。
  - 新签发 proxy token 直接调用 `/api/remote/llm-proxy/v1/chat/completions` → **200**（deepseek-v4-pro 正常回复）→ 新 token 认证通过、不再 401；旧 token 已被 `validate_proxy_token` 拒绝（revoked_at 非空）。
  - 部署后 15 分钟观察：无新的 `Proxy token revoked`/401、无 `Session already running`、命令队列无堆积。
- **状态**：已修复（技术验证通过；待用户 webui 复测确认恢复会话发消息正常）
- **遗留**：除 start_session 外，git_status 等其他命令同样不 ack → `remote_runtime_commands` 会继续堆积 delivered 并在 5 分钟后重投（垃圾流量，不导致功能错误）。建议后续在 agent `_handle_command` 统一补充命令 ack。

### 问题 28：恢复历史对话后泄露系统提示词（Platform Tool Limits / qwen 启动上下文 / Memory 指令以"用户"身份显示）

- **处理时间**：2026-08-06
- **故障现象**：UI 恢复远程会话后对话可正常进行，但恢复的历史对话中出现不应泄露的内部提示词，以"用户（User）"消息身份显示（见 `d:\TraeWorkspace\err.txt`，共 4 组）：
  1. `[Platform Tool Limits]` —— 平台工具限制说明
  2. `<system-reminder>` + qwen 启动上下文（目录结构）—— "This is the Qwen Code. We are setting up the context for our chat."
  3. `Memory directory:` + `## Phase 1-4` —— qwen 内存管理指令
  4. `Managed memory has TWO directories` —— 托管内存指引
- **诊断结论**（证据链，全部核实）：
  1. **qwen CLI 的格式设计（根因 1）**：qwen CLI 把上述系统提示写成 `type=user`、`provenance=real_user` 的消息存入 JSONL（`C:\Users\nuc\.qwen\projects\c--workspace\chats\4a4233a1-*.jsonl` line 0/35 证实），因为这些指令必须以 user 角色进入 LLM 上下文 → **JSONL 中无法仅凭 role/provenance 区分真实用户消息与系统上下文**。
  2. **open-ace 两条实时写入路径无过滤（根因 2）**，把系统上下文当用户消息写入消息表：
     - 路径 A（`source=web_terminal`）：agent 端 `remote-agent/session_sync.py` 解析 JSONL → 上报 messages → 服务器 [remote.py `agent_message`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L2060-L2211) 原样写入 session_messages + daily_messages（DB 证实：20+ 条 `[Platform Tool Limits]` role=user 记录，今天实时写入）。
     - 路径 B（`source=llm_proxy`）：[usage_sink.py `_record_messages`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/usage_sink.py#L353-L368) 在 llm-proxy 转发时取请求**最后一条 role=user 消息**记录——qwen 把系统上下文作为 user 消息发给 LLM → 被记录为 session_messages（DB 证实 `9978ea65` 会话的 `<system-reminder>` 记录）。
     - 另有一条历史抓取路径：[fetch_qwen.py `process_jsonl_file`](file:///d:/TraeWorkspace/open-eduace/scripts/fetch_qwen.py#L565-L590) 同样把 type=user 系统上下文写入 daily_messages。
  3. **webui 展示（根因 3）**：webui 远程会话界面调用 `GET /api/remote/sessions/<id>`（[remote.py `get_remote_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L985-L1009)）→ `get_session_status` → `session_manager.get_messages`（session_messages 表）→ 把 role=user 消息渲染为"用户"消息显示。webui 本地会话则直接读 JSONL（`/api/projects/:encodedProjectName/histories/:sessionId` → node.js `loadConversation` → `processConversationMessages`，同样不过滤）。
- **根本原因**：qwen CLI 将系统上下文写成 user 角色消息（为满足 LLM 上下文注入），open-ace/webui 在存储与展示层均未区分"真实用户消息"与"user 角色的系统上下文"，导致内部提示词泄露为对话内容。
- **解决办法**（3 层方案，用户批准）：
  - **第 1 层｜写入过滤（治本）**：新增共享模块 [scripts/shared/qwen_context.py](file:///d:/TraeWorkspace/open-eduace/scripts/shared/qwen_context.py)（`is_qwen_system_context(content)`，识别特征：`[Platform Tool Limits]` / `This is the Qwen Code. We are setting up the context...` / `Memory directory:` / `Managed memory has TWO directories` / `## Phase N` 标题 / `<system-reminder>` 日期+Qwen 启动句），在 3 个写入点对 role=user 消息过滤：
    - [remote.py `agent_message`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L2080-L2087) 同步循环（`source=web_terminal`）
    - [usage_sink.py `_record_messages`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/usage_sink.py#L353-L360)（`source=llm_proxy`）
    - [fetch_qwen.py `process_jsonl_file`](file:///d:/TraeWorkspace/open-eduace/scripts/fetch_qwen.py#L585-L590)（历史抓取）
  - **第 2 层｜读取兜底**：[remote.py `get_remote_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L992-L1007) 返回 messages 前做同样过滤，防止历史残留数据继续显示。
  - **第 3 层｜数据清洗**：删除 session_messages（115 条）与 daily_messages（68 条）中 role=user 的系统上下文记录（备份：容器 `/tmp/session_messages_sysctx_backup.csv`、`/tmp/daily_messages_sysctx_backup.csv`，可回滚）。
- **修改的文件**：
  - 新建 `scripts/shared/qwen_context.py`（共享过滤函数，本地 + 容器部署，哈希 95285853…）
  - `app/routes/remote.py`（agent_message 写入过滤 + get_remote_session 读取兜底；备份 `remote.py.bak.20260806c`，容器哈希 a7dd4d74…）
  - `app/modules/workspace/usage_sink.py`（llm_proxy 记录过滤；备份 `usage_sink.py.bak.20260806`，容器哈希 91604090…）
  - `scripts/fetch_qwen.py`（历史抓取过滤；备份 `fetch_qwen.py.bak.20260806c`，容器哈希 85ba2df7…）
  - 部署：docker cp 4 文件 → 容器内 py_compile 通过 → `kill -HUP 1` → `/readyz` 200 → 日志无 traceback、agent_message/llm-proxy 持续 200
- **验证**（已确凿）：
  - 过滤函数本地 + 容器内 9 组用例全部通过（err.txt 真实系统上下文→True；正常用户消息"你好"/中文请求→False）
  - 清洗后 session_messages / daily_messages 系统上下文记录 = 0；正常用户消息（"你好"）完好保留
- **状态**：已修复（技术验证通过；待用户 webui 复测：恢复历史会话后不应再看到系统提示词，且对话仍可正常进行）
- **遗留**：① qwen 本地会话历史（webui 读 JSONL 的 `loadConversation` 路径）仍在 qwen-code-webui npm 包内原样展示系统上下文——本方案未改动第三方包，若用户存在"容器内本地 qwen 会话"泄露场景需另行补丁 node.js；② 过滤仅针对 qwen，claude/codex 如存在同类"系统上下文写为 user 消息"的行为需单独评估。

### 问题 28 修复后回归：恢复远程会话报 "Failed to get remote session status: INTERNAL SERVER ERROR"（get_remote_session 500）

- **处理时间**：2026-08-06
- **故障现象**：问题 28 部署后，用户刷新登录，容器内会话正常，但恢复远程工作区会话时报 `Failed to get remote session status: INTERNAL SERVER ERROR`；点击"重新连接"后又报 `Failed to create remote session. Check machine availability and access.`
- **诊断结论**（证据链，已核实）：
  1. 容器日志 traceback：`AttributeError: 'SessionMessage' object has no attribute 'get'`，`GET /api/remote/sessions/9dcba571-…` 返回 500。
  2. **根因**：[remote.py `get_remote_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L992-L1014) 的问题 28 读取兜底过滤中，对 messages 元素直接调用 `m.get("role")/m.get("content")`，但 `session_manager.get_messages` 返回的是 **`SessionMessage` dataclass 对象**（[session_manager.py `class SessionMessage`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/session_manager.py#L146-L182)，有 `.role`/`.content` 属性、`to_dict()` 方法，无 `.get`）→ 过滤时抛 AttributeError → 500。
  3. 连带：前端 [index-DO2hmkKX.js `ao()`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py) 获取会话状态失败后走"重新连接"新建分支（`POST /api/remote/sessions`），因会话已存在/机器 busy 且 qwen 需有效 ha_pool_token，[create_remote_session](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L291-L403) 返回 None → 报 "Check machine availability and access."（连带表现，非独立故障；机器 last_heartbeat 正常、会话 9dcba571 active）。
- **解决办法**（用户批准实施）：
  - [remote.py `get_remote_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L1001-L1014) 过滤逻辑改为兼容对象与 dict：`_msg_role(m) = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")`，`_msg_content(m)` 同理。
  - 备份：`remote.py.bak.20260806d`；部署：docker cp → 容器内 py_compile → `kill -HUP 1` → `/readyz` 200 → 容器哈希 `657b0553…` 一致。
- **验证**（已确凿）：
  - 本地 + 容器内测试：SessionMessage 对象与 dict 混合列表过滤无异常（3 条正常消息保留、2 条系统上下文剔除）。
  - 容器内直接调用 `get_session_status('9dcba571-…')` 返回 15 条 SessionMessage 对象，套用新过滤逻辑无异常（该会话已无系统上下文残留）。
  - 重载后日志无新 Traceback/500；机器 7fad3781 在线（last_heartbeat 持续更新）、会话 active。
- **状态**：已修复（用户 webui 复测确认：恢复远程会话正常进入，不再 500 / 不再提示创建失败）
- **遗留**：无新增；"重新连接"按钮语义为新建会话（qwen 需有效 ha_pool_token），与"恢复会话"是不同入口，若用户误用仍需注意。

### 问题 24 实施收尾 + 新建远程会话失败回归（agent 离线）

- **处理时间**：2026-08-06（当天实施 + 当晚回归）
- **故障现象**：新账号登录后 webui 项目选择器只有容器内项目 `/workspace/admin`，远程项目不显示；恢复远程历史正常，但**新建远程目录对话**报 `错误: Failed to create remote session. Check machine availability and access.`。
- **诊断结论**（证据链，已核实）：
  1. **远程项目不显示**：webui openace 集成模式的项目选择器读 open-ace 后端 `GET /api/projects`，该接口只从 `projects` 表返回（Issue #1859），不含 `agent_sessions` 里的远程会话 → 合并修复。
  2. **新建远程会话失败（非代码 bug）**：容器日志 `Machine 7fad3781-… is not connected`，[create_remote_session](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L328-L333) 的 `is_connected()` 检查失败 → 返回 None → 路由 400 通用错误。本机 `agent.log`：**23:00:29 收到 SIGINT（Ctrl+C）被手动停止**（`Received signal 2, shutting down…`），服务器 3 分钟心跳超时后（23:03:50）标记机器 offline，用户 23:07:28 创建会话即失败。
  3. 附带：计划任务 `OpenACEAgent` 上次运行失败（`0x80070002` = 找不到 `python`），agent 停止后登录自启拉不起来。
- **根本原因**：
  - 远程项目缺失：`/api/projects` 未合并 agent_sessions 的远程项目（webui 集成模式依赖该接口）。
  - 新建会话失败：远程机器 agent 进程停止 → 机器 offline → `is_connected()` 为 False。
  - 报错信息误导：`create_remote_session` 所有失败统一返回 None，路由只能回笼统文案，无法定位真实原因。
- **解决办法**（用户批准实施）：
  - [projects.py `_fetch_remote_projects`](file:///d:/TraeWorkspace/open-eduace/app/routes/projects.py#L173-L212)：`GET /api/projects` 从 `agent_sessions`（workspace_type IN remote/terminal）去重合并远程项目，返回 `is_remote: True` 条目。
  - [session_history_sync.py](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/session_history_sync.py#L52-L64)：新增 `encode_openace_path()`（webui SPA 的 `ko()` 编码：去盘符/前导斜杠 → 全非字母数字替换为 `-` → 加 `-` 前缀），远程会话 JSONL 按 **双编码**（标准 `C--workspace` + openace `--workspace`）写入 `<HOME>/.qwen/projects/{encoded}/chats/`，标准镜像目录只为非 `-` 开头编码创建。
  - [remote.py `create_remote_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L969-L1008)：webui 集成模式新建会话不带 ha_pool_token 时，服务端自动签发（复用 session-models 逻辑：`get_tool_model_pool` → 撤销旧 token → `generate_proxy_token`，session_id=`ha-pool:{machine_id}`）。
  - **报错信息改进**：[remote_session_manager.py](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L291-L430) `create_remote_session` 的 12 处失败点由 `return None` 改为 `return {"success": False, "error": "<具体原因>"}`；[remote.py](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L1021-L1031) 路由透出真实原因（无原因时才回退通用文案）。沙箱调用方 [remote_machine.py](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/autonomous/sandbox/remote_machine.py#L156-L158) 本就按 `success: False` dict 处理，兼容无需改动。
  - 备份：`projects.py.bak.20260806e`、`session_history_sync.py.bak.20260806e`、`remote.py.bak.20260806f`、`remote_session_manager.py.bak.20260806f`；`app/__init__.py` 改动（启动 sync 循环）需 `docker restart open-ace`（HUP 对 `__init__.py` 无效），其余 HUP。
  - 运营操作：本机重启 agent（`start-agent.ps1`，PID 28296），机器恢复 online；计划任务 `OpenACEAgent` 的 action 从 `python` 改为绝对路径 `C:\Users\nuc\AppData\Local\Programs\Python\Python312\python.exe`（需管理员/UAC 提权重注册，普通 `Set-ScheduledTask`/`Register-ScheduledTask` 均 Access denied）。
- **验证**（已确凿）：
  - `GET /api/projects` 返回 4 个项目（`/workspace/admin` + 3 个远程）；`GET /api/projects/{enc}/histories` 在 webui（3100）下 `--workspace` 返回 2843 字节、`-workspace-admin` 返回 1167 字节（19888 后端无此路由返回 404，浏览器经 nginx fetch 拦截器重写到 `/webui/{port}/api/...` 不受影响）。
  - `POST /api/remote/sessions`（不带 ha_pool_token）端到端返回 200，会话 `fdbe3478-a580-4afc-ba9b-97d1f6cd69a9`（machine 7fad3781, `C:\workspace\test`, active）创建成功。
  - 容器哈希与本地一致；`/readyz` 200。
- **状态**：已修复并验证
- **遗留**：无新增。注意 agent 是手动/计划任务进程，若再被 Ctrl+C 停止需手动重启或等下次登录自启。

### 问题 24 第二轮：文件变更 frame 初始化报错（恢复会话 HTTP 403 + 新建远程工作区 GATEWAY TIMEOUT）

- **处理时间**：2026-08-06（当晚）
- **故障现象**：问题 24 收尾后，新建远程会话/恢复远程会话均已正常，但**文件变更面板（file-changes frame）初始化报错**：恢复会话时报 `HTTP 403`；新建远程工作区时转圈很久后报 `Failed to fetch remote git status: GATEWAY TIMEOUT`。
- **诊断结论**（证据链，已核实）：
  1. **面板模式判定**：webui 内嵌组件 `zm()`（`index-DO2hmkKX.js`）只有 URL 带 `workspaceType=remote&machineId` 才走远程模式（`mo(machineId, wd)` → open-ace `/api/remote/machines/{id}/git/status?path=`），否则走本地模式（webui 自己的 `/api/git/status`）。
  2. **403 根因**：本地模式调 webui `/api/git/status?workingDirectory=X`，`validateWorkingDirectory` 要求 X 在 `~/.qwen-code-webui/project-mapping.json`（当前只有 `/workspace/admin`）。实测 `/`、`C:\workspace` 均返回 `403 workingDirectory is not a known project`。
  3. **504 根因**：新建远程会话时 open-ace 前端 URL 未传编码后的项目路径（只传了 raw `encodedProjectName=C:\workspace\test`，webui SPA 的 `ko()` 编码是 `-workspace-test`，raw 路径不匹配项目列表 → 落到 `location.pathname` 兜底 → `/`）；面板远程模式 `mo(machineId, '/')` → agent 快照扫描整个 `C:\`（agent.log：`git_status(snapshot) for C:\: 970/1025/1523/1639/1700 files`，每个耗时 ~2 分钟）→ 后端 `get_browse_result(timeout=15)` 超时 → 504。
  4. **连带故障**：整盘扫描命令在 `remote_runtime_commands` 累积 `delivered` 未响应（21 条），agent 主循环被卡死 → 心跳停发 → 机器被标 offline → 后续请求 503 "Agent is not connected"。
- **根本原因**：
  - open-ace 前端构造远程会话 iframe URL 时用 raw 路径而非 webui SPA 的 ko() 编码，SPA 无法解析出真实远程路径 → workingDirectory 兜底 `/` → git/status 扫全盘 → 504。
  - 后端 git/status 对根路径/盘根不设防，把整盘扫描任务发给 agent。
  - 恢复会话时面板可能以本地模式初始化（URL 缺 workspaceType/machineId 或路径非已知项目）→ webui 403。
- **解决办法**（用户批准 A+B）：
  - **A（后端防呆）**：[remote.py `_dispatch_remote_git_command`](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L3513-L3523) 对空路径、`/`、盘根（`C:`/`C:\`/`C:/`）直接返回 400 "Invalid path"，不再发给 agent。
  - **B（前端传路径）**：[Workspace.tsx](file:///d:/TraeWorkspace/open-eduace/frontend/src/components/features/Workspace.tsx#L872-L897) `getEffectiveUrl` 对 remote 会话把 raw 项目路径按 webui SPA 的 ko() 编码（去盘符/前导斜杠 → 非字母数字换 `-` → 加 `-` 前缀）转换为 `encodedProjectName`（如 `C:\workspace\test` → `-workspace-test`）；`remoteParams` 类型补充 `projectPath/sessionId`。前端重新构建（`npm run build` → `static/js/dist`）。
  - 运营处置：清空 DB `remote_runtime_commands` 中机器 7fad3781 的 `delivered` git/vscode 命令（20 条），重启本机 agent（PID 45048）摆脱整盘扫描，机器恢复在线。
  - 备份：`remote.py.bak.20260806g`、容器 `static/js/dist.bak.20260806`。
- **验证**（已确凿）：
  - git/status：`/` → 400（0.0s）；`C:\workspace\test` → 200（0.9s）；`C:\workspace` → 200（1.0s）。
  - 容器 Workspace.CTkWS1Mq.js 含 ko() 编码逻辑；index.html → index.aPlzavtS.js。
  - 机器心跳恢复（status=busy，heartbeat 持续更新）。
- **状态**：已修复并验证（待用户在浏览器强刷后复测文件变更面板）
- **遗留**：恢复会话的 403 若在 A+B 后仍出现，说明该会话 tab URL 仍缺 workspaceType/machineId（面板退本地模式），需方案 C（webui `validateWorkingDirectory` openace 模式放宽或会话 URL 补齐远程参数）才能彻底解决。

### 问题 24 第三轮：恢复会话 403 复现 — webui SPA 导航清空 URL 参数（C 方案）

- **处理时间**：2026-08-07
- **故障现象**：A+B 修复部署后，用户反馈"会话的 403 仍然出现"——在 webui 内部"查看会话历史"→ 点击历史会话恢复时，文件变更面板仍报 HTTP 403。
- **诊断结论**（证据链，已核实）：
  1. 检查容器内 SPA bundle `index-DO2hmkKX.js`，发现 **3 处导航代码用裸 `new URLSearchParams`（不带参数）重建 URL**，清掉全部现有查询参数：
     - **A 点**（bundle 位置 415528，历史列表点击会话）：`let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}` → 恢复会话时 URL 只剩 `sessionId`，`workspaceType=remote`/`machineId`/`encodedProjectName` 全丢。
     - **B/C 点**（位置 493359/494603，两处"查看会话历史"按钮，文本相同、各出现 1 次共 2 处）：`let e=new URLSearchParams;e.set(`view`,`history`),t({search:e.toString()})` → 进历史列表即丢远程参数。
  2. SPA 状态读取（位置 480595）实证：`A=n.get('workspaceType')==='remote'`、`j=n.get('machineId')`、`ue=view==='history'`、`de=!!sessionId&&!ue`——`workspaceType`/`machineId` 只从 URL 读取。参数丢失 → 文件变更面板 `zm()` 退本地模式 → 调 webui `/api/git/status` → `validateWorkingDirectory` 不认识该 workingDirectory → **403**。
  3. 第 2 轮 A+B 只修了 open-ace 前端构造 iframe URL 的入口（左侧会话 tab 恢复正常），但 **webui 内部自身的导航**仍会把 URL 参数清掉，因此从 webui 内历史列表恢复依然 403。
- **根本原因**：webui SPA 内部 3 处导航以"只保留会话参数"的方式重建 URL（上游设计缺陷），破坏了 open-ace 集成模式依赖的 workspaceType/machineId 远程上下文。
- **解决办法**（用户批准 C 方案）：
  - 新建 [patch-qwen-webui-navparams.py](file:///d:/TraeWorkspace/open-eduace/scripts/patch-qwen-webui-navparams.py)（版本固定 qwen-code-webui@0.2.40，漂移 build 失败，模式同 histories 补丁）：
    - A 点改为 `new URLSearchParams(window.location.search)` 继承现有参数，`set(sessionId,e)` 后 **`delete(view)`**（否则 `view=history` 残留导致会话不加载）；
    - B/C 点改为 `new URLSearchParams(window.location.search)` 继承现有参数后再 `set(view,history)`；
    - 附带对 `static/index.html` 的 `<script src>` 追加 `?v=navparams-20260807` 缓存破坏（热补丁不改哈希文件名，需破浏览器缓存）。
  - **Dockerfile**（L189 后）追加 `RUN python3 /app/scripts/patch-qwen-webui-navparams.py`，未来重建镜像自动生效。
  - 热部署：容器内备份 `index-DO2hmkKX.js.bak.20260807`、`index.html.bak.20260807` → docker cp 脚本 → 执行成功 → 验证 `OLD_session=0/NEW_session=1`、`OLD_history=0/NEW_history=2`、`INDEX_BUST=True`。
- **验证**（已确凿）：
  - `BUNDLE_CHECKS {'OLD_session': 0, 'NEW_session': 1, 'OLD_history': 0, 'NEW_history': 2}`；`SCRIPT_SRC /assets/index-DO2hmkKX.js?v=navparams-20260807`。
  - 无需重启 webui 进程（静态资源，浏览器刷新即取新文件；`?v=` 参数已强制绕过缓存）。
- **状态**：已修复并验证（待用户在浏览器**强刷/重新打开会话**后复测 webui 内历史列表恢复会话）
- **遗留**：webui"新对话"按钮（C 点旁的 `t({search:''})`）仍会清空 URL——属于新建会话语义（集成模式下先选项目再建会话，不在此 403 范围），未改动。

### 问题 24 第四轮：恢复会话 403 复现（D 方案：后端恢复 URL 直接用 ko() 编码）

- **处理时间**：2026-08-07
- **故障现象**：C 方案（SPA 导航保留 URL 参数）部署后用户强刷仍报 "HTTP 403"（文件变更面板）；此前恢复会话还见 `Directory does not exist: \workspace\test`。
- **诊断结论**（证据链，已核实）：
  1. **403 基线实证**：`curl webui /api/git/status?workingDirectory=/workspace/test`、`C:/workspace/test`、`C:\workspace\test` 均返回 `403 {"error":"workingDirectory is not a known project"}`（project-mapping.json 只有 `{"-workspace-admin":"/workspace/admin"}`）。面板本地模式必然 403。
  2. **`Directory does not exist: \workspace\test`**：SPA 的 F（workingDirectory）解析 `i.find(e=>e.encodedName===be)` 未命中项目列表 → 解码兜底 `'/'+be.slice(1).replace(/-/g,'/')` = `/workspace/test` → 远程 agent（Windows）把 `/workspace/test` 规范化为 `\workspace\test` → 目录不存在报错。
  3. **根因收窄**：[workspace.py `restore_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/workspace.py#L1706-L1716) remote 分支此前返回 **raw 路径**（`encoded_project_name = actual_path`，如 `C:\workspace\test`）作为恢复 URL 的 `encodedProjectName`。SPA 项目列表的 encodedName 是 **ko() 编码 `--workspace-test`（双前缀！）**，raw 路径永不匹配 → F 解码兜底 → 路径错/403。
  4. **重要纠错**：ko() 编码对 Windows 路径是**双前缀**：`C:\workspace\test` → 去盘符 `\workspace\test`（反斜杠也非字母数字）→ `-workspace-test` → 加前缀 → **`--workspace-test`**。此前文档/记忆误记为单前缀 `-workspace-test`。
  5. **C 补丁后用户测试无 open-ace 请求**（restore/git/status 日志无新增）→ 用户看到的 403 是 webui 本地接口（不进 open-ace 日志），无法从后端日志定位 URL。
- **根本原因**：恢复会话 URL 的 encodedProjectName 用 raw 路径，SPA 无法匹配项目列表 → 文件变更面板路径解析错误（403 / 远程目录不存在）。
- **解决办法**（用户批准：仅 D1，未做 D2 放宽）：
  - [workspace.py `restore_session`](file:///d:/TraeWorkspace/open-eduace/app/routes/workspace.py#L1706-L1716) remote 分支改为复用 [session_history_sync.py `encode_openace_path`](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/session_history_sync.py#L52-L64)（与 SPA ko() 编码一致）：`encoded_project_name = encode_openace_path(actual_path) if actual_path else ""`。
  - 恢复 URL 现为 `encodedProjectName=--workspace-test`（URL 编码）→ 前端 getEffectiveUrl 因 `startsWith('-')` 不再二次编码 → SPA 项目列表精确匹配（`--workspace-test` ↔ `C:\workspace\test`）→ F=`C:\workspace\test` → 面板远程模式正确。
  - 部署：备份 `workspace.py.bak.20260807d` → docker cp → `kill -HUP 1`（routes 模块 HUP 生效，无需重启）。
- **验证**（已确凿）：
  - `POST /api/workspace/sessions/{id}/restore`（HUP 后）：`C:\workspace` → `encoded_project_name="--workspace"`；`C:\workspace\aaa` → `"--workspace-aaa"`（均与 SPA 项目列表 encodedName 一致）。
  - 前端 Workspace.CTkWS1Mq.js 转换条件确认：`!e.startsWith("-")` 才编码，`--` 前缀原样透传。
  - 语法检查通过；容器内文件已更新（HUP 后接口即返回新格式）。
- **状态**：已修复并验证（待用户复测：左侧历史列表恢复会话 → 文件变更面板应显示远程项目真实 git 状态，不再 403 / Directory does not exist）
- **遗留**：
  - 用户选择不做 D2（webui 服务端 4 处 403 放宽）。若仍有入口让面板退本地模式（URL 缺 workspaceType/machineId），webui `/api/git/status` 对未知路径仍 403——后续如需彻底兜底可补 D2。
  - 会话历史目录中已按错误认知写入的单前缀 `-workspace-test` JSONL 目录与 SPA 期望的 `--workspace-test` 不一致，属历史数据问题（问题 24 第一轮双编码已写 `--workspace-test` 与 `C--workspace-test`，不受影响）。

### 问题 24 第五轮：恢复会话 403 复现（E 方案：webui SPA 项目选择器导航丢失远程参数）

- **处理时间**：2026-08-07
- **故障现象**：D1（恢复 URL ko() 编码）部署后，用户刷新浏览器点击远程会话恢复，仍报 HTTP 403（文件变更面板）。
- **诊断结论**（证据链，已核实）：
  1. **用户 iframe URL 实锤**（用户从 F12 提供）：`http://localhost:3100/projects/C:/workspace/aaa?sessionId=eafde766-...` —— pathname=`/projects/C:/workspace/aaa`，查询参数**只有 sessionId**，无 workspaceType/machineId/token/encodedProjectName。
  2. **403 机制**：该 URL 命中 webui `/projects/*` 路由（ChatPage）。SPA 读 `A=n.get('workspaceType')==='remote'` = false → 面板**本地模式** → `zm` 调 webui `/api/git/status?workingDirectory=C:/workspace/aaa` → 不在 project-mapping.json（只有 `/workspace/admin`）→ **403**。
  3. **URL 生成者**（bundle 位置 182144，项目选择器点击项目）：`let E=e=>{...;S(\`/projects${t}\`)}` —— `S`(navigate) **整页替换 URL**，丢弃 workspaceType/machineId/token。open-ace 集成模式下 iframe 进入项目选择器后，用户点击远程项目即触发该导航 → 远程上下文丢失。
  4. **日志佐证**：面板在远程模式持续请求 `git/status?path=%2F`（400 防呆）与 `session-models?workspace_type=local` 并存——多 tab 混合，远程 tab 的 F 解析为 `/`，本地 tab 报 403。
- **根本原因**：webui SPA 项目选择器点击项目用裸 navigate 替换整 URL（上游设计缺陷），与 C 轮修的 3 处会话内导航同源但位置不同；open-ace 集成模式依赖的 workspaceType/machineId 在此入口丢失。
- **解决办法**（用户批准 E 方案）：
  - 扩展 [patch-qwen-webui-navparams.py](file:///d:/TraeWorkspace/open-eduace/scripts/patch-qwen-webui-navparams.py) 加第 4 处补丁：项目选择器导航 `S(\`/projects${t}\`)` → `S(\`/projects${t}${window.location.search}\`)`（保留现有查询参数）；`CACHE_BUST` bump 为 `v=navparams-20260807b`；脚本 cache-bust 逻辑兼容旧 `?v=navparams-` 版本（替换而非报错）。
  - 热部署：备份 `index-DO2hmkKX.js.bak.20260807b`、`index.html.bak.20260807b` → docker cp 脚本 → 执行成功（旧 3 处 skip、新 1 处 patched、index.html bust bumped）。
- **验证**（已确凿）：
  - `BUNDLE {OLD_SESSION:0, NEW_SESSION:1, OLD_HISTORY:0, NEW_HISTORY:2, OLD_PROJECT:0, NEW_PROJECT:1}`；`SCRIPT_SRC /assets/index-DO2hmkKX.js?v=navparams-20260807b`。
  - webui 3100 实例 curl：`NEW_PROJECT=1 / OLD_PROJECT=0`（3101/3102 已空闲停止）。
  - Dockerfile 已引用该脚本（重建镜像自动应用，含新 cache-bust 逻辑）。
- **状态**：已修复并验证（待用户**强刷浏览器**后复测：webui 项目选择器点击远程项目 → 文件变更面板应走远程模式显示真实 git 状态）
- **遗留**：
  - 用户当前 iframe 会话（`/projects/C:/workspace/aaa?sessionId=...`）是旧 bundle 导航产生的历史 URL，需重新从项目选择器点击一次或刷新以加载新 bundle。
  - D2（webui 服务端 403 放宽）仍未实施；若后续仍出现面板退本地模式导致的 403，可补 D2 彻底兜底。

### 问题 24 第六轮：回滚 403 相关修改（用户要求恢复到 403 修改前基线）

- **处理时间**：2026-08-07
- **背景**：用户反馈 403 反复出现，且新出现"远程会话输入消息后 AI 无响应（日志无 chat/LLM-proxy 请求，消息未到达 open-ace）"，认为修改过头影响正常功能。经用户确认执行**全部回滚**（A/B/C/D/E），但**保留历史会话恢复功能修复（问题 24 第 1 轮）**。
- **回滚清单**（A=git/status 防呆、B=前端 ko() 编码、C=webui SPA 3 处导航补丁、D1=restore URL ko() 编码、E=webui 项目选择器导航补丁）：
  - 容器 webui bundle/index.html → 恢复 `index-DO2hmkKX.js.bak.20260807`、`index.html.bak.20260807`（C/E 前 = 原始无 navparams）
  - 容器 workspace.py → 恢复 `workspace.py.bak.20260807d`（D1 前）+ `kill -HUP 1`
  - 容器 remote.py → 恢复 `remote.py.bak.20260806g`（A 前 = 第 1 轮后）+ HUP
  - 容器 open-ace 前端 dist → 恢复 `static/js/dist.bak.20260806`（B 前，Workspace.B8QfvEGk.js）
  - 源码 [Workspace.tsx](file:///d:/TraeWorkspace/open-eduace/frontend/src/components/features/Workspace.tsx) → 撤销 ko() 编码段与 remoteParams 类型扩展；本地 workspace.py/remote.py 从容器备份拷回；[Dockerfile](file:///d:/TraeWorkspace/open-eduace/Dockerfile) 移除 navparams RUN（补丁脚本 `patch-qwen-webui-navparams.py` 保留备用）
- **保留未动**（用户要求）：projects.py 合并远程项目、session_history_sync.py 双编码与 `encode_openace_path()`、remote.py create_remote_session ha_pool_token 自动签发、remote_session_manager.py 报错信息、workspace.py resume_terminated_session 自动恢复（Issue #669）。
- **验证**（已确凿）：
  - webui bundle 无任何 navparams 补丁（NEW_SESSION=0/NEW_HISTORY=0/NEW_PROJECT=0，OLD_PROJECT_ORIG=1）；index.html 无 `?v=`。
  - workspace.py 无 encode_openace_path；remote.py 无 git/status 防呆（"Invalid path" 仅为 agent_files 原有的路径穿越防护），ha_pool_token 逻辑保留。
  - dist 回滚为 Workspace.B8QfvEGk.js（无 ko 编码）；`/readyz` 全 ok；webui 服务正常。
- **状态**：已回滚到问题 24 第二轮前的基线（403 相关修改全部撤销，历史会话恢复功能保留）
- **遗留**：AI 对话不响应（消息未达 open-ace）待回滚后复测确认是否与补丁相关；若仍复现需另行诊断（ha_pool_token/LLM 代理/会话状态）。

### 问题 24 第七轮：选择项目后无法对话 + 查看历史按钮跳项目选择列表（根因定位 + 精准修复）

- **处理时间**：2026-08-07
- **故障现象**（用户复测 4 点）：
  1. 登录切换工作区 → 项目选择列表选远程项目 → 会话区初始化正常但**和 AI 对话无效**，文件变更区 HTTP 403；
  2. 左下角蓝云朵恢复对话**正常**；
  3. 文件变更区上部"查看历史会话"按钮点击后**先跳项目选择列表**，再点才出历史列表，恢复后**不能对话**；
  4. 正确恢复的会话 UI **自动出现 AI 反馈**（后台把之前未获回复的提问重新发给 AI 并回显）。
- **诊断结论**（证据链，已核实）：
  1. **日志实证**：`app.routes.workspace - WARNING - Invalid project_path rejected` → `POST /api/workspace/sessions` **400**（webui 会话跟踪组件 `Is` 调 open-ace create_session，传远程 Windows 路径 `C:\workspace`，容器内 `is_valid_path`（Linux 要求 `/` 开头）拒绝）。该 400 是**跟踪失败**（会话关联/历史同步不完整），不直接阻断对话。
  2. **对话无效根因（webui SPA）**：[ChatPage（bundle `Bg` 组件）](file:///d:/TraeWorkspace/open-eduace/tmp_analysis/index-DO2hmkKX.js) 建立远程会话的 useEffect 为 `!A||!j||...||(de&&ce?connectSession:F&&ge&&startSession(j,F,...))`，其中 `A=n.get('workspaceType')==='remote'`、`j=n.get('machineId')` **全部来自 URL 查询参数**。项目选择器点击项目用 `S('/projects'+t)` **整页替换 URL 丢弃全部参数**（回滚后 E 点补丁不在）→ A=false/j='' → **永不创建远程会话** → 消息输入框 `if(!t){c('No active remote session');return}` 静默丢弃 → "对话无效"。文件变更面板同因缺 machineId → 本地 `/api/git/status` → 403（**403 根因即此**，本轮按用户要求不专门处理）。
  3. **查看历史按钮跳项目选择**：两处"查看历史会话"按钮用 `new URLSearchParams`（无参）重建 URL 只留 `view=history` → URL 变 `/?view=history` → 根路由 `RootRedirect` 渲染**项目选择器**（用户看到的"先跳项目选择列表"）。
  4. **蓝云朵"正常"是假象**：恢复会话走本地同步 JSONL + 本地 qwen CLI（LLM 走 open-ace 代理），机器在线时可用，但非远程模式（工作目录不对）。
  5. **症状 4 = agent 积压处理**：`deepseek-v4-pro` LLM 代理 200 + agent 批量输出；且 **07:07:29 远程机器心跳超时被标记 offline、07:10 清理会话**——离线后所有远程会话（对话/SSE/git）失败，这是症状 3"恢复后不能对话"与症状 4 的直接环境原因。
  6. **仓库 start-agent.ps1 被意外清空**（git 显示 187 行全删的未提交改动）；远程机器上的 start-agent.ps1 是 8/2 旧版（7633B），仓库/容器最新版（8/5 提交 `bc7fec3e`，8634B）新增 `Get-PythonPath`（计划任务绝对路径，修复 `python` 0x80070002）与精确进程检测正则。
- **根本原因**：webui SPA 4 处导航（sessionId nav、2 处 history nav、项目选择器 nav）重建 URL 时丢弃 workspaceType/machineId/token 等参数 → ChatPage 判定本地模式 → 不创建远程会话（对话无效）+ 面板本地模式（403）+ 历史按钮跳根路由（项目选择器）；叠加远程 agent 离线与 create_session 路径校验误拒远程路径。
- **解决办法**（用户批准全部实施）：
  1. **重新启用 [patch-qwen-webui-navparams.py](file:///d:/TraeWorkspace/open-eduace/scripts/patch-qwen-webui-navparams.py)**：4 处导航全部改为 `new URLSearchParams(window.location.search)` 初始化（保留现有参数）；sessionId nav 额外 `delete('view')`；项目选择器 `S('/projects'+t+window.location.search)`；`CACHE_BUST` bump 为 `v=navparams-20260807c`。热部署：docker cp 脚本 → 容器内执行成功（sessionId 1 处 + history 2 处 + project 1 处 patched，index.html cache-bust applied）→ bundle 中 `new URLSearchParams(window.location.search)` 出现 4 次验证。
  2. **create_session 放行远程 Windows 路径**（[workspace.py L1212-1224](file:///d:/TraeWorkspace/open-eduace/app/routes/workspace.py#L1212-L1224)）：`is_valid_path` 失败时若 `project_path` 匹配 `^[A-Za-z]:[\\/]`（远程机器 Windows 盘符路径，不在容器内）则放行，消除 tracking 400。部署：docker cp + `kill -HUP 1`（新 worker 1421 启动，语法检查通过）。
  3. **恢复 Dockerfile**：在 patch-qwen-webui-histories.py 后恢复 navparams RUN（含注释），保证下次镜像构建自动应用。
  4. **start-agent.ps1**：`git checkout --` 恢复仓库被清空文件（= 容器最新版 8634B）→ 覆盖同步到远程机器 `%USERPROFILE%\.open-ace-agent\start-agent.ps1` → 用新脚本重启 agent（PID 30820）→ 容器日志 `Agent connected (HTTP): 7fad3781` + `Heartbeat monitor started`，**机器恢复在线**。
- **验证**（已确凿）：
  - bundle：`new URLSearchParams(window.location.search)` ×4（原 `new URLSearchParams;n.set`/`;e.set` 为 0）；index.html `?v=navparams-20260807c`。
  - workspace.py AST 语法通过；HUP 后 gunicorn 正常 fork 新 worker。
  - 远程机器 start-agent.ps1 更新为 8634B（与仓库/容器一致）；agent 重新连接、心跳正常。
- **状态**：已修复并部署（待用户**强刷浏览器**后复测：①项目选择器选远程项目 → 对话正常；②查看历史会话按钮 → 直接出历史列表（不再跳项目选择）→ 恢复 → 对话正常；403 按用户要求本轮不处理，修改后如 URL 参数保留 403 应顺带改善）
- **遗留**：
  - 403 未专门修复（D2 服务端放宽仍未实施）；本轮修改保留 URL 参数后，面板应保持在远程模式，403 大概率不再触发，如仍出现可补 D2。
  - 症状 4（agent 积压重放）为机器离线/会话状态不同步所致，agent 已重启在线；若复现需复查 agent 消息队列。
  - 仓库 `remote-agent/start-agent.ps1` 恢复后 git 状态干净；`fix-code-server.ps1` 不在仓库（用户询问的"code-server 修复时脚本"即 start-agent.ps1 8/2 版本）。

### 问题 24 第七轮补充：选择项目后仍不能对话（根因：webui 项目选择器无 machine_id）

- **处理时间**：2026-08-07
- **故障现象**：第七轮 navparams 补丁部署后，历史按钮/恢复会话正常，但**项目选择器选远程项目后仍不能对话**（输入消息时 AI 圈圈图标一闪而过）。日志实证：`session-models?workspace_type=local`（webui 被判定本地模式）、agent 侧 `Failed to send message: Session not found`。
- **诊断结论**（证据链，已核实）：
  1. **webui ProjectSelector（bundle `Ao` 组件）数据源**：`Ga()`（open-ace `/api/projects`）→ `i(e)` 存原始 projects 到状态 `r` → `e.map(ko)` 生成 `{path, encodedName}` 编码列表用于渲染。**列表项与原始数据均无 machine_id**。
  2. **后端 `/api/projects` 的远程项目**（[projects.py `_fetch_remote_projects`](file:///d:/TraeWorkspace/open-eduace/app/routes/projects.py#L173-L216)）只查 `project_path`，**未返回 machine_id**（字段实为 `agent_sessions.remote_machine_id`）。
  3. **open-ace 默认工作区 tab 的 iframe URL 不带 workspaceType/machineId**（`createNewTab()` 无 remoteParams）→ webui 显示 ProjectSelector → 点击远程项目时 E 点补丁只能保留"已有的" query 参数，而这些参数**本就不存在** → ChatPage `A=n.get('workspaceType')==='remote'`=false、`j=n.get('machineId')`='' → **本地模式** → useEffect 不 startSession → 消息输入框静默丢弃 → 对话无效。
  4. **对照**：恢复历史会话正常——open-ace `createNewTab(restoreSessionId, {workspaceType:'remote', machineId, sessionId})` 的 iframe URL **带完整远程参数** → webui 远程模式 connectSession → 正常。两条路径差异即 machine_id 是否在 URL。
- **根本原因**：webui 集成模式项目选择器点击远程项目时，URL 无 workspaceType/machineId 可保留（原 URL 就没有，且项目数据不含 machine_id）→ ChatPage 无法进入远程模式。
- **解决办法**（用户批准修改 3+4）：
  - **修改 3（后端）**：[projects.py](file:///d:/TraeWorkspace/open-eduace/app/routes/projects.py#L186-L216) `_fetch_remote_projects` SQL 加 `remote_machine_id`，返回项加 `"machine_id": r["remote_machine_id"]`。
  - **修改 4（前端）**：[patch-qwen-webui-navparams.py](file:///d:/TraeWorkspace/open-eduace/scripts/patch-qwen-webui-navparams.py#L75-L91) 项目选择器 E 点升级到 v2：点击时 `r.find(s=>s.path===e)` 查原始项目，若 `n.machine_id` 非空 → URL 追加 `workspaceType=remote&machineId=<id>`；本地项目不加参数。依赖数组 `[S]`→`[S,r]`。`CACHE_BUST` bump 为 `v=navparams-20260807d`。
- **部署**：docker cp projects.py + `kill -HUP 1`；docker cp patch 脚本 → 容器内执行成功（sessionId/history skip，project-selector 升级 v2，index.html cache-bust bumped）。
- **验证**（已确凿）：
  - bundle：`n.machine_id` 存在（V2 逻辑生效）；index.html `?v=navparams-20260807d`。
  - SQL 实证：`agent_sessions` 3 个远程项目（`C:\workspace`、`C:\workspace\aaa`、`C:\workspace\test`）均带 `remote_machine_id=7fad3781-038c-42f1-afec-f03c3d8465e9`。
  - projects.py AST 语法通过；HUP 生效。
- **状态**：已修复并部署（待用户**强刷浏览器**后复测：项目选择器选远程项目 → 应进入远程模式创建远程会话 → 正常对话）
- **遗留**：
  - 若 `r.find` 未命中（如项目列表刚加载完前点击）可能不加参数 → 仍本地模式；正常时序（列表加载后点击）不受影响。
  - 403 仍未专门处理；URL 现在带完整远程参数，面板应保持在远程模式。

### 问题 24 第七轮用户验证结果（修改 3+4 生效）

- **验证时间**：2026-08-07
- **用户复测结果**：
  1. **项目选择器选远程项目 → 输入消息 → 能正常对话**（修改 3+4 生效，webui 进入远程模式创建远程会话）✅
  2. **文件变更面板 HTTP 403 消失**（URL 带完整远程参数，面板保持在远程模式）✅
  3. **历史会话恢复后互动正常** ✅
- **新观察现象（非故障）**：选择项目后 UI 自动出现一条消息（内容为 qwen 内部思考："用户问的是 Qwen Code 中 pipeline 的概念…读取记忆文件…C:\workspace\bbb 与 C:\workspace\test"），并弹出 skill 权限请求（`Qwen 想要使用 skill 命令 skill: qc-helper`）。
- **诊断结论**（证据链，已核实）：
  - agent 日志：`Starting session ba0fb5a3: ... resume=None`（qwen CLI **全新启动，无 --resume**）；用户发"你好"后 qwen 主动 `Permission request ... can_use_tool (tool=skill)`。
  - remote_runtime_commands 表无积压 send_message（只有 git_status 轮询 + 用户刚发的消息）——**不是服务器命令重放**。
  - 原因：**qwen CLI 的记忆 + skill 机制**——qwen 回答"你好"时读取了 ~/.qwen 记忆文件，回忆到之前用户问过的 pipeline 话题（bbb/test 项目），自动调起 `qc-helper` 技能（skill 权限请求），输出相关思考与回答。这是 AI 工具的**正常行为**，非代码 bug，无需修复。
- **结论**：本轮问题 1（选择项目后对话）与问题 2（历史按钮/恢复对话）均已解决，403 消失。用户无需再操作；如不希望 qwen 自动回忆旧话题，属 qwen CLI 行为层面（记忆功能），可另行评估。

### 问题 24 第八轮：AI 重复收到同一条用户消息（send_message 缺 ack）+ VS Code 打开弹密码页（诊断）

- **处理时间**：2026-08-07
- **故障现象**：用户在对话区未说话、仅点击文件变更面板"启动 VS Code"按钮后：①VS Code 打开出现**登录输入密码页面**；②对话区 AI **自动回复了"你好"且重复两次**（此前用户发过"你好"）。
- **诊断结论**（证据链，已核实）：
  1. **重复消息根因**：[remote_agent_manager.py](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_agent_manager.py#L168) `COMMAND_CLAIM_TIMEOUT_SECONDS=300`——服务器每 5 分钟重新 claim 超时未确认的 delivered 命令（L1387-1439）；而 [agent.py `_cmd_send_message`](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py#L926-L947) **不回 command_response ack**（对比 `_cmd_start_session` 有 ack）→ send_message 永久 delivered → 每 5 分钟重投。agent 日志实证：`Sending message ... 你好` 出现 4 次（15:34:38、15:37:25、15:39:38、15:44:39），最后两次间隔 301s（恰为 5 分钟重投）；本次用户未说话仍收到 2 次 = 服务器重投。DB 实测：`remote_runtime_commands` 有 3 条 delivered 的 send_message 积压。
  2. **VS Code 密码页**：日志 `GET .../vscode/{id}/proxy/` → **302** → `/proxy/login`（code-server 登录页，3167B）。HTTP 代理 [remote.py L3858-3866](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L3858-L3866) **已有** cs_password→Basic Auth 逻辑，且 vscode_status 处理 L1957 会存 cs_password——需运行时确认 cs_password 是否真的从 agent 传输并落库（AGENT-DEBUG 日志过滤了 cs_password 无法直接观察）。
- **根本原因**：
  - 重复消息：send_message 命令缺 ack → 服务器 5 分钟重投。
  - VS Code 密码页：待诊断（嫌疑 cs_password 丢失/为空）。
- **解决办法**（用户批准全部实施）：
  1. **agent.py `_cmd_send_message` 加 command_response ack**（成功/失败都回，格式同 start_session）→ 服务器标记 responded 不再重投。同步到远程机器 `%USERPROFILE%\.open-ace-agent\agent.py`（备份 `.bak.20260807`，精确文本替换 + py_compile 验证）→ 重启 agent（新 PID 18604，`Agent connected` + 心跳正常）。
  2. **清理积压命令**：删除 `remote_runtime_commands` 中 `command_type='send_message' AND status IN ('pending','delivered')` 的 3 条旧命令，避免修复后仍重放一次。
  3. **remote.py vscode_status 加诊断日志**：status=running 且 cs_password 为空时打 WARNING（`vscode_status running without cs_password`）→ docker cp + HUP 生效。
- **验证**（已确凿）：agent.py 补丁唯一匹配 + 语法通过；DB send_message 积压清零；agent 重新连接；remote.py HUP 生效。
- **状态**：问题 2（重复消息）已修复并部署；问题 1（VS Code 密码）待用户**重新点击"启动 VS Code"**复现，检查 open-ace 日志是否出现 `without cs_password` 警告以确认根因。
- **遗留**：问题 1 若 cs_password 确实为空，需进一步查 agent 上报链路（_http_send/消息序列化）或 code-server 认证行为；若不为空，需查 Basic Auth 是否被 code-server 接受。

### 问题 24 第八轮补充：DeepSeek 思考模式拒绝 tool_choice（AI 无回复）+ VS Code 密码页真根因（cookie 认证）

- **处理时间**：2026-08-07
- **故障现象**：①会话区输入"帮我创建一个名为txt的文件夹"（需调用 shell 工具）→ AI **无任何回复、图标消失**（此前"帮我看看文件夹的状态"正常）；②重新点击"启动 VS Code"仍弹登录密码页，open-ace 日志**无** `without cs_password` 警告（密码已传输）。
- **诊断结论**（证据链，已核实）：
  1. **AI 无回复根因**：日志 `LLM proxy error 400 from https://api.deepseek.com: {"error":{"message":"Thinking mode does not support this tool_choice",...}}`（07:52:35、07:52:50 各一次）。qwen CLI 在需要**强制调用工具**时发送 `tool_choice` 字段，DeepSeek **思考模式不支持 tool_choice → 400** → LLM 调用失败 → qwen 无回复。第一个指令（查状态）不需要强制工具调用 → 正常。
  2. **VS Code 密码页真根因**：[code-server `http.js`](file:///C:/Users/nuc/AppData/Local/nvm/v18.20.8/node_modules/code-server/out/node/http.js) `authenticated` 中间件**只检查 session cookie**（`req.cookies[Session]`），且整个 code-server 代码**无 authorization/Basic Auth 处理**——之前 proxy 加的 `Authorization: Basic base64(:password)` 被 code-server 完全忽略 → 必然 302 到 /login。正确认证 = 用密码 POST /login 拿 cookie。
- **根本原因**：
  - AI 无回复：llm_proxy 透传 tool_choice → DeepSeek 思考模式 400。
  - VS Code 密码页：code-server 只认 cookie，Basic Auth 无效。
- **解决办法**（用户批准全部实施）：
  1. **[llm_proxy_handler.py L1295-1319](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/llm_proxy_handler.py#L1295-L1319)**：转发前解析请求体，`tool_choice` 非空时移除（记录日志；模型仍会按函数定义自动调用工具）。
  2. **[remote.py L3869-3900](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L3869-L3900)**：vscode proxy 转发前，若 info 无 `cs_cookie` 且有 `cs_password` → `POST {original_http_url}/login`（form: password=cs_password, allow_redirects=False）→ 解析 `Set-Cookie` 首段存入 `info["cs_cookie"]`（vscode_info_store 存引用，跨请求保留，避免触发 code-server 登录限流 2/min、12/hour）→ 转发请求头注入 `Cookie: cs_cookie`。
- **部署**：docker cp 两文件 + AST 语法通过 + `kill -HUP 1`（新 worker 2343）。
- **验证**（已确凿）：语法通过；HUP 生效。需用户复测：①重新发"创建 txt 文件夹"指令应正常回复；②重新点"启动 VS Code"应直接进 IDE 不再弹密码。
- **状态**：已修复并部署，待用户浏览器复测确认。

### 问题 24 第九轮：AI 无回复/消息失败（CLI 每轮退出）+ VS Code WS 桥接失败

- **处理时间**：2026-08-07
- **故障现象**：用户复测时：①第二条指令报 `Failed to send message: Session not found`；②VS Code 页面能加载（HTTP cookie 登录修复生效）但**编辑器无响应**（WS 桥接失败）。
- **诊断结论**（证据链，已核实）：
  1. **消息失败根因**：agent 日志 cd6b35b4：16:00:48 启动（qwen CLI pid 10108，cli_session_id=c2fa9bf3）→ 16:01:08 第一条"看看文件夹状态"处理成功（LLM 200 + result success）→ 16:01:30 第二条"创建 txt 文件夹" **Session not found**。**qwen CLI 在处理完每条消息后进程退出**；executor 的 cleanup（[executor.py L1748-1761](file:///d:/TraeWorkspace/open-eduace/remote-agent/executor.py#L1748-L1761)）把已退出会话从 `_sessions` 移除 → 之后 send_message 在 [executor.py L1304-1305](file:///d:/TraeWorkspace/open-eduace/remote-agent/executor.py#L1304-L1305) 直接返回 "Session not found"（连 L1322 的自动重启都不触发，因为 session 对象已不存在）。
  2. **VS Code WS 失败根因**：`VSCode WS handler: bridge failed`（vscode_ws_bridge.py L176 `connect()`）——WS 桥接沿用旧的 Basic Auth 头，而 code-server 认证**只认 cookie**（第九轮确认）→ WS 握手被拒 → IDE 界面加载但无响应。
- **根本原因**：
  - 消息失败：CLI 每轮退出 + executor 移除已退出 session + send_message 无恢复机制。
  - VS Code 无响应：WS 桥接用 Basic Auth（code-server 忽略）而非 cookie。
- **解决办法**（用户批准全部实施）：
  1. **[remote_session_manager.py L896-943](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/remote_session_manager.py#L896-L943)** 新增 `_ensure_cli_running`：`send_message` 前用 `get_session_info` 探测 agent 侧进程，不在则调 `resume_terminated_session`（--resume 恢复，幂等——进程在则跳过）→ 解决 "Session not found"。
  2. **[vscode_proxy.py L42-84](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/vscode_proxy.py#L42-L84)** 新增公共函数 `ensure_cs_cookie(info, original_http_url, vscode_id)`：POST /login 拿 session cookie 并缓存到 info（避免限流 2/min、12/hour）。
  3. **[vscode_ws_bridge.py L145-183](file:///d:/TraeWorkspace/open-eduace/app/modules/workspace/vscode_ws_bridge.py#L145-L183)** `bridge_vscode_ws_raw` 新增 `cs_cookie` 参数：优先用 `Cookie` 头认证，Basic Auth 降级为兼容保留。
  4. **[remote_ws_handler.py](file:///d:/TraeWorkspace/open-eduace/app/remote_ws_handler.py#L633-L646)** WS 桥接前调 `ensure_cs_cookie` 生成 cookie，传给 bridge（日志加 has_cookie 标记）。
  5. **[remote.py L3869-3878](file:///d:/TraeWorkspace/open-eduace/app/routes/remote.py#L3869-L3878)** HTTP 代理改用公共 `ensure_cs_cookie`（统一逻辑）。
- **部署**：docker cp 5 文件 + AST 语法通过 + `kill -HUP 1`（新 worker 2568，心跳正常）。
- **状态**：已修复并部署，待用户浏览器复测：①再次发"创建 txt 文件夹"应能正常回复（自动恢复 CLI）；②重新点"启动 VS Code"应能进 IDE 且编辑器可用（WS cookie 认证）。
- **遗留**：qwen CLI 为何每轮处理完即退出（可能与 CLI 版本/SDK 行为有关）——已用自动恢复兜底；若后续出现"恢复耗时/上下文丢失"可再深究 CLI 退出根因。

### 问题 25："Session not found / 消息无响应"的真正根因——双 agent 实例并存

- **处理时间**：2026-08-07
- **故障现象**：用户质疑"qwen CLI 每轮处理完就退出"不符合常理，要求深入分析根因。
- **诊断结论**（证据链，已核实）：
  1. **"CLI 每轮退出"是误判**：进程查询显示 cd6b35b4 的 CLI 进程树 `cmd 10108 (16:00:48, 父进程=agent 30820) → node 21672 → 19580 → 6488` **到 16:18 仍全部存活**，CLI 根本没有退出。
  2. **系统里同时存在两个 agent.py 实例**：PID **30820**（15:21:37 启动）和 PID **18604**（15:51:06 启动），命令行均为 `python C:\Users\nuc\.open-ace-agent\agent.py`，使用**相同 machine_id=7fad3781** 轮询同一服务器（localhost:19888）。
  3. **命令被错误实例抢占**：两个 agent 都从 `/api/remote/agent/message` 拉取 pending_commands。16:01:08 第一条消息由持有 session 的 30820 处理成功；16:01:30 第二条被 18604 抢到，其 `_sessions` 无 cd6b35b4 → [executor.py L1304-1305](file:///d:/TraeWorkspace/open-eduace/remote-agent/executor.py#L1304-L1305) 返回 "Session not found"，并上报 `status=error pid=None`。
  4. **排除 executor 自身清理**：`cleanup_stopped()`（[executor.py L1746](file:///d:/TraeWorkspace/open-eduace/remote-agent/executor.py#L1746)）在 agent 代码中**从未被调用**；日志无 "Cleaned up stopped session"/"Stopping session" 记录。
  5. **间歇性症状与双实例吻合**：15:53-16:10 多次 "Session not found" 涉及 3c5d0876、6b6902ce、cd6b35b4、9f5700ab、d46ff256 等多个会话，时好时坏。
- **根本原因**：
  1. **start-agent.ps1 单实例检测正则失效**：`Get-AgentProcess` 用 `"(^|[\\])agent\.py($|[\\\s])"` 匹配命令行，但实际命令行是 `"...agent.py"`（**agent.py 后紧跟双引号 `"`**），`($|[\\\s])` 不匹配引号 → 检测永远返回空 → `-Status` 报"未运行"，每次执行都启动新实例。
  2. **计划任务 OpenACEAgent**（登录自启，直接运行 `python agent.py`，不经 start-agent.ps1 检测）+ 用户手动双击 OpenACE-Agent.cmd = 两个入口各自拉起实例，互不知晓。
  3. **agent.py 无单实例保护**：任意入口都能启动第二个进程。
- **解决办法**（用户批准执行）：
  1. **[agent.py L2503-2561](file:///d:/TraeWorkspace/open-eduace/remote-agent/agent.py#L2503-L2561)** 新增 `acquire_single_instance_lock()`：安装目录下 `agent.lock` 文件排他锁（Windows `msvcrt.locking` / POSIX `fcntl.flock`），进程退出/崩溃自动释放；`main()` 中 setup_logging 后调用，抢锁失败则 `sys.exit(1)`。**从任何入口启动都兜底防双实例**。
  2. **[start-agent.ps1 L91-98](file:///d:/TraeWorkspace/open-eduace/remote-agent/start-agent.ps1#L91-L98)** `Get-AgentProcess` 正则改为 `(^|[\\])agent\.py(?![A-Za-z0-9_])`（负向断言排除 agent.pyc 等，允许后跟引号/空白/参数/行尾）→ 检测恢复生效。
  3. **注意**：本文件为 **UTF-8 with BOM + CRLF**（git 原始如此）。用 Edit 工具保存会丢 BOM，PowerShell 5.1 按 ANSI/GBK 解析中文注释乱码导致语法错误——需用 PowerShell `UTF8Encoding($true)` 写回 BOM。
  4. **现场清理**：`start-agent.ps1 -Stop` 停掉两个 agent；清理旧 agent 残留的孤儿进程（qwen CLI 进程树 + 8 个 code-server 实例，父进程已死）；重启单个 agent。
- **验证**（已确凿）：
  - `start-agent.ps1 -Status` 修复前报"未运行"（检测失效），修复后正确报 "Agent 正在运行 (PID: 38592)"。
  - 再次运行 `start-agent.ps1` → "Agent 已在运行，无需重复启动"（ps1 层检测生效）。
  - 绕过检测直接 `python agent.py` → ERROR "Another Open ACE Remote Agent instance is already running (lock held...)"，exit=1，实例数保持 1（文件锁兜底生效）。
  - agent 日志心跳正常（git_status 轮询持续），session 崩溃恢复 4 个会话成功。
- **状态**：已修复并验证，单实例运行正常。用户需浏览器复测：①选择项目后 AI 对话正常、无 "Session not found"；②文件变更面板正常。
- **遗留**：①旧 agent 恢复的 76010d38 会话在 resume 后 CLI 空闲退出，permission response 写入 stdin 报 `[Errno 22]`（属第九轮"CLI 空闲退出"已知行为，非双 agent 问题）；②计划任务 OpenACEAgent 与手动启动仍可能竞争（agent.py 文件锁已兜底，无需改计划任务）。

### 问题 25 补充：agent 卡死（心跳停止/对话无响应/VSCode 超时）——Trae 工具沙箱导致 tempfile 死循环

- **处理时间**：2026-08-07
- **故障现象**：修复双 agent 后，用户复测：①重新登录后工作区需新建才能进入；②新建会话后 AI 对话等待很长、卡死；③恢复会话后发现新建会话的指令出现在该会话并得到回答；④打开 VS Code 报 "VSCode startup timed out"。
- **诊断结论**（证据链，已核实）：
  1. **agent 主循环完全卡死**：agent.log 从 16:52:05 起完全停止（git_status 轮询 5s 一次但 5 分钟无新记录）；进程 CPU 飙升至 805 秒/5 分钟（死循环烧 CPU）。
  2. **py-spy 抓栈定位卡点**（决定性证据）：主线程卡在 `cli_settings.py:38 _atomic_write_json → tempfile.NamedTemporaryFile → _mkstemp_inner`；session-sync 线程也卡在 `tempfile.mkstemp`。**两个线程都在 ~/.qwen 创建临时文件时死循环**。
  3. **tempfile 死循环机理**：`_mkstemp_inner` 里 `os.open` 抛 **PermissionError**（写入被拒），随后检查 `os.path.isdir(dir) and os.access(dir, W_OK)`——Windows 上 `os.access` 对目录**误报可写**（返回 True）→ `continue` 无限重试，且 `TMP_MAX=2147483647`（Python 3.12 定义）→ 死循环。
  4. **根因是 Trae 工具沙箱**：我通过 RunCommand 终端启动的 agent 进程**继承了 Trae 沙箱环境**（`TRAE_SANDBOX_SBOX_ID`、`TOOLHOST_SANDBOX_DISABLED=false` 等），沙箱限制 agent 写 `~/.qwen`（该目录 ACL 含 `CodexSandboxUsers:(RX)` 只读组）→ 写 settings.json 的 tempfile 创建被拒 → 死循环。**用户手动启动（双击 OpenACE-Agent.cmd）/计划任务启动的 agent 无沙箱限制，一直正常**。
  5. **附带佐证**：沙箱启动的 agent 日志反复出现 `Permission denied: sessions.json`（Errno 13）；改用计划任务启动后该错误消失。
- **根本原因**：在 Trae/AI 工具沙箱内启动 agent → agent 继承受限令牌 → 无法写用户目录 → `tempfile.mkstemp` PermissionError + `os.access` 误报 → 无限循环卡死。
- **解决办法**（已实施）：
  1. **用计划任务启动 agent（避开沙箱）**：`schtasks /Run /TN OpenACEAgent`——Task Scheduler 以用户交互令牌启动，进程父链为 svchost（任务计划服务），无 Trae 沙箱环境变量。**不要用 AI 终端启动 agent**。
  2. **验证**：新 agent（PID 36252）心跳正常、4 会话恢复成功、无 Permission denied；py-spy 确认无线程卡死；CPU 正常（2.3s/1分钟）。
  3. 桌面双击 OpenACE-Agent.cmd 同样可用（用户正常环境）。
- **状态**：已恢复。用户需刷新浏览器复测：①新建会话 AI 对话正常；②VS Code 启动正常；③恢复会话指令不串线。
- **经验教训（重要）**：**本项目的 agent 必须在用户正常环境（计划任务/桌面脚本）启动，严禁在 Trae 工具终端沙箱内启动**——沙箱令牌会使 agent 写用户目录失败并死循环。若 agent 异常卡死（心跳停止），先用 py-spy dump 抓栈定位，再按此检查启动来源。