#!/bin/bash
#
# Open ACE - Docker Image Export Script
#
# This script exports Docker images for offline deployment.
# Includes both application and PostgreSQL images.
#
# Usage:
#   ./export-image.sh                    # Export all images
#   ./export-image.sh --build            # Build and export all images
#   ./export-image.sh --app-only         # Export only open-ace image
#   ./export-image.sh --help             # Show help
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
IMAGE_NAME="${IMAGE_NAME:-open-ace:latest}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:15-alpine}"
APP_PLATFORM="${APP_PLATFORM:-linux/amd64}"  # Default platform for application (most servers are amd64)
POSTGRES_PLATFORM="${POSTGRES_PLATFORM:-linux/amd64}"  # Default platform for PostgreSQL (most servers are amd64)
OUTPUT_DIR="${OUTPUT_DIR:-.}"
OUTPUT_NAME=""
APP_ONLY=false
BUILD_IMAGE=false
NO_CACHE=false

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

show_help() {
    echo "Open ACE - Docker Image Export Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --image, -i IMAGE       Application image name (default: open-ace:latest)"
    echo "  --postgres, -p IMAGE    PostgreSQL image name (default: postgres:15-alpine)"
    echo "  --app-platform PLATFORM Application image platform (default: linux/amd64)"
    echo "  --platform PLATFORM     PostgreSQL image platform (default: linux/amd64)"
    echo "  --output, -o DIR        Output directory (default: current directory)"
    echo "  --name, -n NAME         Output filename prefix (default: open-ace-images)"
    echo "  --app-only              Export only application image (skip PostgreSQL)"
    echo "  --build, -b             Build image before exporting"
    echo "  --no-cache              Build without cache (use with --build)"
    echo "  --compress, -c          Compress with gzip (.tar.gz)"
    echo "  --help, -h              Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  IMAGE_NAME              Application image name"
    echo "  POSTGRES_IMAGE          PostgreSQL image name"
    echo "  APP_PLATFORM            Application image platform (default: linux/amd64)"
    echo "  POSTGRES_PLATFORM       PostgreSQL image platform (default: linux/amd64)"
    echo "  OUTPUT_DIR              Output directory"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Export existing images to ./open-ace-images.tar"
    echo "  $0 --build --compress                 # Build and export to ./open-ace-images.tar.gz"
    echo "  $0 --build --no-cache                 # Build without cache and export"
    echo "  $0 --app-only                         # Export only open-ace image"
    echo "  $0 --output /tmp --compress           # Export to /tmp/open-ace-images.tar.gz"
    echo "  $0 --app-platform linux/arm64         # Build/export ARM64 application image"
    echo ""
    echo "Output:"
    echo "  Creates tar file(s) that can be loaded with:"
    echo "    docker load -i open-ace-images.tar"
    echo "    # or for compressed:"
    echo "    gunzip -c open-ace-images.tar.gz | docker load"
    echo ""
    exit 0
}

# ============================================================================
# Main Script
# ============================================================================

COMPRESS=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --image|-i)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --postgres|-p)
            POSTGRES_IMAGE="$2"
            shift 2
            ;;
        --app-platform)
            APP_PLATFORM="$2"
            shift 2
            ;;
        --platform)
            POSTGRES_PLATFORM="$2"
            shift 2
            ;;
        --output|-o)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --name|-n)
            OUTPUT_NAME="$2"
            shift 2
            ;;
        --compress|-c)
            COMPRESS=true
            shift
            ;;
        --app-only)
            APP_ONLY=true
            shift
            ;;
        --build|-b)
            BUILD_IMAGE=true
            shift
            ;;
        --no-cache)
            NO_CACHE=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Run '$0 --help' for usage information."
            exit 1
            ;;
    esac
done

# Remove trailing slash from OUTPUT_DIR to avoid double slashes
OUTPUT_DIR="${OUTPUT_DIR%/}"

print_header "Open ACE - 镜像导出工具"

# Interactive prompt for export mode (skip if --app-only is specified)
if [ "$APP_ONLY" = false ] && [ -t 0 ]; then
    echo -e "${BLUE}是否包含 PostgreSQL 镜像? [y/N]: ${NC}"
    read -r choice

    case "$choice" in
        y|Y|yes|YES)
            print_info "将导出: open-ace + PostgreSQL 镜像"
            ;;
        *)
            APP_ONLY=true
            print_info "将导出: 仅 open-ace 镜像"
            ;;
    esac
    echo ""
fi

# Check if Docker is available
if ! command -v docker &>/dev/null; then
    print_error "Docker 未安装"
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &>/dev/null; then
    print_error "Docker daemon 未运行"
    exit 1
fi

# Build image if requested
if [ "$BUILD_IMAGE" = true ]; then
    print_header "构建 Docker 镜像"

    # Find project root (where Dockerfile is)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

    if [ ! -f "$PROJECT_ROOT/Dockerfile" ]; then
        print_error "找不到 Dockerfile: $PROJECT_ROOT/Dockerfile"
        exit 1
    fi

    print_info "项目目录: $PROJECT_ROOT"
    print_info "镜像名称: $IMAGE_NAME"
    print_info "目标平台: $APP_PLATFORM"

    if [ "$NO_CACHE" = true ]; then
        print_info "构建模式: 无缓存"
        BUILD_ARGS="--no-cache"
    else
        print_info "构建模式: 使用缓存"
        BUILD_ARGS=""
    fi

    echo ""
    print_info "开始构建..."

    cd "$PROJECT_ROOT"
    # Use buildx for cross-platform builds
    if docker buildx build $BUILD_ARGS --platform "$APP_PLATFORM" --target production -t "$IMAGE_NAME" --load .; then
        print_success "镜像构建成功: $IMAGE_NAME (平台: $APP_PLATFORM)"
    else
        print_error "镜像构建失败"
        exit 1
    fi
    echo ""
