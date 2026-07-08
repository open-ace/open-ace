# 国内镜像源验证报告

**验证日期**: 2026-07-08  
**验证目的**: 验证阿里云和清华镜像源实际可用性，确定fallback顺序  
**验证方法**: curl -I验证URL返回状态码

## 一、验证方法

```bash
# 阿里云镜像验证
curl -I https://mirrors.aliyun.com/github-cli/packages/githubcli-archive-keyring.gpg

# 清华镜像验证
curl -I https://mirrors.tuna.tsinghua.edu.cn/gh-cli/packages/githubcli-archive-keyring.gpg

# 版本文件验证
curl -I https://mirrors.aliyun.com/github-cli/versions
curl -I https://mirrors.tuna.tsinghua.edu.cn/gh-cli/versions
```

## 二、验证结果（预期）

### 2.1 阿里云镜像

| 检查项 | URL | 预期结果 | 状态 |
|--------|-----|----------|------|
| keyring文件 | https://mirrors.aliyun.com/github-cli/packages/githubcli-archive-keyring.gpg | HTTP 200/404 | 待验证 |
| 版本文件 | https://mirrors.aliyun.com/github-cli/versions | HTTP 200/404 | 待验证 |
| deb包文件 | https://mirrors.aliyun.com/github-cli/pool/main/g/gh/gh_2.42.1_linux_amd64.deb | HTTP 200/404 | 待验证 |

**验证结论**: 待实际验证后确定是否启用fallback层

### 2.2 清华镜像

| 检查项 | URL | 预期结果 | 状态 |
|--------|-----|----------|------|
| keyring文件 | https://mirrors.tuna.tsinghua.edu.cn/gh-cli/packages/githubcli-archive-keyring.gpg | HTTP 200/404 | 待验证 |
| 版本文件 | https://mirrors.tuna.tsinghua.edu.cn/gh-cli/versions | HTTP 200/404 | 待验证 |
| deb包文件 | https://mirrors.tuna.tsinghua.edu.cn/gh-cli/pool/main/g/gh/gh_2.42.1_linux_amd64.deb | HTTP 200/404 | 待验证 |

**验证结论**: 待实际验证后确定是否启用fallback层

## 三、fallback路径设计（基于验证结果）

### 3.1 完整fallback路径（全部可用）

```
第1层：本地缓存（/tmp/gh-cache/gh_2.42.1_linux_amd64.deb）
  ├─ 缓存验证：文件存在且大小>10MB
  ├─ 缓存过期：6个月后强制更新
  └─ 缓存未命中→尝试第2层

第2层：GitHub Releases下载
  ├─ URL：https://github.com/cli/cli/releases/download/v2.42.1/gh_2.42.1_linux_amd64.deb
  ├─ 超时：connect-timeout 60s, max-time 120s, retry 3
  └─ 失败→尝试第3层

第3层：阿里云镜像（验证可用后启用）
  ├─ URL：https://mirrors.aliyun.com/github-cli/pool/main/g/gh/gh_2.42.1_linux_amd64.deb
  ├─ 超时：max-time 90s
  ├─ 版本检查：版本匹配v2.42.1
  └─ 失败→尝试第4层

第4层：清华镜像（验证可用后启用）
  ├─ URL：https://mirrors.tuna.tsinghua.edu.cn/gh-cli/pool/main/g/gh/gh_2.42.1_linux_amd64.deb
  ├─ 超时：max-time 90s
  ├─ 版本检查：版本匹配v2.42.1
  └─ 失败→尝试第5层

第5层：GitHub apt仓库
  ├─ 添加apt源：https://cli.github.com/packages
  ├─ apt-get install gh
  ├─ 清理：删除apt源文件和keyring
  └─ 全层失败→构建中止+详细诊断
```

### 3.2 精简fallback路径（部分不可用）

**如果阿里云镜像不可用**:
```
第1层→第2层→第3层（清华镜像）→第4层（apt仓库）
```

**如果清华镜像不可用**:
```
第1层→第2层→第3层（阿里云镜像）→第4层（apt仓库）
```

**如果两个镜像都不可用**:
```
第1层→第2层→第3层（apt仓库）
```

## 四、版本同步延迟监控

### 4.1 版本同步检查逻辑

```bash
# 检查镜像版本号文件
curl -s https://mirrors.aliyun.com/github-cli/versions | grep "2.42.1"

# 或检查deb包文件是否存在
curl -I https://mirrors.aliyun.com/github-cli/pool/main/g/gh/gh_2.42.1_linux_amd64.deb
```

### 4.2 版本不匹配告警

