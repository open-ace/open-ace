## Round 3 非阻塞问题已修复 ✅

### 🟡 → ✅ fs.py 双重认证已移除

每个路由移除了 `get_webui_user()` / `get_current_user()` 的第二次 token 验证，改为直接使用 `@auth_required` 设置的 `g.user`：

```python
# 修复前：每次请求做 2 次 token 验证
user, error, code = get_webui_user()
if not user:
    user, error, code = get_current_user()

# 修复后：使用 @auth_required 已设置的 g.user
user = g.user
```

### 🟡 → ✅ fetch.py 双重认证已移除

移除 `before_request` + `@auth_required`，改为逐路由装饰器：
- `/fetch/data`, `/fetch/status` → `@auth_required`
- `/fetch`, `/fetch/remote` → `@admin_required`（包含完整 auth + role check）
- `/data-status` → `@auth_required`（不变）

每个请求现在只做一次认证验证。

Commit: `fbff9da`
