# Nginx 反向代理配置指南

## 背景

Open-ACE 在多用户模式下，每个用户拥有独立的 qwen-code-webui 进程，运行在不同端口（如 3100-3200）。当使用 HTTPS + nginx 反向代理部署时，需要解决以下问题：

1. **混合内容阻止**：HTTPS 页面无法加载 HTTP iframe
2. **路径重写**：前端资源引用的是绝对路径（如 `/assets/index.js`），需要添加 `/webui/{port}/` 前缀
3. **React Router 路由匹配**：BrowserRouter 没有 `basename`，无法匹配 `/webui/{port}/` 路径
4. **API 路径代理**：JS 中同时包含 webui API 和 Open-ACE API，需要分别代理到不同后端

## 整体架构

```
浏览器 (HTTPS)
    │
    ▼
nginx (443) ─── /webui/{port}/* ──→ http://127.0.0.1:{port} (qwen-code-webui)
    │
    └── /* ──────────────────────→ http://127.0.0.1:5000   (Open-ACE Flask)
```

用户通过 `https://your-domain/webui/3100/` 访问自己的 webui 实例。

## 完整配置

将 `your-domain.com` 替换为你的实际域名，端口范围根据 `config.json` 中的 `port_range_start` / `port_range_end` 调整。

```nginx
# HTTP -> HTTPS 重定向
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

# HTTPS 主配置
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /path/to/ssl/fullchain.pem;
    ssl_certificate_key /path/to/ssl/privkey.pem;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    # ── JS 文件：注入 basename（不重写 API 路径） ──
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)/(.+\.js)$ {
        set $webui_port $1;
        set $webui_file $2;

        proxy_pass http://127.0.0.1:$webui_port/$webui_file$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 只注入 BrowserRouter basename，不重写 API 路径
        # API 路径由 HTML 中注入的 fetch/EventSource 拦截器处理
        sub_filter '(0,U.jsx)(mi,{children:' '(0,U.jsx)(mi,{basename:window.__WEBUI_BASENAME__,children:';
        sub_filter_types application/javascript text/javascript text/html;
        sub_filter_once off;
    }

    # ── CSS 文件：修正 Content-Type ──
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)/(.+\.css)$ {
        set $webui_port $1;
        set $webui_file $2;

        add_header Content-Type "text/css; charset=utf-8" always;

        proxy_pass http://127.0.0.1:$webui_port/$webui_file$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── HTML 页面和 API 请求 ──
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)(/.*)?$ {
        set $webui_port $1;
        set $webui_path $2;

        if ($webui_path = "") {
            set $webui_path "/";
        }

        proxy_pass http://127.0.0.1:$webui_port$webui_path$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "";
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_send_timeout 75s;

        # 注入 __WEBUI_BASENAME__ + fetch/EventSource 拦截脚本
        # 拦截脚本将 webui API 调用（/api/chat 等）加上 /webui/{port} 前缀，
        # 而 Open-ACE API（/api/remote/ 等）保持不变，直接走主后端
        sub_filter '<script type="module"' '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";(function(){var p="/webui/$webui_port";var s=["/api/remote/","/api/workspace/","/api/quota/"];var of=window.fetch;window.fetch=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return of.call(this,u,o)};var oe=window.EventSource;window.EventSource=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return new oe(u,o)}})();</script><script type="module"';
        # 重写 HTML 中的资源路径
        sub_filter 'href="/' 'href="/webui/$webui_port/';
        sub_filter 'src="/' 'src="/webui/$webui_port/';
        sub_filter_types text/html;
        sub_filter_once off;
    }

    # ── Open-ACE 主应用 ──
    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "";
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_send_timeout 75s;
    }
}
```

## 配置详解

### 1. 三个 location 块的作用

nginx 按 **最长匹配优先** 处理 location。三个 location 从上到下匹配精度递减：

| location | 匹配内容 | 作用 |
|---|---|---|
| `~ \.js$` | `/webui/{port}/assets/index-xxx.js` | JS 文件 basename 注入（不重写 API 路径） |
| `~ \.css$` | `/webui/{port}/assets/index-xxx.css` | CSS 文件 Content-Type 修正 |
| `~ ^/webui/...` | `/webui/{port}/`、`/webui/{port}/api/chat` 等 | HTML 页面 + API 请求 + fetch 拦截器注入 |

