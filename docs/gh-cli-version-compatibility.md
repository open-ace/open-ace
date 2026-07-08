# gh CLI版本兼容性管理文档

**文档版本**: 1.0  
**创建日期**: 2026-07-08  
**锁定版本**: gh CLI v2.42.1

## 一、版本锁定策略

### 1.1 当前锁定版本

| 项目 | 版本 | 锁定日期 | 过期日期 |
|------|------|----------|----------|
| gh CLI | v2.42.1 | 2026-07-08 | 2027-07-08（1年） |

### 1.2 锁定原因

| 原因类型 | 详细说明 |
|----------|----------|
| **稳定性** | v2.42.1经过充分测试，已知版本稳定性高 |
| **API兼容性** | 完全兼容GitHub API v3，所有autonomous工作流API调用验证通过 |
| **中国网络适配** | 已验证镜像源同步，中国网络环境下构建成功率高 |
| **功能完整性** | 覆盖autonomous工作流所有必需功能（issue、pr、repo操作） |
| **安全审查** | 已通过安全审查，无已知安全漏洞 |

### 1.3 过期策略

| 过期触发条件 | 处理流程 |
|--------------|----------|
| **锁定时间超过1年** | 强制评估流程（每季度） |
| **GitHub发布安全漏洞** | 立即评估+紧急更新（Issue #1514） |
| **GitHub API变更通知** | 立即评估+必须更新 |
| **构建失败（版本不匹配）** | 立即评估+快速验证 |
| **新版本重大功能更新** | 高优先级评估（可选） |

## 二、版本兼容性测试流程

### 2.1 定期评估流程（每季度）

#### Q1评估流程（1月）

```
步骤1：获取gh CLI最新稳定版本号
  ├─ 访问GitHub Releases页面
  ├─ 提取最新稳定版本号
  └─ 对比锁定版本（v2.42.1）

步骤2：检查GitHub API变更通知
  ├─ 查看GitHub API变更日志
  ├─ 确认是否有API变更影响autonomous工作流
  └─ 记录变更详情

步骤3：构建测试镜像（新版本）
  ├─ 更新Dockerfile中的版本号
  ├─ 构建测试镜像（新版本）
  └─ 记录构建时间

步骤4：运行autonomous工作流测试套件
  ├─ 运行preparation阶段测试（git clone、branch）
  ├─ 运行execution阶段测试（git commit、push）
  ├─ 运行pr阶段测试（gh pr create、view、merge）
  ├─ 运行issue阶段测试（gh issue view、create）
  └─ 统计测试通过率

步骤5：性能对比测试（新旧版本）
  ├─ Token验证响应时间对比
  ├─ PR创建响应时间对比
  ├─ Issue查询响应时间对比
  └─ 构建时间对比（各层fallback耗时）

步骤6：中国网络环境测试（镜像同步）
  ├─ 测试镜像源下载成功率
  ├─ 验证镜像版本同步延迟
  └─ 统计构建成功率

步骤7：输出测试报告
  ├─ 版本对比结果
  ├─ 性能对比数据
  ├─ 兼容性问题列表
  ├─ 建议：是否更新版本
  └─ 技术负责人审批

步骤8：更新锁定版本（如批准）
  ├─ 更新Dockerfile版本号
  ├─ 更新本文档锁定版本
  ├─ 重置过期时间（从批准日期开始）
  └─ 发布更新公告（邮件+CONTRIBUTING.md）
```

#### Q2/Q3/Q4评估流程（4月/7月/10月）

同Q1流程，增加：
- 季度评估报告归档
- 与前季度结果对比

### 2.2 安全漏洞响应流程（紧急）

```
触发条件：GitHub发布gh CLI安全漏洞公告

步骤1：立即获取修复版本
  ├─ 查看安全漏洞详情
  ├─ 获取修复版本号
  └─ 评估漏洞影响范围

步骤2：构建测试镜像（修复版本）
  ├─ 更新Dockerfile版本号（修复版本）
  ├─ 快速构建测试镜像
  └─ 验证构建成功

步骤3：运行安全测试套件
  ├─ 验证漏洞修复有效性
  ├─ 运行关键功能快速测试
  ├─ 测试sudoers白名单有效性
  └─ 确认无新安全问题

步骤4：兼容性快速测试（关键功能）
  ├─ Token验证测试
  ├─ PR创建测试
  ├─ Issue查询测试
  └─ 统计测试通过率

步骤5：技术负责人审批→紧急更新
  ├─ 评估安全风险（高→紧急）
  ├─ 技术负责人审批
  └─ 决定更新方案

步骤6：发布更新公告
  ├─ 邮件通知：技术负责人、运维团队
  ├─ 更新CONTRIBUTING.md
  ├─ 发布安全公告（详细说明漏洞和修复）
  └─ 更新锁定版本（重置过期时间）
```

