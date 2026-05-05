## Round 2 修复已提交 ✅

两个 🔴 阻塞问题均已修复：

### 🔴 ✅ 已修复: 6 个零认证文件

所有 6 个文件已添加 `@before_request` + `@auth_required`：

| 文件 | 添加方式 | 路由数 |
|------|---------|--------|
| `analysis.py` | `before_request` + `@auth_required` | 12 |
| `fetch.py` | `before_request` + `@auth_required` + `/fetch` `/fetch/remote` 保留 `@admin_required` | 5 |
| `messages.py` | `before_request` + `@auth_required` | 5 |
| `roi.py` | `before_request` + `@auth_required` | 10 |
| `tool_accounts.py` | `before_request` + `@auth_required` | 8 |
| `usage.py` | `before_request` + `@auth_required` | 15 |

共 **55 个路由** 现在受认证保护。

### 🔴 ✅ 已修复: projects.py `system_account` 回退

WebUI fallback 路径已修复：
```python
# 修复前：
g.user = {"id": user_id, "username": ..., "email": ..., "role": ...}  # 无 system_account

# 修复后：
g.user = user  # user_repo.get_user_by_id() 返回的完整对象
```

主路径之前已修复（`g.user = user`），现在两条路径都使用完整 user 对象。

### Baseline 状态

重新生成后仅剩 **6 个 SEC002** 抑制项（projects.py 2 + workspace.py 6），均为 ownership check 在 manager class 中的合理豁免。

Commit: `ad41a83`