### 2. 端口范围正则

```
(310[0-9]|31[1-9][0-9]|3200)
```

匹配 3100-3200 端口范围。如果你的 `port_range_start` / `port_range_end` 配置不同，需要相应调整。

### 3. sub_filter 工作原理

`sub_filter` 是 nginx 的响应内容替换模块，对代理返回的内容做字符串替换：

```
sub_filter '原始字符串' '替换后的字符串';
```

关键注意事项：
- **基于 Content-Type 过滤**：`sub_filter_types` 指定哪些 Content-Type 的响应会被处理
- **不能与 `proxy_hide_header` 配合**：`proxy_hide_header Content-Type` 会移除类型信息，导致 sub_filter 无法判断是否处理
- **不处理压缩内容**：如果上游返回 gzip 压缩，sub_filter 不会工作（确保上游不发压缩或使用 `proxy_set_header Accept-Encoding ""`）

### 4. 核心问题与解决方案

#### 问题 A：混合内容阻止

**现象**：iframe 一直转圈，浏览器控制台报 mixed content 错误。

**原因**：Flask 应用不知道自己在 HTTPS 后面，`request.scheme` 是 `http`，返回的 iframe URL 是 `http://ip:port`。

**解决**：三处配合

1. Flask 应用添加 `ProxyFix` 中间件（`app/__init__.py`）：
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
```

2. nginx 传递正确的代理头：
```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

3. Flask 在 HTTPS 时返回相对路径 URL（`app/routes/workspace.py`）：
```python
if manager.config.multi_user_mode and flask_request.scheme == "https":
    port_match = re.search(r":(\d+)$", url)
    if port_match:
        url = f"/webui/{port_match.group(1)}/"
```

#### 问题 B：React Router 路由不匹配

**现象**：iframe 加载空白，浏览器控制台报 `No routes matched location "/webui/{port}/..."`。

**原因**：qwen-code-webui 的 `BrowserRouter` 没有配置 `basename`，路由定义在 `/`、`/projects/*` 等根路径上，无法匹配 `/webui/{port}/` 前缀。

**解决**：两步注入

**第一步**：在 HTML 中注入全局变量（在 HTML location 块中）：
```nginx
sub_filter '<script type="module"'
    '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";</script><script type="module"';
```
效果：在模块脚本加载前设置 `window.__WEBUI_BASENAME__`。

**第二步**：在 JS 中将变量传递给 BrowserRouter（在 JS location 块中）：
```nginx
sub_filter '(0,U.jsx)(mi,{children:'
    '(0,U.jsx)(mi,{basename:window.__WEBUI_BASENAME__,children:';
```
效果：React Router 获得 `basename="/webui/{port}"`，能正确匹配路由。

> **注意**：`(0,U.jsx)(mi,` 是 qwen-code-webui 打包后 `React.createElement(BrowserRouter, ...)` 的 minified 形式。如果 webui 重新打包，变量名会变化，需要相应更新 sub_filter 规则。

#### 问题 C：API 路径代理

**现象**：前端 API 请求 404 或返回 Not Found。

**原因**：JS 中同时包含两类 API 调用，路径都以 `/api/` 开头：
- webui API：`/api/chat`、`/api/version` 等 → 需要代理到 webui 端口
- Open-ACE API：`/api/remote/sessions/...`、`/api/workspace/...` → 需要代理到主后端（5000 端口）

使用 `sub_filter '`/api/'` 会**无差别重写**所有 API 路径，导致 Open-ACE API 调用被错误代理到 webui 端口而返回 404。

**解决**：在 HTML 中注入 fetch/EventSource 拦截器，按路径前缀区分两类 API：
```nginx
# 注入拦截脚本：仅对 webui API（/api/chat 等）添加 /webui/{port} 前缀
# Open-ACE API（/api/remote/、/api/workspace/、/api/quota/）保持原路径
sub_filter '<script type="module"' '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";(function(){var p="/webui/$webui_port";var s=["/api/remote/","/api/workspace/","/api/quota/"];var of=window.fetch;window.fetch=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return of.call(this,u,o)};var oe=window.EventSource;window.EventSource=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return new oe(u,o)}})();</script><script type="module"';
# 重写 HTML 中的静态资源路径
sub_filter 'href="/' 'href="/webui/$webui_port/';
sub_filter 'src="/' 'src="/webui/$webui_port/';
```