### 2.3 API变更响应流程（必须）

```
触发条件：GitHub发布API变更通知

步骤1：检查变更影响范围
  ├─ 分析API变更内容
  ├─ 检查autonomous工作流使用情况
  ├─ 评估兼容性影响
  └─ 记录变更详情

步骤2：更新gh CLI版本（支持新API）
  ├─ 查看gh CLI更新版本
  ├─ 确认新版本支持API变更
  └─ 获取新版本号

步骤3：构建测试镜像
  ├─ 更新Dockerfile版本号
  ├─ 构建测试镜像（新版本）
  └─ 验证构建成功

步骤4：运行API兼容性测试
  ├─ 测试所有gh API调用（github_ops.py）
  ├─ 验证新API功能正常
  ├─ 验证旧API兼容性（如有）
  └─ 统计测试通过率

步骤5：更新autonomous工作流代码（如有必要）
  ├─ 修改github_ops.py（适配新API）
  ├─ 运行完整测试套件
  └─ 确认功能正常

步骤6：技术负责人审批→更新
  ├─ 提交兼容性测试报告
  ├─ 技术负责人审批
  └─ 决定更新方案

步骤7：发布兼容性更新公告
  ├─ 邮件通知：技术负责人、开发团队
  ├─ 更新CONTRIBUTING.md
  ├─ 发布兼容性公告（说明API变更和代码修改）
  └─ 更新锁定版本（重置过期时间）
```

## 三、测试覆盖定义

### 3.1 autonomous工作流测试套件

| 测试阶段 | 测试内容 | 测试文件 | 验证标准 |
|----------|----------|----------|----------|
| preparation | git clone, git branch | tests/issues/*/test_git_network_retry.py | 命令执行成功 |
| execution | git commit, git push | tests/issues/*/test_github_ops.py | 命令执行成功 |
| pr | gh pr create, gh pr view, gh pr merge | tests/issues/*/test_github_ops.py | 命令执行成功 |
| issue | gh issue view, gh issue create | tests/issues/*/test_github_ops.py | 命令执行成功 |
| **完整工作流** | autonomous端到端测试 | tests/integration/test_autonomous_workflow.py | **100%通过** |

### 3.2 API兼容性测试

| API调用 | 测试方法 | 验证标准 |
|---------|----------|----------|
| `gh api user` | Token验证测试 | 返回username |
| `gh api repos/*/pulls/*/comments` | PR评论查询测试 | 返回评论列表 |
| `gh api repos/*/issues/*/comments` | Issue评论查询测试 | 返回评论列表 |
| 所有github_ops.py API调用 | 完整测试套件 | 100%通过 |

### 3.3 性能对比测试

| 性能指标 | 测试方法 | 告警阈值 |
|----------|----------|----------|
| Token验证响应时间 | subprocess.run耗时记录 | >5秒→WARNING |
| PR创建响应时间 | _run_gh耗时记录 | >10秒→WARNING |
| Issue查询响应时间 | _run_gh耗时记录 | >5秒→WARNING |
| 构建时间 | Docker构建总耗时 | >600秒→WARNING |

### 3.4 网络容错测试

| 测试场景 | 模拟方法 | 验证标准 |
|----------|----------|----------|
| GitHub Releases下载 | 真实环境测试 | 成功率统计 |
| 阿里云镜像下载 | 真实环境测试 | 成功率统计 |
| 清华镜像下载 | 真实环境测试 | 成功率统计 |
| apt仓库下载 | 真实环境测试 | 成功率统计 |
| fallback路径测试 | 网络故障模拟 | fallback触发验证 |

### 3.5 安全测试

| 安全测试 | 测试内容 | 验证标准 |
|----------|----------|----------|
| sudoers白名单测试 | 参数绕过攻击测试 | 100%阻断 |
| 危险命令阻断测试 | repo delete、push --force测试 | 权限拒绝 |
| 安全漏洞修复验证 | 漏洞修复有效性测试 | 漏洞已修复 |

## 四、构建缓存过期机制

### 4.1 缓存过期策略

| 缓存项 | 过期时间 | 检查时机 | 清理方式 |
|--------|----------|----------|----------|
| gh deb包缓存 | **6个月** | 每次构建 | 自动删除 |
| apt源缓存 | 构建结束 | 构建完成 | 立即清理 |
| keyring缓存 | 构建结束 | 构建完成 | 立即清理 |

