# 常见问题

本文档收集了用户在日常使用 Open ACE 过程中可能遇到的常见问题及解决方案。

---

## 目录

**一、安装部署**
- Docker 启动失败：数据库连接超时
- 端口冲突：5000 端口被占用
- SECRET_KEY 未设置：生产环境启动失败
- 配置文件找不到

**二、登录认证**
- 登录失败：用户名或密码错误
- 会话过期：自动退出登录
- 账户被禁用：无法登录
- 权限不足：无法访问管理功能
- 修改密码失败：密码长度不足

**三、工作区与项目管理**
- 项目创建失败：路径权限不足
- 项目路径不存在或无法访问
- 项目已存在：重复创建
- 多用户模式 Workspace 启动失败
- Workspace 实例数达到上限

**四、会话与 AI 交互**
- 配额超限：Workspace 被禁用
- 远程机器离线：无法创建远程会话
- 请求超时或网络错误
- 会话找不到

**五、系统设置**
- 语言切换
- 主题切换（暗色/亮色）
- 页面刷新后设置丢失

---

## 一、安装部署

### Docker 启动失败：数据库连接超时

**问题现象：** 启动 Docker 容器时，日志显示：`ERROR: PostgreSQL not ready after 60s. Exiting.`

**可能原因：**
1. PostgreSQL 容器尚未完成初始化
2. 数据库连接参数配置错误
3. Docker 网络问题导致容器间无法通信

**解决步骤：**
1. 检查 PostgreSQL 容器状态：`docker compose ps`
2. 查看 PostgreSQL 日志：`docker compose logs postgres`
3. 如果 PostgreSQL 正在初始化，等待完成后重启：`docker compose restart open-ace-web`
4. 检查数据库连接参数是否正确

**预防措施：** 确保 docker-compose.yml 中配置了 depends_on 和 healthcheck

---

### 端口冲突：5000 端口被占用

**问题现象：** 启动时报错：`Error: Address already in use (0.0.0.0:5000)`

**可能原因：**
1. 其他服务已占用 5000 端口
2. 之前的 Open ACE 进程未完全停止

**解决步骤：**
1. 查看端口占用：`lsof -i :5000` 或 `netstat -tlnp | grep 5000`
2. 停止占用端口的进程：`kill -9 <PID>` 或 `docker compose down`
3. 更换端口启动：`PORT=8080 docker compose up -d`

---

### SECRET_KEY 未设置：生产环境启动失败

**问题现象：** 容器启动失败，日志显示：`RuntimeError: SECRET_KEY environment variable must be set in production!`

**可能原因：**
1. 生产环境未配置 SECRET_KEY 环境变量
2. 使用了默认的开发密钥

**解决步骤：**
1. 设置 SECRET_KEY 环境变量：`echo "SECRET_KEY=$(openssl rand -hex 32)" > .env`
2. 在 docker-compose.yml 中配置环境变量
3. 重启容器

---

### 配置文件找不到

**问题现象：** 启动后 Workspace 功能无法使用，日志显示：`Config file not found: ~/.open-ace/config.json`

**解决步骤：**
1. 创建配置目录和文件：`mkdir -p ~/.open-ace` 并复制示例配置
2. 编辑配置文件修改 host_name 等参数
3. 重启服务

---

## 二、登录认证

### 登录失败：用户名或密码错误

**问题现象：** 登录页面提示"用户名或密码错误"或"Invalid username or password"

**解决步骤：**
1. 首次登录使用默认管理员账号：用户名 `admin`，密码 `admin123`
2. 如果默认密码无效，联系管理员重置密码
3. 检查用户是否存在

---

### 会话过期：自动退出登录

**问题现象：** 使用一段时间后，页面自动跳转到登录页

**可能原因：**
1. 会话有效期已过（默认 24 小时）
2. 浏览器 Cookie 被清除
3. 服务重启导致会话失效

**解决步骤：** 重新登录即可恢复使用

---

### 账户被禁用：无法登录

**问题现象：** 登录失败，提示"Account is disabled"

**解决步骤：** 联系管理员重新启用账户：
```bash
docker compose exec postgres psql -U ace -d ace -c "UPDATE users SET is_active=true WHERE username='xxx';"
```

---

### 权限不足：无法访问管理功能

**问题现象：** 访问管理页面时提示"Admin access required"