> **注意**：不要在 JS location 块中使用 `sub_filter '`/api/'` 重写 API 路径，因为它无法区分 webui API 和 Open-ACE API。

#### 问题 D：查询参数丢失

**现象**：URL 中的 token 参数没有被传递到后端。

**原因**：当 `proxy_pass` 使用变量时，nginx 不会自动附加查询参数。

**解决**：手动附加 `$is_args$args`：
```nginx
proxy_pass http://127.0.0.1:$webui_port$webui_path$is_args$args;
```
- `$is_args`：如果请求有查询参数则为 `?`，否则为空
- `$args`：查询参数字符串

## 部署步骤

```bash
# 1. 复制配置文件
cp open-ace.conf /etc/nginx/conf.d/open-ace.conf

# 2. 测试配置语法
nginx -t

# 3. 重载 nginx
nginx -s reload

# 4. 验证
# 检查 HTML 中是否注入了 __WEBUI_BASENAME__ 和 fetch 拦截器
curl -sk "https://your-domain/webui/3100/" | grep __WEBUI_BASENAME__

# 检查 JS 中是否注入了 basename
curl -sk "https://your-domain/webui/3100/assets/index-xxx.js" | grep 'basename:window.__WEBUI_BASENAME__'

# 确认 JS 中没有被重写 API 路径（不应出现 /webui/3100/api/）
curl -sk "https://your-domain/webui/3100/assets/index-xxx.js" | grep '/webui/3100/api/' | wc -l

# 检查 API 可达性（需要有效 token）
curl -sk "https://your-domain/webui/3100/api/version?token=YOUR_TOKEN"
```

## 常见问题排查

### iframe 空白

1. 打开浏览器开发者工具 → Console
2. 检查是否有 `No routes matched location` 错误 → basename 未注入
3. 检查是否有 `mixed content` 错误 → HTTPS 配置问题
4. 检查 Network 面板，确认 JS/CSS 返回 200

### JS 文件 sub_filter 不生效

1. 检查上游返回的 Content-Type：
   ```bash
   curl -sI http://127.0.0.1:3100/assets/index-xxx.js | grep Content-Type
   ```
2. 确保 `sub_filter_types` 包含上游实际返回的 Content-Type
3. 不要使用 `proxy_hide_header Content-Type`，这会阻止 sub_filter 工作

### basename 注入失效（webui 升级后）

webui 重新打包后，minified 变量名会改变。需要：

1. 在服务器上查看新的 JS 文件，找到 BrowserRouter 的实例化位置：
   ```bash
   grep -oP '.{0,5}jsx\)\(\w+,\{children:.{0,30}' /path/to/webui/dist/static/assets/index-*.js
   ```
2. 更新 sub_filter 中的变量名（如 `mi` → 新名称）

### 远程会话 "Not Found" 错误

**现象**：创建远程工作区时返回 "Failed to create remote session: Not Found"。

**原因**：如果 nginx 配置中使用了 `sub_filter '`/api/'` 全局重写 API 路径，Open-ACE API 调用（如 `/api/remote/sessions/{id}`）也会被加上 `/webui/{port}` 前缀，被错误代理到 webui 端口。

**解决**：使用 fetch/EventSource 拦截器代替 sub_filter 全局重写（见"问题 C"）。

### 端口范围不匹配

如果你的 `config.json` 中 `port_range_start` / `port_range_end` 不是 3100-3200，需要更新 nginx 中的正则表达式。

## 仅 HTTP 部署

如果不需要 HTTPS，可以简化配置：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # WebUI 代理（同上，无需 SSL 配置）
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)(/.*)?$ {
        # ... 同 HTTPS 配置中的 location 块
    }

    # 主应用
    location / {
        proxy_pass http://127.0.0.1:5000;
        # ...
    }
}
```

此时 Flask 不需要 ProxyFix，也不需要 URL 转换逻辑。iframe 可以直接使用 `http://ip:port` 地址。