### 4.2 缓存过期检查逻辑

```bash
# 缓存过期检查脚本
CACHE_DIR="/tmp/gh-cache"
CACHE_FILE="${CACHE_DIR}/gh_2.42.1_linux_amd64.deb"
MAX_AGE_DAYS=180  # 6个月

if [ -f "$CACHE_FILE" ]; then
    CACHE_MTIME=$(stat -c %Y "$CACHE_FILE" 2>/dev/null)
    CURRENT_TIME=$(date +%s)
    AGE_DAYS=$(( (CURRENT_TIME - CACHE_MTIME) / 86400 ))
    
    if [ $AGE_DAYS -gt $MAX_AGE_DAYS ]; then
        echo "INFO: Cache expired (${AGE_DAYS} days old), deleting"
        rm -f "$CACHE_FILE"
        # 强制从网络下载新版本
    else
        echo "INFO: Cache valid (${AGE_DAYS} days old), using cached file"
        # 使用缓存文件
    fi
fi
```

### 4.3 版本变更触发清理

```bash
# 版本变更时自动清除旧缓存
LOCKED_VERSION="2.42.1"
CACHE_DIR="/tmp/gh-cache"

# 清除不匹配版本的缓存文件
find "$CACHE_DIR" -name "gh_*.deb" ! -name "gh_${LOCKED_VERSION}_*.deb" -delete

echo "INFO: Cleaned old version cache files, keeping gh_${LOCKED_VERSION}"
```

### 4.4 定期清理策略

```bash
# 每周清理过期缓存（可在cron中配置）
CACHE_DIR="/tmp/gh-cache"
MAX_AGE_DAYS=180

find "$CACHE_DIR" -name "gh_*.deb" -mtime +${MAX_AGE_DAYS} -delete

echo "INFO: Weekly cache cleanup completed"
```

### 4.5 手动清理脚本

```bash
# scripts/clean-gh-cache.sh
#!/bin/bash
set -e

CACHE_DIR="/tmp/gh-cache"

echo "Cleaning gh CLI cache directory: $CACHE_DIR"

# 显示当前缓存文件
if [ -d "$CACHE_DIR" ]; then
    echo "Current cache files:"
    ls -lh "$CACHE_DIR"
    
    # 删除所有缓存
    rm -rf "$CACHE_DIR"
    echo "Cache directory cleaned"
else
    echo "Cache directory not found, no action needed"
fi
```

## 五、版本更新流程

### 5.1 更新决策标准

| 决策因素 | 权重 | 评估标准 |
|----------|------|----------|
| 安全漏洞修复 | **高（紧急）** | 有安全漏洞→立即更新 |
| API兼容性 | **高（必须）** | API变更→立即更新 |
| 测试通过率 | **高（必须）** | <100%→拒绝更新 |
| 性能影响 | 中 | 性能下降>20%→谨慎评估 |
| 中国网络适配 | 中 | 镜像源不可用→谨慎评估 |
| 功能完整性 | 中 | 功能缺失→谨慎评估 |

### 5.2 更新审批流程

```
提交测试报告→技术负责人审查→评估风险等级→
  ├─ 高风险（安全漏洞）：立即批准→紧急更新
  ├─ 中风险（API变更）：必须批准→立即更新
  ├─ 低风险（功能更新）：评估后决定→可选更新
  └─ 不可用（测试失败）：拒绝更新→维持旧版本+记录原因
```

### 5.3 更新公告内容

```
邮件通知格式：
标题：[Open ACE] gh CLI版本更新通知

正文：
- 旧版本：v2.42.1
- 新版本：vX.X.X
- 更新原因：[安全漏洞修复/API变更/功能更新/定期评估]
- 更新日期：YYYY-MM-DD
- 测试报告链接：[链接]
- 影响范围：[autonomous工作流/Docker构建/API调用]
- 操作建议：[重新构建镜像/更新代码/无需操作]
- 过期日期：YYYY-MM-DD（新版本锁定1年）

CONTRIBUTING.md更新：
- 版本锁定记录：旧版本→新版本
- 更新日期记录
- 更新原因记录
- 过期时间记录
```

## 六、版本兼容性记录

### 6.1 版本历史记录

| 版本 | 锁定日期 | 解锁日期 | 锁定原因 | 解锁原因 |
|------|----------|----------|----------|----------|
| v2.42.1 | 2026-07-08 | - | 初始版本，稳定性高 | - |

### 6.2 兼容性问题记录

| 日期 | 版本 | 问题类型 | 问题详情 | 解决方案 |
|------|------|----------|----------|----------|
| - | - | - | - | - |

