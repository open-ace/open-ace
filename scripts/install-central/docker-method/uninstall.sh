#!/bin/bash
#
# Open ACE - Uninstallation Script
#
# This script removes Open ACE deployment including containers, volumes, and data.
#
# Usage:
#   ./uninstall.sh                  # Interactive uninstall
#   ./uninstall.sh --purge          # Remove all data including database
#   ./uninstall.sh --non-interactive # Non-interactive mode
#   ./uninstall.sh --help           # Show help
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
RUN_USER="${RUN_USER:-open-ace}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/$RUN_USER/open-ace}"
PURGE_DATA=false
NON_INTERACTIVE=false

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  $1${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

prompt_input() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [ "$NON_INTERACTIVE" = true ]; then
        eval "$var_name='$default'"
        return
    fi

    if [ -n "$default" ]; then
        echo -ne "${BLUE}$prompt [${default}]: ${NC}"
    else
        echo -ne "${BLUE}$prompt: ${NC}"
    fi

    read -r value

    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi

    eval "$var_name='$value'"
}

prompt_yesno() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [ "$NON_INTERACTIVE" = true ]; then
        eval "$var_name='$default'"
        return
    fi

    local options="[Y/n]"
    [ "$default" = "n" ] && options="[y/N]"

    echo -ne "${BLUE}$prompt ${options}: ${NC}"
    read -r value

    value=$(echo "$value" | tr '[:upper:]' '[:lower:]')

    if [ -z "$value" ]; then
        value="$default"
    fi

    if [ "$value" = "y" ] || [ "$value" = "yes" ]; then
        eval "$var_name='yes'"
    else
        eval "$var_name='no'"
    fi
}

show_help() {
    echo "Open ACE - Uninstallation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --non-interactive    Run without prompts"
    echo "  --purge              Remove all data including database volumes"
    echo "  --user, -u USER      Run user (default: open-ace)"
    echo "  --deploy-dir DIR     Deployment directory (default: /home/\$USER/open-ace)"
    echo "  --help, -h           Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  RUN_USER             Run user (default: open-ace)"
    echo "  DEPLOY_DIR           Deployment directory"
    echo ""
    echo "Examples:"
    echo "  $0                    # Interactive uninstall (keep database)"
    echo "  $0 --purge            # Remove everything including database"
    echo "  $0 --user myuser      # Uninstall for different user"
    echo "  $0 --non-interactive  # Non-interactive (keep database)"
    echo ""
    echo "Warning:"
    echo "  --purge will permanently delete all data including:"
    echo "  - PostgreSQL database"
    echo "  - Usage records"
    echo "  - User accounts"
    echo "  - Configuration files"
    echo "  - Run user (if created by deploy.sh)"
    echo ""
    exit 0
}

# ============================================================================
# Uninstall Functions
# ============================================================================

check_deployment() {
    print_header "检查部署状态"

    print_info "运行用户: $RUN_USER"
    print_info "部署目录: $DEPLOY_DIR"

    # Check if deployment directory exists
    if [ ! -d "$DEPLOY_DIR" ]; then
        print_warning "部署目录不存在: $DEPLOY_DIR"
        return 1
    fi
    print_success "部署目录存在"

    # Check if docker-compose.yml exists
    if [ ! -f "$DEPLOY_DIR/docker-compose.yml" ]; then
        print_warning "docker-compose.yml 不存在"
        return 1
    fi
    print_success "docker-compose.yml 存在"

    # Check running containers
    local containers=$(docker ps -a --filter "name=open-ace" --format "{{.Names}}" 2>/dev/null || true)
    if [ -n "$containers" ]; then
        print_info "发现的容器:"
        echo "$containers" | while read container; do
            echo "  - $container"
        done
    else
        print_info "没有发现相关容器"
    fi

    return 0
}

stop_containers() {
    print_header "停止容器"

    cd "$DEPLOY_DIR"

    if [ -f "docker-compose.yml" ]; then
        # Stop and remove containers, networks
        docker compose down -v
        print_success "容器和网络已停止"
    else
        # Try to stop containers by name
        for container in open-ace open-ace-postgres; do
            if docker ps --filter "name=$container" --format "{{.Names}}" | grep -q "$container"; then
                docker stop "$container" 2>/dev/null || true
                docker rm "$container" 2>/dev/null || true
                print_success "容器 $container 已停止"
            fi
        done
    fi
}

