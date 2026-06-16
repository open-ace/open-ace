#!/usr/bin/env bash
#
# clear_frontend_cache.sh
#
# 清理 Open ACE 前端缓存并重新构建。
#
# 做三件事：
#   1. 清理后端本地的 Vite 构建产物（node_modules/.vite + static/js/dist）
#   2. 重新构建前端，生成带新 hash 的产物
#   3. 打印浏览器侧清理指引（注销 Service Worker + 清 Cache API）
#
# 注意：Service Worker / Cache API 是浏览器侧存储，脚本无法直接清理，
#       必须在浏览器里操作一次（脚本最后会给出最省事的方法）。
#
# 用法：
#   ./scripts/clear_frontend_cache.sh          # 清理 + 重建
#   ./scripts/clear_frontend_cache.sh --no-build   # 只清理，不重建
#
set -euo pipefail

# ---------- 颜色输出 ----------
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; NC=''
fi

info()  { printf "${CYAN}[INFO]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
err()   { printf "${RED}[ERR]${NC} %s\n" "$*" >&2; }

# ---------- 路径 ----------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
DIST_DIR="$REPO_ROOT/static/js/dist"
VITE_CACHE="$FRONTEND_DIR/node_modules/.vite"

# ---------- 选项 ----------
DO_BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *) err "未知参数: $arg"; exit 1 ;;
  esac
done

info "仓库根目录: $REPO_ROOT"

# ---------- Step 1: 清理构建产物 ----------
info "Step 1/3: 清理前端构建缓存"

if [[ -d "$VITE_CACHE" ]]; then
  rm -rf "$VITE_CACHE"
  ok "已清理 Vite 依赖缓存: $VITE_CACHE"
else
  info "Vite 缓存不存在，跳过"
fi

if [[ -d "$DIST_DIR" ]]; then
  # 只删构建产物，保留目录本身
  find "$DIST_DIR" -mindepth 1 -delete
  ok "已清理后端静态产物: $DIST_DIR"
else
  warn "静态产物目录不存在: $DIST_DIR（可能尚未构建过）"
  mkdir -p "$DIST_DIR"
fi

# ---------- Step 2: 重新构建 ----------
if [[ "$DO_BUILD" -eq 1 ]]; then
  info "Step 2/3: 重新构建前端"
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    info "node_modules 不存在，先安装依赖"
    (cd "$FRONTEND_DIR" && npm install)
  fi
  (cd "$FRONTEND_DIR" && npm run build)
  ok "前端构建完成"
else
  warn "Step 2/3: --no-build，跳过重新构建"
fi

# ---------- Step 3: 浏览器清理指引 ----------
echo
printf "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${YELLOW}Step 3/3: 浏览器侧缓存清理（必须手动做一次）${NC}\n"
printf "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
cat <<'TIP'
服务端产物已更新，但浏览器里的 Service Worker + Cache API 仍可能命中旧缓存。
请在打开 Open ACE 页面的浏览器里做【其中一种】操作：

方法 A（推荐，最彻底）：
  1. F12 打开 DevTools
  2. 切到 Application（应用）标签
  3. 左侧 Service Workers → 勾选 "Update on reload"，再点 "Unregister"
  4. 左侧 Storage → 点 "Clear site data"（清空站点数据）
  5. 关闭 DevTools，刷新页面（Cmd+R / Ctrl+R）

方法 B（最快）：
  1. F12 → Application → Service Workers → Unregister
  2. 无痕窗口重新打开页面

方法 C（一行代码）：
  在页面 Console 粘贴并回车（会刷新页面）：
    navigator.serviceWorker.getRegistrations()
      .then(rs => Promise.all(rs.map(r => r.unregister())))
      .then(() => Promise.all((await caches.keys()).map(k => caches.delete(k))))
      .then(() => location.reload());
TIP
echo
ok "全部完成。请按上面指引刷新浏览器，即可看到 PR #1016 的新 UI。"