### 6.3 测试报告归档

| 评估日期 | 版本对比 | 测试通过率 | 性能对比 | 建议 | 审批结果 |
|----------|----------|------------|----------|------|----------|
| - | - | - | - | - | - |

## 七、CONTRIBUTING.md更新建议

### 7.1 版本管理章节

在CONTRIBUTING.md中添加以下章节：

```markdown
## gh CLI版本锁定策略

### 当前锁定版本
- **gh CLI**: v2.42.1
- **锁定日期**: 2026-07-08
- **过期日期**: 2027-07-08（1年）

### 版本更新周期
- **定期评估**: 每季度（Q1/Q2/Q3/Q4）
- **安全漏洞**: 立即评估+紧急更新
- **API变更**: 立即评估+必须更新
- **功能更新**: 高优先级评估（可选）

### 版本更新流程
1. 获取最新版本号
2. 构建测试镜像（新版本）
3. 运行autonomous工作流测试套件
4. 性能对比测试
5. 中国网络环境测试
6. 输出测试报告
7. 技术负责人审批
8. 更新锁定版本+重置过期时间
9. 发布更新公告（邮件+CONTRIBUTING.md）

### 版本兼容性测试
- autonomous工作流测试套件（100%通过）
- API兼容性测试（所有gh API调用）
- 性能对比测试（响应时间对比）
- 网络容错测试（fallback路径测试）
- 安全测试（sudoers白名单测试）

### 构建缓存管理
- **缓存过期时间**: 6个月
- **版本变更触发**: 自动清除旧缓存
- **定期清理**: 每周检查过期缓存
- **手动清理**: scripts/clean-gh-cache.sh

### 相关文档
- 版本兼容性管理文档：docs/gh-cli-version-compatibility.md
- sudoers审计报告：docs/sudoers-audit-report.md
- 镜像源验证报告：docs/mirror-source-validation-report.md
```

## 八、监控与告警

### 8.1 版本不匹配告警

```bash
# docker-entrypoint.sh版本检查
GH_VERSION=$(gh --version 2>/dev/null | grep -o 'gh version [0-9.]*' | awk '{print $3}')
EXPECTED_VERSION="2.42.1"

if [ "$GH_VERSION" != "$EXPECTED_VERSION" ]; then
    echo "WARNING: gh CLI version mismatch"
    echo "  - Expected: $EXPECTED_VERSION"
    echo "  - Actual: $GH_VERSION"
    echo "  - Please evaluate compatibility or rebuild image"
fi
```

### 8.2 构建时间告警

```bash
# 构建时间超过600秒→WARNING
BUILD_START=$(date +%s)
# 执行构建
BUILD_END=$(date +%s)
BUILD_TIME=$((BUILD_END - BUILD_START))

if [ $BUILD_TIME -gt 600 ]; then
    echo "WARNING: Docker build took ${BUILD_TIME}s (threshold: 600s)"
    echo "  - Possible network instability"
    echo "  - Consider using local cache"
fi
```

### 8.3 缓存过期告警

```bash
# 缓存接近过期→INFO提示
CACHE_FILE="/tmp/gh-cache/gh_2.42.1_linux_amd64.deb"
MAX_AGE_DAYS=180
WARN_AGE_DAYS=150  # 提前30天提醒

if [ -f "$CACHE_FILE" ]; then
    AGE_DAYS=$(( ($(date +%s) - $(stat -c %Y "$CACHE_FILE")) / 86400 ))
    
    if [ $AGE_DAYS -gt $WARN_AGE_DAYS ]; then
        echo "INFO: Cache approaching expiration (${AGE_DAYS} days old)"
        echo "  - Will expire in $((MAX_AGE_DAYS - AGE_DAYS)) days"
        echo "  - Consider updating gh CLI version"
    fi
fi
```

## 九、总结

**版本锁定**: gh CLI v2.42.1  
**锁定原因**: 稳定性、API兼容性、中国网络适配、功能完整性  
**过期时间**: 2027-07-08（1年）  
**更新触发**: 安全漏洞、API变更、定期评估  
**测试覆盖**: autonomous工作流、API兼容性、性能对比、网络容错、安全测试  
**缓存管理**: 6个月过期、版本变更触发清理、定期清理

**下一步**:
1. 实施定期评估流程（每季度）
2. 配置监控告警（版本不匹配、构建时间）
3. 配置缓存清理cron任务（每周）
4. 更新CONTRIBUTING.md

---

**文档维护人**: 技术团队  
**更新日期**: 2026-07-08