**解决步骤：**
1. 检查当前用户角色
2. 联系管理员修改用户角色为 admin

---

### 修改密码失败：密码长度不足

**问题现象：** 修改密码时提示"New password must be at least 8 characters"

**解决步骤：**
1. 确保新密码至少 8 个字符
2. 确保新密码与当前密码不同

---

## 三、工作区与项目管理

### 项目创建失败：路径权限不足

**问题现象：** 创建项目时提示"Permission denied to create directory"

**解决步骤：**
1. 检查用户 system_account 是否有权限：`sudo chown -R <user>:<group> /path`
2. 授权或更换路径
3. 多用户模式下默认路径：`/workspace/<username>/`

---

### 项目路径不存在或无法访问

**问题现象：** 打开项目时提示"Directory does not exist"

**解决步骤：**
1. 确认路径存在且为目录
2. 如路径不存在，重新创建项目

---

### 项目已存在：重复创建

**问题现象：** 创建项目时提示"Project already exists"

**解决步骤：** 使用不同的路径创建新项目，或删除已有项目后重新创建

---

### 多用户模式 Workspace 启动失败

**问题现象：** 进入工作区时提示"Failed to get user workspace URL"

**可能原因：**
1. qwen-code-webui 未安装或路径配置错误
2. 用户 system_account 系统账户不存在
3. sudo 配置问题

**解决步骤：**
1. 检查 qwen-code-webui 是否可用：`which qwen-code-webui`
2. 检查用户 system_account 是否存在：`id <account>`
3. 检查 sudoers 配置
4. 查看启动日志：`tail -f /tmp/open-ace-*.log`

---

### Workspace 实例数达到上限

**问题现象：** 创建新会话时提示"Maximum instances (30) reached"

**解决步骤：**
1. 等待空闲实例自动清理（默认 30 分钟超时）
2. 管理员修改配置增加上限（max_instances）

---

## 四、会话与 AI 交互

### 配额超限：Workspace 被禁用

**问题现象：** 工作区页面显示配额超限提示，无法继续使用 AI 功能

**可能原因：**
1. 日/月 Token 使用量超过配额限制
2. 日/月请求次数超过配额限制

**解决步骤：**
1. 在 Dashboard 页面查看 Usage Overview
2. 等待配额重置（日配额每日重置，月配额每月重置）
3. 联系管理员调整配额

---

### 远程机器离线：无法创建远程会话

**问题现象：** 创建远程会话时提示"Failed to create remote session"

**可能原因：**
1. 远程 Agent 未运行或网络不通
2. Agent 注册失效
3. 用户未被分配到该机器

**解决步骤：**
1. 检查远程机器状态
2. 确认 Agent 服务运行：`systemctl status open-ace-agent`
3. 重新注册 Agent
4. 确认用户已分配到机器

---

### 请求超时或网络错误

**问题现象：** API 请求失败，提示"Request timed out"或"Network error"

**可能原因：**
1. 网络连接不稳定
2. 服务端响应慢或负载高
3. 请求超时（默认 30 秒）

**解决步骤：**
1. 检查网络连接状态
2. 刷新页面重试（前端会自动重试 3 次）
3. 检查服务状态

---

### 会话找不到

**问题现象：** 打开会话详情时提示"Session not found"

**可能原因：**
1. 会话已被删除或过期
2. 会话 ID 错误
3. 用户无权访问该会话

**解决步骤：**
1. 确认会话 ID 正确
2. 在会话列表中查找有效会话
3. 如果是远程会话，确认远程机器在线

---

## 五、系统设置

### 语言切换

**解决步骤：** 在登录页面或设置页面选择语言，支持：
- English（英语）
- 中文（简体中文）
- 日本語（日语）
- 한국어（韩语）

---

### 主题切换（暗色/亮色）

**解决步骤：** 在界面顶部或设置中找到主题切换按钮，选择 Light / Dark 模式

---

### 页面刷新后设置丢失

**可能原因：** 浏览器禁用了本地存储

**解决步骤：** 确保浏览器允许使用 localStorage，重新设置偏好

---

## 更多帮助

如以上方案未能解决问题，请：
1. 查看 GitHub Issues 是否有相关问题：https://github.com/open-ace/open-ace/issues
2. 提交新的 Issue，附上问题描述、复现步骤、环境信息、相关日志
