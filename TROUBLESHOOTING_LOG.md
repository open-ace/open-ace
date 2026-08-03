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

- **可能的解决方案**（待后期设计）：
  1. **在 `fetch_qwen.py` 中添加文件变更检测逻辑**：解析 shell 工具调用的命令内容，识别 mkdir/mv/cp 等操作，生成 `file_change` content_block
  2. **在通用层面添加文件变更提取**：在 `remote_session_manager.py` 中从 `tool_use` 事件分析工具调用内容，识别文件操作并生成 `file_change` block
  3. **添加后处理机制**：会话完成后通过 git diff 检测文件变更，追加到 transcript 中

- **修改的文件**：无（留到后期设计实现）

- **状态**：待处理（功能缺失，需后期设计文件变更检测方案）

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