fi

# Create output directory if needed
if [ ! -d "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR"
    print_info "创建输出目录: $OUTPUT_DIR"
fi

# Generate output filename
if [ -z "$OUTPUT_NAME" ]; then
    OUTPUT_NAME="open-ace-images"
fi

# List of images to export
IMAGES=()
IMAGE_SIZES=()

# Check application image
print_info "检查镜像: $IMAGE_NAME"
if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
    print_error "镜像不存在: $IMAGE_NAME"
    echo ""
    echo "可用镜像:"
    docker images --format "  {{.Repository}}:{{.Tag}}\t{{.Size}}" | grep -E "^open-ace|^REPOSITORY" || echo "  (无 open-ace 镜像)"
    echo ""
    echo "提示: 使用 --build 参数构建镜像"
    echo "  $0 --build --compress"
    exit 1
fi
IMAGES+=("$IMAGE_NAME")
IMAGE_ID=$(docker image inspect "$IMAGE_NAME" --format '{{.Id}}' | cut -c8-19)
IMAGE_SIZE=$(docker image inspect "$IMAGE_NAME" --format '{{.Size}}')
IMAGE_ARCH=$(docker image inspect "$IMAGE_NAME" --format '{{.Architecture}}')
print_success "应用镜像: $IMAGE_NAME (ID: $IMAGE_ID, 平台: $IMAGE_ARCH, 大小: $((IMAGE_SIZE/1024/1024)) MB)"

# Check PostgreSQL image
if [ "$APP_ONLY" = false ]; then
    print_info "检查镜像: $POSTGRES_IMAGE (平台: $POSTGRES_PLATFORM)"
    if ! docker image inspect "$POSTGRES_IMAGE" &>/dev/null; then
        print_warning "PostgreSQL 镜像不存在: $POSTGRES_IMAGE"
        print_info "正在拉取 PostgreSQL 镜像 (平台: $POSTGRES_PLATFORM)..."
        if docker pull --platform "$POSTGRES_PLATFORM" "$POSTGRES_IMAGE"; then
            print_success "PostgreSQL 镜像拉取成功"
        else
            print_error "PostgreSQL 镜像拉取失败"
            print_info "将跳过 PostgreSQL 镜像导出"
        fi
    fi

    if docker image inspect "$POSTGRES_IMAGE" &>/dev/null; then
        IMAGES+=("$POSTGRES_IMAGE")
        IMAGE_ID=$(docker image inspect "$POSTGRES_IMAGE" --format '{{.Id}}' | cut -c8-19)
        IMAGE_SIZE=$(docker image inspect "$POSTGRES_IMAGE" --format '{{.Size}}')
        print_success "PostgreSQL 镜像: $POSTGRES_IMAGE (ID: $IMAGE_ID, 平台: $POSTGRES_PLATFORM, 大小: $((IMAGE_SIZE/1024/1024)) MB)"
    fi
fi

echo ""
print_info "准备导出 ${#IMAGES[@]} 个镜像"

# Export images
if [ "$COMPRESS" = true ]; then
    OUTPUT_FILE="${OUTPUT_DIR}/${OUTPUT_NAME}.tar.gz"
else
    OUTPUT_FILE="${OUTPUT_DIR}/${OUTPUT_NAME}.tar"
fi

print_info "导出到: $OUTPUT_FILE"
echo ""

# Export all images
if [ "$COMPRESS" = true ]; then
    docker save "${IMAGES[@]}" | gzip > "$OUTPUT_FILE"
else
    docker save "${IMAGES[@]}" -o "$OUTPUT_FILE"
fi

# Check if export was successful
if [ -f "$OUTPUT_FILE" ]; then
    OUTPUT_SIZE=$(ls -lh "$OUTPUT_FILE" | awk '{print $5}')
    
    print_success "镜像导出成功"
    echo ""
    echo "导出信息:"
    echo "  文件: $OUTPUT_FILE"
    echo "  大小: $OUTPUT_SIZE"
    echo "  镜像:"
    for img in "${IMAGES[@]}"; do
        echo "    - $img"
    done
    echo ""
    echo "部署方法:"
    echo "  1. 拷贝文件到目标服务器:"
    echo "     scp $OUTPUT_FILE user@server:~"
    echo ""
    echo "  2. 在目标服务器上加载镜像:"
    if [ "$COMPRESS" = true ]; then
        echo "     gunzip -c $OUTPUT_NAME.tar.gz | docker load"
    else
        echo "     docker load -i $OUTPUT_NAME.tar"
    fi
    echo ""
    echo "  3. 运行部署脚本:"
    echo "     ./deploy.sh"
    echo ""
else
    print_error "镜像导出失败"
    exit 1
fi