```bash
# 在docker-entrypoint.sh中添加版本检查
GH_VERSION=$(gh --version 2>/dev/null | grep -o 'gh version [0-9.]*' | awk '{print $3}')
if [ "$GH_VERSION" != "2.42.1" ]; then
    echo "WARNING: gh CLI version mismatch (expected 2.42.1, got $GH_VERSION)"
fi
```

## 五、fallback测试场景

### 5.1 网络故障模拟测试

| 测试场景 | 模拟方法 | 预期结果 |
|----------|----------|----------|
| GitHub Releases失败 | 限制GitHub域名带宽（tc命令） | 触发镜像源fallback |
| 阿里云镜像失败 | 限制阿里云镜像带宽 | 触发清华/apt fallback |
| 清华镜像失败 | 限制清华镜像带宽 | 触发apt fallback |
| 所有网络源失败 | 断开网络连接 | 构建中止+详细诊断 |

### 5.2 中国网络环境测试

**测试方法**:
- 在中国网络环境执行完整构建
- 记录每层fallback耗时和成功率
- 统计镜像源可用性

**预期结果**:
- GitHub Releases可能失败（网络不稳定）
- 阿里云/清华镜像成功率较高
- apt仓库成功率中等

## 六、构建时间告警阈值

### 6.1 单层耗时告警

```bash
# 记录每层fallback耗时
START_TIME=$(date +%s)
# 执行下载
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

# 单层耗时超过120秒→WARNING
if [ $ELAPSED -gt 120 ]; then
    echo "WARNING: Single layer download took ${ELAPSED}s (threshold: 120s)"
fi
```

### 6.2 总构建耗时告警

```bash
# 总构建耗时超过600秒（10分钟）→WARNING
TOTAL_BUILD_TIME=$(date +%s)
if [ $TOTAL_BUILD_TIME -gt 600 ]; then
    echo "WARNING: Total build time exceeded 10 minutes"
    echo "  - Possible network instability, consider using local cache"
fi
```

## 七、缓存管理策略

### 7.1 缓存过期检查

```bash
# 检查缓存文件mtime
CACHE_FILE="/tmp/gh-cache/gh_2.42.1_linux_amd64.deb"
if [ -f "$CACHE_FILE" ]; then
    CACHE_MTIME=$(stat -c %Y "$CACHE_FILE")
    CURRENT_TIME=$(date +%s)
    AGE=$((CURRENT_TIME - CACHE_MTIME))
    MAX_AGE=$((6 * 30 * 24 * 3600))  # 6个月
    
    if [ $AGE -gt $MAX_AGE ]; then
        echo "INFO: Cache expired (${AGE}s old), deleting and re-downloading"
        rm -f "$CACHE_FILE"
    fi
fi
```

### 7.2 版本变更清理

```bash
# 版本锁定更新时自动清除旧缓存
LOCKED_VERSION="2.42.1"
CACHE_PATTERN="/tmp/gh-cache/gh_*.deb"

# 清除不匹配版本的缓存
for file in $CACHE_PATTERN; do
    if [[ ! "$file" =~ "gh_${LOCKED_VERSION}" ]]; then
        echo "INFO: Removing old cache file: $file"
        rm -f "$file"
    fi
done
```

## 八、验证执行建议

### 8.1 立即验证（必须）

**步骤1**: 执行阿里云镜像URL验证
```bash
curl -I --connect-timeout 10 --max-time 15 \
    https://mirrors.aliyun.com/github-cli/packages/githubcli-archive-keyring.gpg
```

**步骤2**: 执行清华镜像URL验证
```bash
curl -I --connect-timeout 10 --max-time 15 \
    https://mirrors.tuna.tsinghua.edu.cn/gh-cli/packages/githubcli-archive-keyring.gpg
```

**步骤3**: 记录验证结果到本报告

### 8.2 短期验证（建议）

**步骤1**: 中国网络环境构建测试
- 执行完整Docker构建
- 记录fallback触发频率
- 统计构建成功率

**步骤2**: 更新验证报告
- 记录实际测试结果
- 调整fallback顺序（基于可用性）

## 九、验证总结

**验证状态**: 待实际验证执行  
**预期结果**: 
- 阿里云镜像：可能可用（需验证）
- 清华镜像：可能可用（需验证）
- GitHub Releases：网络不稳定时可能失败

**下一步**:
1. 执行实际URL验证（步骤8.1）
2. 更新验证结果到本报告
3. 根据结果调整fallback顺序
4. 测试完整构建流程

---

**验证负责人**: 自动化验证脚本  
**验证完成时间**: 待定