remove_volumes() {
    print_header "删除数据卷"

    # Get project name from deploy directory
    local project_name=$(basename "$DEPLOY_DIR" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')
    
    # List of volume patterns to check
    local volume_patterns=(
        "open-ace"
        "${project_name}"
        "postgres"
    )
    
    local all_volumes=""
    for pattern in "${volume_patterns[@]}"; do
        local vols=$(docker volume ls --filter "name=$pattern" --format "{{.Name}}" 2>/dev/null || true)
        if [ -n "$vols" ]; then
            all_volumes="$all_volumes$vols"$'\n'
        fi
    done
    
    # Remove duplicates and empty lines
    all_volumes=$(echo "$all_volumes" | sort -u | grep -v '^$' || true)

    if [ -z "$all_volumes" ]; then
        print_info "没有发现相关数据卷"
        return 0
    fi

    print_info "发现的数据卷:"
    echo "$all_volumes" | while read volume; do
        echo "  - $volume"
    done

    if [ "$PURGE_DATA" = true ]; then
        echo "$all_volumes" | while read volume; do
            if [ -n "$volume" ]; then
                docker volume rm "$volume" 2>/dev/null || true
                print_success "数据卷已删除: $volume"
            fi
        done
    else
        print_info "保留数据卷 (使用 --purge 删除)"
    fi
}

remove_images() {
    print_header "清理镜像"

    # Remove open-ace images
    local images=$(docker images --filter "reference=open-ace*" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)

    if [ -z "$images" ]; then
        print_info "没有发现 Open ACE 镜像"
    else
        print_info "发现的 Open ACE 镜像:"
        echo "$images" | while read image; do
            echo "  - $image"
        done

        # In purge mode, remove without asking
        if [ "$PURGE_DATA" = true ]; then
            echo "$images" | while read image; do
                docker rmi "$image" 2>/dev/null || true
                print_success "镜像已删除: $image"
            done
        elif [ "$NON_INTERACTIVE" = false ]; then
            prompt_yesno "是否删除 Open ACE 镜像?" "y" remove_images_confirm
            if [ "$remove_images_confirm" = "yes" ]; then
                echo "$images" | while read image; do
                    docker rmi "$image" 2>/dev/null || true
                    print_success "镜像已删除: $image"
                done
            else
                print_info "保留 Open ACE 镜像"
            fi
        else
            # Non-interactive mode: remove by default
            echo "$images" | while read image; do
                docker rmi "$image" 2>/dev/null || true
                print_success "镜像已删除: $image"
            done
        fi
    fi

    # Check PostgreSQL image
    if docker image inspect "postgres:15-alpine" &>/dev/null; then
        print_info "发现 PostgreSQL 镜像: postgres:15-alpine"

        if [ "$PURGE_DATA" = true ]; then
            docker rmi "postgres:15-alpine" 2>/dev/null || true
            print_success "PostgreSQL 镜像已删除"
        elif [ "$NON_INTERACTIVE" = false ]; then
            prompt_yesno "是否删除 PostgreSQL 镜像?" "y" remove_pg_confirm
            if [ "$remove_pg_confirm" = "yes" ]; then
                docker rmi "postgres:15-alpine" 2>/dev/null || true
                print_success "PostgreSQL 镜像已删除"
            else
                print_info "保留 PostgreSQL 镜像"
            fi
        else
            # Non-interactive mode: remove by default
            docker rmi "postgres:15-alpine" 2>/dev/null || true
            print_success "PostgreSQL 镜像已删除"
        fi
    fi
}

remove_directories() {
    print_header "删除目录"

    if [ "$PURGE_DATA" = true ]; then
        print_warning "即将删除所有数据: $DEPLOY_DIR"

        if [ "$NON_INTERACTIVE" = false ]; then
            prompt_yesno "确认删除所有数据?" "n" confirm_purge
            if [ "$confirm_purge" != "yes" ]; then
                print_info "取消删除"
                PURGE_DATA=false
            fi
        fi
    fi

    if [ "$PURGE_DATA" = true ]; then
        rm -rf "$DEPLOY_DIR"
        print_success "目录已删除: $DEPLOY_DIR"
    else
        # Keep config, remove only docker-compose.yml and .env
        if [ -f "$DEPLOY_DIR/docker-compose.yml" ]; then
            rm "$DEPLOY_DIR/docker-compose.yml"
            print_success "docker-compose.yml 已删除"
        fi

        if [ -f "$DEPLOY_DIR/.env" ]; then
            rm "$DEPLOY_DIR/.env"
            print_success ".env 已删除"
        fi

        print_info "保留以下目录:"
        echo "  - $DEPLOY_DIR/config"
        print_info "使用 --purge 删除所有数据"
    fi
}

remove_user() {
    if [ "$PURGE_DATA" = false ]; then
        return 0
    fi

    print_header "删除用户"

    # Check if user exists
    if ! id "$RUN_USER" &>/dev/null; then
        print_info "用户 $RUN_USER 不存在"
        return 0
    fi

    # Check if this is a system user created by deploy.sh
    local user_home=$(eval echo ~$RUN_USER 2>/dev/null || echo "")
    if [ "$user_home" != "/home/$RUN_USER" ]; then
        print_info "用户 $RUN_USER 不是由 deploy.sh 创建的，保留"
        return 0
    fi

    # Ask if user wants to remove the user
    if [ "$NON_INTERACTIVE" = false ]; then
        prompt_yesno "是否删除用户 $RUN_USER?" "n" remove_user_confirm
        if [ "$remove_user_confirm" != "yes" ]; then
            print_info "保留用户: $RUN_USER"
            return 0
        fi
    else
        print_info "保留用户: $RUN_USER (非交互模式)"
        return 0
    fi

    # Remove user
    userdel "$RUN_USER" 2>/dev/null || true
    print_success "用户已删除: $RUN_USER"
}

show_summary() {
    print_header "卸载完成"

    echo -e "${GREEN}Open ACE 已成功卸载${NC}"
    echo ""

    if [ "$PURGE_DATA" = true ]; then
        echo "已删除:"
        echo "  ✓ 容器"
        echo "  ✓ 数据卷"
        echo "  ✓ 镜像"
        echo "  ✓ 配置文件"
        echo "  ✓ 数据目录"
        echo "  ✓ 用户 (可选)"
    else
        echo "已删除:"
        echo "  ✓ 容器"
        echo "  ✓ docker-compose.yml"
        echo "  ✓ .env"
        echo "  ✓ Docker 镜像 (可选)"
        echo ""
        echo "已保留:"
        echo "  - $DEPLOY_DIR/config"
        echo "  - PostgreSQL 数据卷"
        echo "  - 用户: $RUN_USER"
        echo ""
        echo "如需完全删除（包括数据卷），请运行:"
        echo "  $0 --purge"
    fi
}

# ============================================================================
# Main
# ============================================================================

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --non-interactive|-n)
            NON_INTERACTIVE=true
            shift
            ;;
        --purge)
            PURGE_DATA=true
            shift
            ;;
        --user|-u)
            RUN_USER="$2"
            DEPLOY_DIR="/home/$RUN_USER/open-ace"
            shift 2
            ;;
        --deploy-dir)
            DEPLOY_DIR="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            ;;
        *)
            print_error "未知选项: $1"
            echo "运行 '$0 --help' 查看帮助"
            exit 1
            ;;
    esac
done

# Main execution
print_header "Open ACE - 卸载程序"

# Check if deployment exists
if ! check_deployment; then
    print_warning "未找到有效的部署"
    prompt_yesno "是否继续检查其他位置?" "n" check_other
    if [ "$check_other" = "yes" ]; then
        prompt_input "运行用户" "$RUN_USER" RUN_USER
        DEPLOY_DIR="/home/$RUN_USER/open-ace"
        check_deployment || exit 1
    else
        exit 0
    fi
fi

# Confirm uninstallation
echo ""
echo -e "${YELLOW}警告: 此操作将停止并移除 Open ACE 服务${NC}"
if [ "$PURGE_DATA" = true ]; then
    echo -e "${RED}警告: --purge 将永久删除所有数据！${NC}"
fi
echo ""

if [ "$NON_INTERACTIVE" = false ]; then
    prompt_yesno "确认卸载?" "n" confirm
    if [ "$confirm" != "yes" ]; then
        echo "卸载已取消"
        exit 0
    fi
fi

# Execute uninstallation
stop_containers
remove_volumes
remove_images
remove_directories
remove_user
show_summary