#!/bin/bash
#
# Open ACE - AI Computing Explorer - Installation Script
#
# This script installs or upgrades Open ACE.
# Supports both local installation and remote deployment via SSH.
#
# Usage:
#   ./install.sh                      # Interactive mode
#   ./install.sh --config install.conf  # Use config file
#   ./install.sh --help               # Show help
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# SOURCE_DIR should be the package root (where cli.py, server.py, etc. are located)
# Package structure: package_root/scripts/install-central/package-method/install.sh
# So we need to go up 3 levels from SCRIPT_DIR to reach package_root
# SCRIPT_DIR = scripts/install-central/package-method
# LEVEL 1 up = scripts/install-central
# LEVEL 2 up = scripts
# LEVEL 3 up = package_root (where server.py, cli.py are)
SOURCE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Validate SOURCE_DIR - must contain server.py or cli.py
validate_source_dir() {
    if [ ! -f "$SOURCE_DIR/server.py" ] && [ ! -f "$SOURCE_DIR/cli.py" ]; then
        print_error "SOURCE_DIR is invalid: $SOURCE_DIR"
        print_error "Expected to find server.py or cli.py in package root"
        print_info "Please ensure you're running install.sh from a valid Open ACE package"
        print_info "Current SCRIPT_DIR: $SCRIPT_DIR"
        exit 1
    fi
    print_info "Source directory validated: $SOURCE_DIR"
}

# Default values
CONFIG_FILE=""
INSTALL_MODE=""  # "local" or "deploy"
DO_UPGRADE="no"  # Set to "yes" when upgrade is detected and confirmed in interactive_config
EXISTING_CONFIG_PATH=""  # Path to existing installation's config.json (for database config reuse)

# Deployment settings (for both local and deploy modes)
DEPLOY_HOST=""        # Empty for local mode, required for deploy mode
DEPLOY_USER=""        # Will be set after checking for openace user
DEPLOY_PATH=""        # Will be set based on DEPLOY_USER

# ============================================================================
# Python Version Check
# ============================================================================

# Check Python version (Open ACE requires Python >= 3.10)
check_python_version() {
    if ! command -v python3 &>/dev/null; then
        print_error "Python 3 is not installed"
        print_info "Open ACE requires Python 3.10 or later"
        print_info ""
        print_info "On CentOS/RHEL 7, install Python 3.10 manually or use a newer OS release"
        print_info ""
        print_info "On Ubuntu/Debian:"
        print_info "  apt install python3.10 python3.10-venv python3.10-dev"
        exit 1
    fi

    local python_version=$(python3 -c "import sys; print(sys.version_info.major * 100 + sys.version_info.minor)")

    if [ "$python_version" -lt 310 ]; then
        local actual_version=$(python3 --version 2>&1 | head -1)
        print_error "Python version too old: $actual_version"
        print_error "Open ACE requires Python 3.10 or later"
        print_info ""
        print_info "On CentOS/RHEL 7, install Python 3.10 manually or use a newer OS release"
        print_info ""
        print_info "On Rocky Linux 8/9 or RHEL 8/9:"
        print_info "  dnf install python3.10 python3.10-pip"
        print_info "  alternatives --set python3 /usr/bin/python3.10"
        print_info ""
        print_info "On Ubuntu/Debian:"
        print_info "  apt install python3.10 python3.10-venv python3.10-dev"
        print_info "  update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1"
        exit 1
    fi

    local actual_version=$(python3 --version 2>&1 | head -1)
    print_success "Python version: $actual_version (OK)"
}

# Check Python version on remote system
check_python_version_remote() {
    local remote="$1"

    if ! ssh "$remote" "command -v python3 &>/dev/null"; then
        print_error "Python 3 is not installed on $remote"
        print_info "Open ACE requires Python 3.10 or later"
        return 1
    fi

    local python_version=$(ssh "$remote" "python3 -c \"import sys; print(sys.version_info.major * 100 + sys.version_info.minor)\"")

    if [ "$python_version" -lt 310 ]; then
        local actual_version=$(ssh "$remote" "python3 --version 2>&1 | head -1")
        print_error "Python version too old on $remote: $actual_version"
        print_error "Open ACE requires Python 3.10 or later"
        return 1
    fi

    local actual_version=$(ssh "$remote" "python3 --version 2>&1 | head -1")
    print_success "Python version on $remote: $actual_version (OK)"
    return 0
}


# ============================================================================
# Build Dependencies Check
# ============================================================================

# Check and install build dependencies (gcc, python-dev) for packages like gevent, bcrypt
# These packages require C compilation and will fail without proper build tools
check_build_dependencies() {
    print_info "Checking build dependencies..."
    local missing_deps=()
    local python_dev_pkg=""
    local procps_pkg="procps"
    if command -v dnf &>/dev/null || command -v yum &>/dev/null; then
        procps_pkg="procps-ng"
    fi

    # Check gcc
    if ! command -v gcc &>/dev/null; then
        missing_deps+=("gcc")
    fi

    # Check make (required by gevent's libev configure)
    if ! command -v make &>/dev/null; then
        missing_deps+=("make")
    fi
    if ! command -v setfacl &>/dev/null; then
        missing_deps+=("acl")
    fi
    if ! command -v flock &>/dev/null; then
        missing_deps+=("util-linux")
    fi
    if ! command -v pkill &>/dev/null; then
        missing_deps+=("$procps_pkg")
    fi

    # Determine python-dev package name based on OS
    if command -v apt-get &>/dev/null; then
        python_dev_pkg="python3-dev"
    elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
        python_dev_pkg="python3-devel"
    fi

    # Check Python development headers
    local python_include=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('INCLUDEPY'))" 2>/dev/null)
    if [ -n "$python_include" ] && [ ! -f "$python_include/Python.h" ]; then
        if [ -n "$python_dev_pkg" ]; then
            missing_deps+=("$python_dev_pkg")
        fi
    fi

    # All dependencies satisfied
    if [ ${#missing_deps[@]} -eq 0 ]; then
        print_success "Build dependencies already installed"
        return 0
    fi

    print_info "Missing build dependencies: ${missing_deps[*]}"

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        print_error "Root privileges required to install: ${missing_deps[*]}"
        print_info "Please run as root, or install manually"
        return 1
    fi

    # Install missing dependencies
    print_info "Installing build dependencies..."
    if command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y ${missing_deps[*]}
    elif command -v dnf &>/dev/null; then
        dnf install -y ${missing_deps[*]}
    elif command -v yum &>/dev/null; then
        yum install -y ${missing_deps[*]}
    else
        print_error "Unsupported package manager"
        return 1
    fi
    print_success "Build dependencies installed"
}


# Check and install build dependencies on remote system (for deploy mode)
check_build_dependencies_remote() {
    local remote="$1"
    print_info "Checking build dependencies on $remote..."

    local missing_deps=""
    local has_gcc=$(ssh "$remote" "command -v gcc >/dev/null 2>&1 && echo yes || echo no")
    local has_make=$(ssh "$remote" "command -v make >/dev/null 2>&1 && echo yes || echo no")
    local has_setfacl=$(ssh "$remote" "command -v setfacl >/dev/null 2>&1 && echo yes || echo no")
    local has_flock=$(ssh "$remote" "command -v flock >/dev/null 2>&1 && echo yes || echo no")
    local has_pkill=$(ssh "$remote" "command -v pkill >/dev/null 2>&1 && echo yes || echo no")

    # Determine remote package manager and python-dev package name
    local remote_pkg=$(ssh "$remote" "command -v apt-get >/dev/null 2>&1 && echo apt || (command -v dnf >/dev/null 2>&1 && echo dnf) || (command -v yum >/dev/null 2>&1 && echo yum) || echo unknown")
    local python_dev_pkg="python3-devel"
    local procps_pkg="procps-ng"
    [ "$remote_pkg" = "apt" ] && python_dev_pkg="python3-dev"
    [ "$remote_pkg" = "apt" ] && procps_pkg="procps"

    # Check Python.h existence on remote
    local has_python_h=$(ssh "$remote" 'python3 -c "import sysconfig,os; print(\"yes\" if os.path.exists(sysconfig.get_config_var(\"INCLUDEPY\")+\"/Python.h\") else \"no\")" 2>/dev/null || echo no')

    # Build missing deps list
    [ "$has_gcc" = "no" ] && missing_deps="$missing_deps gcc"
    [ "$has_make" = "no" ] && missing_deps="$missing_deps make"
    [ "$has_setfacl" = "no" ] && missing_deps="$missing_deps acl"
    [ "$has_flock" = "no" ] && missing_deps="$missing_deps util-linux"
    [ "$has_pkill" = "no" ] && missing_deps="$missing_deps $procps_pkg"
    [ "$has_python_h" = "no" ] && missing_deps="$missing_deps $python_dev_pkg"
    missing_deps=$(echo "$missing_deps" | sed 's/^ *//')

    if [ -z "$missing_deps" ]; then
        print_success "Build dependencies already installed on $remote"
        return 0
    fi

    print_info "Missing build dependencies on $remote: $missing_deps"
    print_info "Installing build dependencies on $remote..."

    ssh "$remote" "
        if [ '$remote_pkg' = 'apt' ]; then
            sudo apt-get update -qq && sudo apt-get install -y $missing_deps
        elif [ '$remote_pkg' = 'dnf' ]; then
            sudo dnf install -y $missing_deps
        elif [ '$remote_pkg' = 'yum' ]; then
            sudo yum install -y $missing_deps
        else
            echo 'ERROR: Unsupported package manager on remote'
            exit 1
        fi
    " || {
        print_error "Failed to install build dependencies on $remote"
        print_info "Please ensure sudo is available on remote, or install manually: $missing_deps"
        return 1
    }
    print_success "Build dependencies installed on $remote"
}
# ============================================================================

# Check and install PostgreSQL client (psql) for schema execution
check_psql_client() {
    print_info "Checking PostgreSQL client (psql)..."

    # Check if psql is available
    if command -v psql >/dev/null 2>&1; then
        print_success "psql client already installed"
        return 0
    fi

    print_info "psql client not found, installing..."

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        print_error "Root privileges required to install PostgreSQL client"
        print_info "Please install manually:"
        print_info "  CentOS/RHEL/Rocky: yum install postgresql"
        print_info "  Ubuntu/Debian: apt install postgresql-client"
        return 1
    fi

    # Install PostgreSQL client based on OS
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y postgresql-client
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y postgresql
    elif command -v yum >/dev/null 2>&1; then
        yum install -y postgresql
    else
        print_error "Unsupported package manager"
        return 1
    fi

    print_success "PostgreSQL client installed"
}

# Check and install PostgreSQL client on remote system (for deploy mode)
check_psql_client_remote() {
    local remote="$1"
    print_info "Checking PostgreSQL client on $remote..."

    # Check if psql is available on remote
    local has_psql=$(ssh "$remote" "command -v psql >/dev/null 2>&1 && echo yes || echo no")

    if [ "$has_psql" = "yes" ]; then
        print_success "psql client already installed on $remote"
        return 0
    fi

    print_info "psql client not found on $remote, installing..."

    # Determine remote package manager
    local remote_pkg=$(ssh "$remote" "command -v apt-get >/dev/null 2>&1 && echo apt || (command -v dnf >/dev/null 2>&1 && echo dnf) || (command -v yum >/dev/null 2>&1 && echo yum) || echo unknown")

    ssh "$remote" "
        if [ '$remote_pkg' = 'apt' ]; then
            sudo apt-get update -qq && sudo apt-get install -y postgresql-client
        elif [ '$remote_pkg' = 'dnf' ]; then
            sudo dnf install -y postgresql
        elif [ '$remote_pkg' = 'yum' ]; then
            sudo yum install -y postgresql
        else
            echo 'ERROR: Unsupported package manager on remote'
            exit 1
        fi
    " || {
        print_error "Failed to install PostgreSQL client on $remote"
        print_info "Please ensure sudo is available on remote, or install manually"
        return 1
    }

    print_success "PostgreSQL client installed on $remote"
}

# ============================================================================
# Schema Detection (Issue #1095)
# ============================================================================

# Check if Open ACE application schema already exists in database
# Detects 3 sentinel tables: users, agent_sessions, session_messages
# Returns 0 if schema exists, 1 if not
check_app_schema_exists() {
    local db_url="$1"
    local db_host db_port db_name db_user db_pass

    # Parse database URL using Python (more robust than sed)
    db_host=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.hostname or 'localhost')" 2>/dev/null)
    db_port=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.port or 5432)" 2>/dev/null)
    db_name=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print((u.path or '/').lstrip('/'))" 2>/dev/null)
    db_user=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.username or '')" 2>/dev/null)
    db_pass=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.password or '')" 2>/dev/null)

    if [ -z "$db_name" ]; then
        print_warning "Could not parse database name from URL"
        return 1
    fi

    # Check if all 3 sentinel tables exist
    local count=$(PGPASSWORD="$db_pass" psql -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" -tAc "
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema='public'
        AND table_name IN ('users', 'agent_sessions', 'session_messages')
    " 2>/dev/null || echo "0")

    if [ "$count" = "3" ]; then
        print_info "Existing Open ACE schema detected (3 sentinel tables found)"
        return 0
    fi

    return 1
}

# ============================================================================
# User Detection and Creation
# ============================================================================

# Check if openace user exists on the system
check_openace_user_exists() {
    if id "openace" &>/dev/null; then
        return 0  # User exists
    else
        return 1  # User does not exist
    fi
}

# Check if a user exists on the system (validates user name)
user_exists() {
    local user="$1"
    if [ -z "$user" ]; then
        return 1
    fi
    id "$user" &>/dev/null
}

# Check if openace user exists on remote system
check_openace_user_exists_remote() {
    local remote="$1"
    if ssh "$remote" "id openace &>/dev/null"; then
        return 0  # User exists
    else
        return 1  # User does not exist
    fi
}

# Create openace user on local system
create_openace_user() {
    print_info "Creating openace system user..."

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        print_warning "Root privileges required to create openace user"
        print_info "Please run: sudo $0"
        return 1
    fi

    # Detect OS and create user accordingly
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        # Check if the user already exists in local directory
        if dscl . list /Users | grep -q "^openace"; then
            print_success "openace user already exists"
            return 0
        fi

        # Create user on macOS
        local last_uid=$(dscl . list /Users UniqueID | sort -nr | head -1 | awk '{print $2}')
        local new_uid=$((last_uid + 1))

        # Create the user
        dscl . -create /Users/openace
        dscl . -create /Users/openace UserShell /bin/bash
        dscl . -create /Users/openace RealName "Open ACE Service"
        dscl . -create /Users/openace UniqueID "$new_uid"
        dscl . -create /Users/openace PrimaryGroupID 20  # Staff group

        # Create home directory
        createhomedir -c -u openace > /dev/null 2>&1 || true

        print_success "Created openace user (UID: $new_uid)"
        print_info "Home directory: /Users/openace"

    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        if command -v useradd &>/dev/null; then
            # Create system user with home directory
            useradd -r -m -s /bin/bash openace || {
                # If -r fails (some systems don't support it), try without
                useradd -m -s /bin/bash openace
            }
            print_success "Created openace system user"
            print_info "Home directory: /home/openace"
        elif command -v adduser &>/dev/null; then
            # Debian-style adduser
            adduser --system --home /home/openace --shell /bin/bash openace || {
                # If --system fails, try regular adduser
                adduser --disabled-password --gecos "Open ACE Service" openace
            }
            print_success "Created openace system user"
        else
            print_error "Cannot create user: useradd/adduser not found"
            return 1
        fi
    else
        print_error "Unsupported OS: $OSTYPE"
        print_info "Please create the openace user manually"
        return 1
    fi

    return 0
}

# Create openace user on remote system
create_openace_user_remote() {
    local remote="$1"

    print_info "Creating openace system user on remote..."

    # Detect remote OS and create user
    local os_type=$(ssh "$remote" "echo \$OSTYPE")

    ssh "$remote" "
        # Check if already exists
        if id openace &>/dev/null; then
            echo 'User openace already exists'
            exit 0
        fi

        # Check for sudo
        if ! sudo -n true 2>/dev/null; then
            echo 'SUDO_REQUIRED'
            exit 1
        fi

        # Create user based on OS
        if command -v useradd &>/dev/null; then
            sudo useradd -r -m -s /bin/bash openace 2>/dev/null || \
            sudo useradd -m -s /bin/bash openace
            echo 'USER_CREATED'
        elif command -v adduser &>/dev/null; then
            sudo adduser --system --home /home/openace --shell /bin/bash openace 2>/dev/null || \
            sudo adduser --disabled-password --gecos 'Open ACE Service' openace
            echo 'USER_CREATED'
        else
            echo 'USERADD_NOT_FOUND'
            exit 1
        fi
    "

    local result=$(ssh "$remote" "
        if id openace &>/dev/null; then
            echo 'USER_EXISTS'
        elif sudo -n true 2>/dev/null; then
            echo 'SUDO_OK'
        else
            echo 'SUDO_REQUIRED'
        fi
    ")

    case "$result" in
        USER_EXISTS)
            print_success "openace user exists on remote"
            return 0
            ;;
        SUDO_OK)
            # Try to create again
            ssh "$remote" "
                if ! id openace &>/dev/null && sudo -n true; then
                    if command -v useradd &>/dev/null; then
                        sudo useradd -r -m -s /bin/bash openace 2>/dev/null || sudo useradd -m -s /bin/bash openace
                    elif command -v adduser &>/dev/null; then
                        sudo adduser --system --home /home/openace --shell /bin/bash openace 2>/dev/null || sudo adduser --disabled-password --gecos 'Open ACE Service' openace
                    fi
                fi
            "
            if ssh "$remote" "id openace &>/dev/null"; then
                print_success "Created openace user on remote"
                return 0
            else
                print_error "Failed to create openace user on remote"
                return 1
            fi
            ;;
        SUDO_REQUIRED)
            print_warning "Sudo required on remote to create openace user"
            print_info "Please run on remote: sudo useradd -r -m -s /bin/bash openace"
            return 1
            ;;
    esac
}

# Initialize deployment user based on openace user availability
init_deploy_user() {
    local is_local="$1"  # "true" for local, "false" for remote

    # Determine default home directory based on OS
    local openace_home
    if [[ "$OSTYPE" == "darwin"* ]]; then
        openace_home="/Users/openace"
    else
        openace_home="/home/openace"
    fi

    if [ "$is_local" = "true" ]; then
        # Local installation
        if check_openace_user_exists; then
            DEPLOY_USER="openace"
            DEPLOY_PATH="$openace_home"
            print_info "Found openace user, using as deployment user"
        else
            # Check if running as root - can create user
            if [ "$EUID" -eq 0 ]; then
                print_info "Running as root, will create openace user"
                if create_openace_user; then
                    DEPLOY_USER="openace"
                    DEPLOY_PATH="$openace_home"
                else
                    print_warning "Failed to create openace user, using current user"
                    DEPLOY_USER="${USER}"
                    DEPLOY_PATH="$HOME/open-ace"
                fi
            else
                # Not root, use current user as default
                DEPLOY_USER="${USER}"
                DEPLOY_PATH="$HOME/open-ace"
            fi
        fi
    else
        # Remote deployment - will check later during SSH connection
        DEPLOY_USER="openace"
        DEPLOY_PATH="/home/openace"  # Most servers run Linux
    fi
}

# Check if DEPLOY_PATH is dangerously set to user's home directory.
# Returns 0 if safe (or user confirmed), 1 if cancelled.
check_deploy_path_safety() {
    # Validate that DEPLOY_PATH is a non-empty absolute path
    if [ -z "$DEPLOY_PATH" ]; then
        print_error "Deployment path cannot be empty"
        return 1
    fi

    if [[ "$DEPLOY_PATH" != /* ]]; then
        print_error "Deployment path must be an absolute path (starting with /): $DEPLOY_PATH"
        print_info "Example: /home/$DEPLOY_USER/open-ace"
        return 1
    fi

    local user_home
    user_home=$(eval echo "~$DEPLOY_USER")

    # Normalize both paths (remove trailing slash)
    local norm_deploy="${DEPLOY_PATH%/}"
    local norm_home="${user_home%/}"

    if [ "$norm_deploy" != "$norm_home" ]; then
        # Not the home directory — safe
        return 0
    fi

    echo ""
    echo -e "${RED}WARNING: Deployment path is set to the user's home directory: $DEPLOY_PATH${NC}"
    echo -e "${RED}During upgrades, ALL subdirectories (except logs, data, .open-ace) will be deleted.${NC}"
    echo -e "${RED}This may cause loss of user projects and files!${NC}"
    echo ""

    # List subdirectories in the home directory
    echo -e "${YELLOW}Current subdirectories in $DEPLOY_PATH:${NC}"
    local subdir_count=0
    for d in "$DEPLOY_PATH"/*/; do
        if [ -d "$d" ]; then
            local dir_name=$(basename "$d")
            echo "  - $dir_name/"
            subdir_count=$((subdir_count + 1))
        fi
    done
    if [ "$subdir_count" -eq 0 ]; then
        echo "  (empty)"
    fi
    echo ""

    echo -e "${YELLOW}It is recommended to use a subdirectory like: $user_home/open-ace${NC}"
    local auto_fix_path="$user_home/open-ace"
    prompt_yesno "Use recommended path: $auto_fix_path?" "y" use_safe_path
    if [ "$use_safe_path" = "yes" ]; then
        DEPLOY_PATH="$auto_fix_path"
        print_success "Deployment path set to: $DEPLOY_PATH"
        return 0
    fi

    prompt_yesno "Continue with home directory anyway? (NOT recommended)" "n" force_continue
    if [ "$force_continue" = "yes" ]; then
        print_warning "Proceeding with home directory. Use with caution!"
        return 0
    fi

    return 1
}

# ============================================================================
# PostgreSQL Detection and Installation
# ============================================================================

# Database settings
DB_TYPE="postgresql"
DB_HOST="localhost"
DB_PORT="5432"
DB_NAME="openace"
DB_USER="openace"
DB_PASSWORD=""
DB_INSTALL_METHOD=""  # "binary", "docker", or "existing"

# Check if PostgreSQL is running as systemd service
check_postgresql_systemd() {
    local service_names=("postgresql" "postgres" "postgresql@14-main" "postgresql@15-main" "postgresql@16-main")

    for service in "${service_names[@]}"; do
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            print_success "Found PostgreSQL systemd service: $service"
            return 0
        fi
    done
    return 1
}

# Check if PostgreSQL process is running (without systemd)
check_postgresql_process() {
    if pgrep -x "postgres" >/dev/null 2>&1; then
        # Check if the process is actually listening on a port
        if ss -tlnp 2>/dev/null | grep -q postgres; then
            print_success "Found PostgreSQL process running and listening"
            return 0
        else
            # Process exists but not listening - could be Docker container process
            print_warning "Found PostgreSQL process but NOT listening on any port"
            print_info "This may be a Docker container process, not host PostgreSQL"
            return 1
        fi
    fi
    return 1
}

# Check if PostgreSQL is running in Docker container with port mapped to host
check_postgresql_docker() {
    if ! command -v docker &>/dev/null; then
        return 1
    fi

    # Check for running PostgreSQL containers with port mapped to host
    # Format: 0.0.0.0:5432->5432/tcp or similar
    local containers=$(docker ps --filter "ancestor=postgres" --filter "ancestor=postgresql" --format "{{.Names}} {{.Ports}}" 2>/dev/null)
    if [ -n "$containers" ]; then
        # Check if port is mapped to host (contains 0.0.0.0: or [::]:)
        local port_mapped=$(echo "$containers" | grep -E '0\.0\.0\.0:[0-9]+->5432|:::[0-9]+->5432|\[::\]:[0-9]+->5432')
        if [ -n "$port_mapped" ]; then
            local container_name=$(echo "$containers" | head -1 | awk '{print $1}')
            print_success "Found PostgreSQL Docker container with port mapped: $container_name"
            return 0
        else
            # Container exists but no port mapping
            local container_name=$(echo "$containers" | head -1 | awk '{print $1}')
            print_warning "Found PostgreSQL Docker container but port NOT mapped to host: $container_name"
            print_info "Container has internal port only, cannot connect from host"
            return 1
        fi
    fi

    # Also check for containers with postgres in name
    containers=$(docker ps --filter "name=postgres" --format "{{.Names}} {{.Ports}}" 2>/dev/null)
    if [ -n "$containers" ]; then
        local port_mapped=$(echo "$containers" | grep -E '0\.0\.0\.0:[0-9]+->5432|:::[0-9]+->5432|\[::\]:[0-9]+->5432')
        if [ -n "$port_mapped" ]; then
            local container_name=$(echo "$containers" | head -1 | awk '{print $1}')
            print_success "Found PostgreSQL Docker container with port mapped: $container_name"
            return 0
        else
            local container_name=$(echo "$containers" | head -1 | awk '{print $1}')
            print_warning "Found PostgreSQL Docker container but port NOT mapped to host: $container_name"
            print_info "Container has internal port only, cannot connect from host"
            return 1
        fi
    fi

    return 1
}

# Check if Docker is available and running
check_docker_available() {
    if ! command -v docker &>/dev/null; then
        return 1
    fi

    # Check if Docker daemon is running
    if docker info >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

# Check if PostgreSQL is installed (binary) but not running
check_postgresql_installed() {
    # Check for postgres command
    if command -v postgres &>/dev/null; then
        return 0
    fi

    # Check common installation paths
    local paths=(
        "/usr/bin/postgres"
        "/usr/lib/postgresql/*/bin/postgres"
        "/usr/pgsql-*/bin/postgres"
    )

    for path_pattern in "${paths[@]}"; do
        if ls $path_pattern >/dev/null 2>&1; then
            return 0
        fi
    done

    return 1
}

# Get PostgreSQL port from running instance
get_postgresql_port() {
    # Try to get port from postgres process
    local port=$(ss -tlnp | grep postgres | grep -oP ':\K\d+' | head -1)
    if [ -n "$port" ]; then
        echo "$port"
        return
    fi

    # Try Docker
    if command -v docker &>/dev/null; then
        port=$(docker ps --filter "ancestor=postgres" --format "{{.Ports}}" 2>/dev/null | grep -oP '0\.0\.0\.0:\K\d+' | head -1)
        if [ -n "$port" ]; then
            echo "$port"
            return
        fi
    fi

    # Default port
    echo "5432"
}

# Find existing installation's config.json dynamically
# Scans all user home directories and root's config
# Returns: config.json path if found, empty string otherwise
find_existing_config_file() {
    local config_file=""

    # Linux: scan /home/* for .open-ace/config.json
    for user_home in /home/*; do
        if [ -d "$user_home/.open-ace" ] && [ -f "$user_home/.open-ace/config.json" ]; then
            config_file="$user_home/.open-ace/config.json"
            break
        fi
    done

    # macOS: scan /Users/* for .open-ace/config.json
    if [[ "$OSTYPE" == "darwin"* ]]; then
        for user_home in /Users/*; do
            if [ -d "$user_home/.open-ace" ] && [ -f "$user_home/.open-ace/config.json" ]; then
                config_file="$user_home/.open-ace/config.json"
                break
            fi
        done
    fi

    # Check root's config
    if [ -z "$config_file" ] && [ -f "/root/.open-ace/config.json" ]; then
        config_file="/root/.open-ace/config.json"
    fi

    echo "$config_file"
}

# Install PostgreSQL as binary service
# Configure pg_hba.conf for password authentication
# Changes peer/ident authentication to md5 for local connections
configure_pg_hba_conf() {
    print_info "Configuring PostgreSQL authentication..."

    # Find pg_hba.conf location
    local pg_hba_conf=""

    # Common locations for pg_hba.conf
    local possible_locations=(
        "/var/lib/pgsql/data/pg_hba.conf"       # RHEL/CentOS/Fedora default
        "/var/lib/postgresql/data/pg_hba.conf"  # Debian/Ubuntu default
        "/etc/postgresql/*/main/pg_hba.conf"    # Debian/Ubuntu alternative
        "/usr/local/var/lib/postgresql/pg_hba.conf"  # macOS Homebrew
    )

    for loc in "${possible_locations[@]}"; do
        # Handle wildcard paths
        if [[ "$loc" == *"*"* ]]; then
            # Find the first matching file
            pg_hba_conf=$(ls $loc 2>/dev/null | head -1)
        else
            if [ -f "$loc" ]; then
                pg_hba_conf="$loc"
            fi
        fi
        if [ -n "$pg_hba_conf" ] && [ -f "$pg_hba_conf" ]; then
            break
        fi
    done

    # Try to get location from PostgreSQL itself
    if [ -z "$pg_hba_conf" ]; then
        pg_hba_conf=$(su - postgres -c "psql -t -P format=unaligned -c 'SHOW hba_file'" 2>/dev/null)
    fi

    if [ -z "$pg_hba_conf" ] || [ ! -f "$pg_hba_conf" ]; then
        print_warning "Could not find pg_hba.conf, skipping authentication configuration"
        print_info "You may need to manually configure PostgreSQL authentication"
        return 0
    fi

    print_info "Found pg_hba.conf at: $pg_hba_conf"

    # Backup original file
    cp "$pg_hba_conf" "${pg_hba_conf}.bak" 2>/dev/null

    # Change peer/ident to md5 for local connections
    # BUT keep peer authentication for postgres user (needed for admin commands via su)
    # CRITICAL: postgres user needs peer auth to create other users/databases

    # Always ensure postgres peer entry exists at the beginning of the file
    # (must be first rule so it matches before the general "local all all" rule)

    # First, remove any existing postgres-specific entries (we'll add a fresh one)
    sed -i '/^local[[:space:]]*postgres[[:space:]]*postgres/d' "$pg_hba_conf" 2>/dev/null

    # Add postgres peer entry at the beginning
    { echo "local   postgres        postgres                                peer"; cat "$pg_hba_conf"; } > "${pg_hba_conf}.tmp" && mv "${pg_hba_conf}.tmp" "$pg_hba_conf"
    print_info "Added postgres peer authentication rule at top"

    # Change all "local all" entries from peer/ident to md5 (using flexible pattern)
    sed -i -E 's/^local[[:space:]]+all[[:space:]]+all[[:space:]]+(peer|ident)[[:space:]]*$/local   all             all                                     md5/' "$pg_hba_conf" 2>/dev/null || true

    # Also change ident to md5 for host connections
    sed -i 's/ident$/md5/' "$pg_hba_conf" 2>/dev/null || true

    # Reload PostgreSQL to apply changes
    if systemctl is-active --quiet postgresql 2>/dev/null; then
        systemctl reload postgresql 2>/dev/null || systemctl restart postgresql 2>/dev/null
        print_success "PostgreSQL authentication configured (reloaded)"
    elif systemctl is-active --quiet postgres 2>/dev/null; then
        systemctl reload postgres 2>/dev/null || systemctl restart postgres 2>/dev/null
        print_success "PostgreSQL authentication configured (reloaded)"
    else
        print_warning "PostgreSQL not running, will apply changes on next start"
    fi

    return 0
}


install_postgresql_binary() {
    print_info "Installing PostgreSQL (binary)..."

    if [ "$EUID" -ne 0 ]; then
        print_error "Root privileges required to install PostgreSQL"
        return 1
    fi

    # Detect OS and install
    if command -v apt-get &>/dev/null; then
        print_info "Using apt to install PostgreSQL..."
        apt-get update -qq
        apt-get install -y postgresql postgresql-contrib
    elif command -v yum &>/dev/null; then
        print_info "Using yum to install PostgreSQL..."
        yum install -y postgresql-server postgresql-contrib
        # Initialize database on RHEL/CentOS
        postgresql-setup initdb 2>/dev/null || postgresql-setup --initdb 2>/dev/null || true
        systemctl start postgresql
        systemctl enable postgresql
    elif command -v dnf &>/dev/null; then
        print_info "Using dnf to install PostgreSQL..."
        dnf install -y postgresql-server postgresql-contrib
        postgresql-setup --initdb 2>/dev/null || postgresql-setup initdb 2>/dev/null || true
        systemctl start postgresql
        systemctl enable postgresql
    else
        print_error "Unsupported package manager. Please install PostgreSQL manually."
        return 1
    fi

    # Start service if not running
    if systemctl is-active --quiet postgresql 2>/dev/null; then
        print_success "PostgreSQL service started"
    else
        systemctl start postgresql 2>/dev/null || systemctl start postgres 2>/dev/null || {
            print_error "Failed to start PostgreSQL service"
            return 1
        }
    fi

    # Configure pg_hba.conf for password authentication
    configure_pg_hba_conf

    print_success "PostgreSQL installed successfully"
    return 0
}

# Install PostgreSQL as Docker container
install_postgresql_docker() {
    print_info "Installing PostgreSQL (Docker)..."

    if [ "$EUID" -ne 0 ]; then
        print_error "Root privileges required to install PostgreSQL with Docker"
        return 1
    fi

    # Check if Docker is available
    if ! check_docker_available; then
        print_error "Docker is not available or not running"
        return 1
    fi

    # Create data directory for PostgreSQL
    local pg_data_dir="/var/lib/openace-postgres"
    mkdir -p "$pg_data_dir"

    # Generate a random password
    local pg_password=$(openssl rand -hex 12 2>/dev/null || echo "$(date +%s)$$" | sha256sum | head -c 24)

    # Run PostgreSQL container
    docker run -d \
        --name openace-postgres \
        -e POSTGRES_USER="$DB_USER" \
        -e POSTGRES_PASSWORD="$pg_password" \
        -e POSTGRES_DB="$DB_NAME" \
        -p "$DB_PORT":5432 \
        -v "$pg_data_dir":/var/lib/postgresql/data \
        --restart unless-stopped \
        postgres:16

    if [ $? -eq 0 ]; then
        DB_PASSWORD="$pg_password"
        print_success "PostgreSQL Docker container started"
        print_info "Container name: openace-postgres"
        print_info "Data directory: $pg_data_dir"
        print_info "Database user: $DB_USER"
        print_info "Database password: $pg_password"
        print_warning "Please save the password for future reference!"
        return 0
    else
        print_error "Failed to start PostgreSQL Docker container"
        return 1
    fi
}

# Create database user and database
configure_postgresql() {
    print_info "Configuring PostgreSQL database..."

    # Generate password if not set
    if [ -z "$DB_PASSWORD" ]; then
        DB_PASSWORD=$(openssl rand -hex 12 2>/dev/null || echo "$(date +%s)$$" | sha256sum | head -c 24)
    fi

    # Create user and database
    if [ "$DB_INSTALL_METHOD" = "docker" ]; then
        # For Docker, user and database are created automatically via environment variables
        print_info "Database already created by Docker container"
    else
        # For binary installation, need to create user and database manually
        print_info "Creating database user and database..."

        # Check if user already exists
        local user_exists=$(su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'\"" 2>/dev/null)

        if [ "$user_exists" = "1" ]; then
            print_info "Database user '$DB_USER' already exists"
        else
            su - postgres -c "psql -c \"CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';\"" 2>/dev/null && \
                print_success "Created database user: $DB_USER" || \
                print_warning "Failed to create user (may already exist)"
        fi

        # Check if database exists
        local db_exists=$(su - postgres -c "psql -lqt | cut -d \| -f 1 | grep -qw $DB_NAME" 2>/dev/null && echo "1")

        if [ "$db_exists" = "1" ]; then
            print_info "Database '$DB_NAME' already exists"
        else
            su - postgres -c "psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_USER;\"" 2>/dev/null && \
                print_success "Created database: $DB_NAME" || \
                print_warning "Failed to create database (may already exist)"
        fi

        # Grant privileges
        su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;\"" 2>/dev/null
    fi

    print_success "PostgreSQL configured"
    print_info "Database URL: postgresql://$DB_USER:****@$DB_HOST:$DB_PORT/$DB_NAME"
}

# Setup PostgreSQL - detect existing or install new
setup_postgresql() {
    print_header "Database Setup"

    # Check if database configuration is already set from config file
    # If DB_PASSWORD is set and DB_INSTALL_METHOD is specified, use those values
    if [ -n "$DB_PASSWORD" ] && [ -n "$DB_INSTALL_METHOD" ]; then
        print_info "Using database configuration from config file"
        print_success "Database host: $DB_HOST"
        print_success "Database port: $DB_PORT"
        print_success "Database name: $DB_NAME"
        print_success "Database user: $DB_USER"
        print_info "Database password: <configured>"
        print_info "Install method: $DB_INSTALL_METHOD"
        return 0
    fi

    # Check for existing PostgreSQL
    local has_systemd=false
    local has_process=false
    local has_docker=false

    if command -v systemctl &>/dev/null; then
        check_postgresql_systemd && has_systemd=true
    fi

    if ! $has_systemd; then
        check_postgresql_process && has_process=true
    fi

    check_postgresql_docker && has_docker=true

    # If DB_PASSWORD is set (from config file), skip interactive prompts
    if [ -n "$DB_PASSWORD" ]; then
        print_success "PostgreSQL is already running"
        DB_INSTALL_METHOD="existing"
        print_info "Using database configuration from config file"
        print_success "Database host: $DB_HOST"
        print_success "Database port: $DB_PORT"
        print_success "Database name: $DB_NAME"
        print_success "Database user: $DB_USER"
        return 0
    fi

    if $has_systemd || $has_process || $has_docker; then
        print_success "PostgreSQL is already running"
        DB_INSTALL_METHOD="existing"

        # Try to reuse existing database configuration
        local config_file="$EXISTING_CONFIG_PATH"

        # If no preserved path, dynamically find existing config
        if [ -z "$config_file" ]; then
            config_file=$(find_existing_config_file)
        fi

        if [ -n "$config_file" ] && [ -f "$config_file" ]; then
            local existing_db_url=$(python3 -c "import json; c=json.load(open('$config_file')); print(c.get('database', {}).get('url', ''))" 2>/dev/null)

            if [ -n "$existing_db_url" ]; then
                print_info "Found existing database configuration in: $config_file"
                prompt_yesno "Use existing database configuration?" "y" use_existing_db

                if [ "$use_existing_db" = "yes" ]; then
                    # Parse URL and fill database parameters
                    DB_HOST=$(echo "$existing_db_url" | python3 -c "import sys, urllib.parse; u=urllib.parse.urlparse(sys.stdin.read().strip()); print(u.hostname or 'localhost')")
                    DB_PORT=$(echo "$existing_db_url" | python3 -c "import sys, urllib.parse; u=urllib.parse.urlparse(sys.stdin.read().strip()); print(u.port or 5432)")
                    DB_NAME=$(echo "$existing_db_url" | python3 -c "import sys, urllib.parse; u=urllib.parse.urlparse(sys.stdin.read().strip()); print(u.path.lstrip('/') or 'openace')")
                    DB_USER=$(echo "$existing_db_url" | python3 -c "import sys, urllib.parse; u=urllib.parse.urlparse(sys.stdin.read().strip()); print(u.username or 'openace')")
                    DB_PASSWORD=$(echo "$existing_db_url" | python3 -c "import sys, urllib.parse; u=urllib.parse.urlparse(sys.stdin.read().strip()); print(u.password or '')")

                    print_success "Using existing database configuration"
                    print_info "  Host: $DB_HOST"
                    print_info "  Port: $DB_PORT"
                    print_info "  Name: $DB_NAME"
                    print_info "  User: $DB_USER"
                    return 0
                fi
            fi
        fi

        # No existing config found or user chose not to use it, proceed with manual input
        local detected_port=$(get_postgresql_port)
        prompt_input "Database host" "$DB_HOST" DB_HOST
        prompt_input "Database port" "$detected_port" DB_PORT
        prompt_input "Database name" "$DB_NAME" DB_NAME
        prompt_input "Database user" "$DB_USER" DB_USER
        prompt_input "Database password" "" DB_PASSWORD

        if [ -z "$DB_PASSWORD" ]; then
            print_warning "No password provided. Generating random password..."
            DB_PASSWORD=$(openssl rand -hex 12 2>/dev/null || echo "$(date +%s)$$" | sha256sum | head -c 24)
            print_info "Generated password: $DB_PASSWORD"
            print_warning "Please save this password for future reference!"
        fi

        return 0
    fi

    # No PostgreSQL running - need to install
    print_info "No PostgreSQL instance found running"

    # If DB_INSTALL_METHOD is set from config file, use it
    if [ -n "$DB_INSTALL_METHOD" ] && [ "$DB_INSTALL_METHOD" != "existing" ]; then
        print_info "Using install method from config file: $DB_INSTALL_METHOD"
    else
        # Check if Docker is available
        local docker_available=false
        check_docker_available && docker_available=true

        if $docker_available; then
            print_info "Docker is available. Choose installation method:"
            echo "  1) Install as binary service (Recommended for production)"
            echo "  2) Install as Docker container"
            echo ""
            prompt_input "Enter choice" "1" pg_method

            case $pg_method in
                1)
                    DB_INSTALL_METHOD="binary"
                    ;;
                2)
                    DB_INSTALL_METHOD="docker"
                    ;;
                *)
                    DB_INSTALL_METHOD="binary"
                    ;;
            esac
        else
            print_info "Docker not available. Installing as binary service."
            DB_INSTALL_METHOD="binary"
        fi
    fi

    # Install PostgreSQL
    case $DB_INSTALL_METHOD in
        binary)
            if install_postgresql_binary; then
                configure_postgresql
            else
                print_error "Failed to install PostgreSQL"
                print_info "Please install PostgreSQL manually and configure config.json"
                return 1
            fi
            ;;
        docker)
            if install_postgresql_docker; then
                print_success "PostgreSQL Docker container created user and database"
            else
                print_error "Failed to install PostgreSQL"
                print_info "Please install PostgreSQL manually and configure config.json"
                return 1
            fi
            ;;
    esac

    return 0
}

# Update config.json with database settings
update_config_database() {
    local config_file="$1"

    if [ ! -f "$config_file" ]; then
        print_warning "Config file not found: $config_file"
        return 1
    fi

    # Build database URL
    local db_url="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"

    # Update config.json
    print_info "Updating database configuration in $config_file..."

    # Use Python to update JSON (more reliable than sed for JSON)
    if command -v python3 &>/dev/null; then
        python3 -c "
import json
with open('$config_file', 'r') as f:
    config = json.load(f)
config['database'] = {
    'type': '$DB_TYPE',
    'url': '$db_url'
}
with open('$config_file', 'w') as f:
    json.dump(config, f, indent=2)
" 2>/dev/null && print_success "Database configuration updated" || {
            # Fallback: use sed
            sed -i "s|postgresql://.*|$db_url|g" "$config_file" 2>/dev/null || true
            print_warning "Used fallback method to update config"
        }
    else
        # Fallback: use sed
        sed -i "s|postgresql://.*|$db_url|g" "$config_file" 2>/dev/null || true
        print_warning "Used sed to update config (may not work for all formats)"
    fi

    return 0
}

# Update config.json with workspace settings
update_config_workspace() {
    local config_file="$1"
    local webui_path="$2"

    if [ ! -f "$config_file" ]; then
        print_warning "Config file not found: $config_file"
        return 1
    fi

    print_info "Updating workspace configuration in $config_file..."

    # Use Python to update JSON
    # Convert bash "true"/"false" to Python True/False
    if command -v python3 &>/dev/null; then
        # Use environment variables to pass values safely (avoids special character issues in heredoc)
        export _CONFIG_FILE="$config_file"
        export _WS_ENABLED="$WORKSPACE_ENABLED"
        export _WS_MULTI_USER="$WORKSPACE_MULTI_USER_MODE"
        export _WS_PORT_START="$WORKSPACE_PORT_RANGE_START"
        export _WS_PORT_END="$WORKSPACE_PORT_RANGE_END"
        export _WS_MAX_INSTANCES="$WORKSPACE_MAX_INSTANCES"
        export _WS_IDLE_TIMEOUT="$WORKSPACE_IDLE_TIMEOUT"
        export _WS_WEBUI_PATH="$webui_path"

        python3 << 'EOF'
import json
import os
import secrets

with open(os.environ.get('_CONFIG_FILE', ''), 'r') as f:
    config = json.load(f)

if 'workspace' not in config:
    config['workspace'] = {}

# Convert bash string to Python bool
def bash_to_bool(val):
    return val.lower() == 'true'

config['workspace']['enabled'] = bash_to_bool(os.environ.get('_WS_ENABLED', 'false'))
config['workspace']['multi_user_mode'] = bash_to_bool(os.environ.get('_WS_MULTI_USER', 'false'))
config['workspace']['port_range_start'] = int(os.environ.get('_WS_PORT_START', '3100'))
config['workspace']['port_range_end'] = int(os.environ.get('_WS_PORT_END', '3200'))
config['workspace']['max_instances'] = int(os.environ.get('_WS_MAX_INSTANCES', '30'))
config['workspace']['idle_timeout_minutes'] = int(os.environ.get('_WS_IDLE_TIMEOUT', '30'))
config['workspace']['webui_path'] = os.environ.get('_WS_WEBUI_PATH', '')

# Generate token_secret if not already set (for multi-user mode security)
if not config['workspace'].get('token_secret'):
    config['workspace']['token_secret'] = secrets.token_hex(32)

# Set workspace URL - get server IP address
try:
    # Get local IP using hostname command
    import subprocess
    result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
    ips = result.stdout.strip().split()
    if ips:
        # Use first non-localhost IP
        for ip in ips:
            if not ip.startswith('127.') and not ip.startswith('169.254'):
                config['workspace']['url'] = 'http://' + ip
                break
        else:
            # All IPs are localhost/link-local, use first one
            config['workspace']['url'] = 'http://' + ips[0]
    else:
        # Fallback to hostname
        config['workspace']['url'] = 'http://localhost'
except:
    # Fallback to localhost
    config['workspace']['url'] = 'http://localhost'

with open(os.environ.get('_CONFIG_FILE', ''), 'w') as f:
    json.dump(config, f, indent=2)
print("Workspace configuration updated")
EOF
        if [ $? -eq 0 ]; then
            print_success "Workspace configuration updated"
        else
            print_warning "Failed to update workspace configuration"
            return 1
        fi
    else
        print_warning "Python3 not available, cannot update workspace config"
        return 1
    fi

    return 0
}

# Update config.json with secret_key for Flask session and API key encryption
update_config_secret_key() {
    local config_file="$1"
    local secret_key="$2"

    if [ ! -f "$config_file" ]; then
        print_warning "Config file not found: $config_file"
        return 1
    fi

    if [ -z "$secret_key" ]; then
        print_warning "No secret_key provided"
        return 1
    fi

    print_info "Updating secret_key in $config_file..."

    # Use Python to update JSON
    if command -v python3 &>/dev/null; then
        python3 -c "
import json
with open('$config_file', 'r') as f:
    config = json.load(f)
config['secret_key'] = '$secret_key'
with open('$config_file', 'w') as f:
    json.dump(config, f, indent=2)
" 2>/dev/null && print_success "secret_key configuration updated" || {
            print_warning "Failed to update secret_key in config"
            return 1
        }
    else
        print_warning "Python3 not available, cannot update secret_key config"
        return 1
    fi

    return 0
}

# Systemd service settings
SERVICE_PORT=""       # Web server port (will be read from config or use default)
SERVICE_HOST="0.0.0.0" # Web server host

# Workspace configuration defaults
WORKSPACE_ENABLED="true"
WORKSPACE_MULTI_USER_MODE="true"
WORKSPACE_PORT_RANGE_START="3100"
WORKSPACE_PORT_RANGE_END="3200"
WORKSPACE_MAX_INSTANCES="30"
WORKSPACE_IDLE_TIMEOUT="30"
WORKSPACE_URL=""      # Workspace URL (will be set based on host_name or server_url)
WORKSPACE_BASE_DIR="/home"  # Default workspace base directory for Package version

# Upgrade systemd service switch decision (set in verify_upgrade_systemd_config)
UPGRADE_SWITCH_SERVICE="no"

# Data directories to preserve during upgrade
DATA_DIRS=(
    "data"
    "logs"
)

# Data files to preserve (in ~/.open-ace/)
DATA_FILES=(
    "usage.db"
    "config.json"
    "feishu_users.json"
    "dingtalk_users.json"
    "dingtalk_groups.json"
    "upload_marker.json"
)

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
    echo -e "${GREEN}[OK] $1${NC}"
}

print_error() {
    echo -e "${RED}[FAIL] $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

print_info() {
    echo -e "${BLUE}[INFO] $1${NC}"
}

prompt_input() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    # Output directly to terminal to avoid buffering issues
    if [ -n "$default" ]; then
        printf "${BLUE}%s [%s]: ${NC}" "$prompt" "$default" > /dev/tty
    else
        printf "${BLUE}%s: ${NC}" "$prompt" > /dev/tty
    fi

    # Read from terminal directly
    read -r value < /dev/tty

    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi

    eval "$var_name='$value'"
}

prompt_yesno() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    local options="[Y/n]"
    [ "$default" = "n" ] && options="[y/N]"

    # Output directly to terminal to avoid buffering issues
    printf "${BLUE}%s %s: ${NC}" "$prompt" "$options" > /dev/tty

    # Read from terminal directly
    read -r value < /dev/tty

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

# Prompt for deployment path with validation.
# Ensures the path is a non-empty absolute path, re-prompting on invalid input.
# Usage: prompt_deploy_path <prompt_text> <default_value>
prompt_deploy_path() {
    local prompt_text="$1"
    local default_value="$2"

    while true; do
        prompt_input "$prompt_text" "$default_value" DEPLOY_PATH
        # Remove trailing slash
        DEPLOY_PATH="${DEPLOY_PATH%/}"

        if [ -z "$DEPLOY_PATH" ]; then
            print_error "Deployment path cannot be empty. Please enter an absolute path."
            continue
        fi

        if [[ "$DEPLOY_PATH" != /* ]]; then
            print_error "Deployment path must be an absolute path (starting with /): $DEPLOY_PATH"
            print_info "Example: /home/$DEPLOY_USER/open-ace"
            # Use the suggested default for next iteration
            default_value="/home/$DEPLOY_USER/open-ace"
            continue
        fi

        # Valid absolute path
        break
    done
}

# ============================================================================
# Multi-User Workspace Sudo Configuration
# ============================================================================

# Stop and disable qwen-code-webui systemd service if it exists
# Open ACE will manage qwen-code-webui instances in multi-user mode
stop_webui_systemd_service() {
    # Check if systemd is available
    if ! command -v systemctl &>/dev/null; then
        return 0
    fi

    # Check if qwen-code-webui service exists
    local service_name="qwen-code-webui"
    # List all service unit files and check if our service exists
    if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${service_name}.service"; then
        print_warning "Detected existing qwen-code-webui systemd service"
        print_info "In multi-user mode, Open ACE automatically manages qwen-code-webui instances"
        print_info "Stopping and disabling standalone qwen-code-webui services..."

        # Stop the service (need sudo for system service)
        if sudo systemctl is-active --quiet "${service_name}.service" 2>/dev/null; then
            sudo systemctl stop "${service_name}.service"
            if [ $? -eq 0 ]; then
                print_success "Stopped ${service_name} service"
            else
                print_warning "Failed to stop ${service_name} service"
            fi
        fi

        # Disable the service (need sudo for system service)
        if sudo systemctl is-enabled --quiet "${service_name}.service" 2>/dev/null; then
            sudo systemctl disable "${service_name}.service"
            if [ $? -eq 0 ]; then
                print_success "Disabled ${service_name} service"
            else
                print_warning "Failed to disable ${service_name} service"
            fi
        fi

        print_info "Open ACE will automatically start qwen-code-webui instances when needed"
    fi

    return 0
}

# Install qwen-code-webui via npm if not found
install_webui() {
    print_info "Installing qwen-code-webui via npm..."
    print_info "This may take several minutes, please wait..."

    # Check if npm is available
    if ! command -v npm &>/dev/null; then
        print_warning "npm not found, installing Node.js via NodeSource..."
        print_info "Downloading Node.js 20.x setup script..."
        if [ "$EUID" -eq 0 ]; then
            # Use NodeSource to get Node.js 20.x
            if command -v dnf &>/dev/null || command -v yum &>/dev/null; then
                curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
                if command -v dnf &>/dev/null; then
                    dnf install -y nodejs
                else
                    yum install -y nodejs
                fi
            elif command -v apt-get &>/dev/null; then
                curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
                apt-get install -y nodejs
            else
                print_error "Cannot install Node.js automatically on this system"
                print_info "Please install Node.js 20+ manually"
                return 1
            fi
        else
            print_error "Not running as root, cannot install Node.js automatically"
            print_info "Please run with sudo: curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - && sudo yum install -y nodejs"
            return 1
        fi
    fi

    # Install qwen-code-webui globally (with progress)
    print_info "Downloading qwen-code-webui package..."
    print_info "Package size: ~50MB, this may take 2-5 minutes depending on network speed"
    if npm install -g qwen-code-webui; then
        print_success "qwen-code-webui installed successfully"
    else
        print_error "Failed to install qwen-code-webui"
        return 1
    fi

    # Check and install qwen-code CLI (required by qwen-code-webui)
    if ! command -v qwen &>/dev/null; then
        print_info ""
        print_info "qwen-code CLI not found, installing..."
        print_info "This is required for qwen-code-webui to function"
        print_info "Package size: ~30MB, this may take 1-3 minutes"
        if npm install -g @qwen-code/qwen-code; then
            print_success "qwen-code CLI installed successfully"
        else
            print_warning "Failed to install qwen-code CLI automatically"
            print_info "You may need to install it manually: npm install -g @qwen-code/qwen-code"
        fi
    else
        print_success "qwen-code CLI already installed"
    fi

    # Create symlinks in /usr/bin for easier access
    create_webui_symlinks

    return 0
}

# Create symlinks in /usr/bin for qwen-code-webui and qwen-code executables
# This ensures all users can access these commands regardless of npm global install location
create_webui_symlinks() {
    # Check if running as root (required to write to /usr/bin)
    if [ "$EUID" -ne 0 ]; then
        print_warning "Not running as root, cannot create symlinks in /usr/bin"
        print_info "Symlinks will be created by the installer if run with sudo"
        return 0
    fi

    print_info "Creating symlinks in /usr/bin..."

    # Find npm global bin directory
    local npm_bin_dir=""
    if command -v npm &>/dev/null; then
        npm_bin_dir=$(npm bin -g 2>/dev/null || npm prefix -g 2>/dev/null | xargs -I{} echo "{}/bin")
    fi

    # Common npm global bin locations
    local candidates=(
        "$npm_bin_dir"
        "/usr/local/bin"
        "/usr/bin"
        "$HOME/.npm-global/bin"
    )

    # Find actual executable locations
    local webui_actual=""
    local qwen_actual=""

    for dir in "${candidates[@]}"; do
        if [ -n "$dir" ] && [ -d "$dir" ]; then
            if [ -x "$dir/qwen-code-webui" ] && [ -z "$webui_actual" ]; then
                webui_actual="$dir/qwen-code-webui"
            fi
            if [ -x "$dir/qwen" ] && [ -z "$qwen_actual" ]; then
                qwen_actual="$dir/qwen"
            fi
        fi
    done

    # Also check using which/command -v
    if [ -z "$webui_actual" ] && command -v qwen-code-webui &>/dev/null; then
        webui_actual=$(which qwen-code-webui 2>/dev/null || command -v qwen-code-webui)
    fi
    if [ -z "$qwen_actual" ] && command -v qwen &>/dev/null; then
        qwen_actual=$(which qwen 2>/dev/null || command -v qwen)
    fi

    # Create symlink for qwen-code-webui
    if [ -n "$webui_actual" ] && [ -x "$webui_actual" ]; then
        local webui_link="/usr/bin/qwen-code-webui"
        if [ "$webui_actual" != "$webui_link" ]; then
            # Remove existing symlink or file if it exists
            if [ -L "$webui_link" ] || [ -e "$webui_link" ]; then
                rm -f "$webui_link"
            fi
            # Create new symlink
            if ln -sf "$webui_actual" "$webui_link"; then
                print_success "Created symlink: $webui_link -> $webui_actual"
            else
                print_warning "Failed to create symlink for qwen-code-webui"
            fi
        else
            print_info "qwen-code-webui already in /usr/bin, no symlink needed"
        fi
    else
        print_warning "qwen-code-webui executable not found, cannot create symlink"
    fi

    # Create symlink for qwen-code (qwen CLI)
    if [ -n "$qwen_actual" ] && [ -x "$qwen_actual" ]; then
        local qwen_link="/usr/bin/qwen"
        if [ "$qwen_actual" != "$qwen_link" ]; then
            # Remove existing symlink or file if it exists
            if [ -L "$qwen_link" ] || [ -e "$qwen_link" ]; then
                rm -f "$qwen_link"
            fi
            # Create new symlink
            if ln -sf "$qwen_actual" "$qwen_link"; then
                print_success "Created symlink: $qwen_link -> $qwen_actual"
            else
                print_warning "Failed to create symlink for qwen"
            fi
        else
            print_info "qwen already in /usr/bin, no symlink needed"
        fi
    else
        print_warning "qwen executable not found, cannot create symlink"
    fi

    return 0
}

# Find qwen-code-webui executable
find_webui_executable() {
    local candidates=(
        "/usr/bin/qwen-code-webui"
        "/usr/local/bin/qwen-code-webui"
        "/opt/qwen-code-webui/bin/qwen-code-webui"
        "$HOME/.local/bin/qwen-code-webui"
        "$HOME/.npm-global/bin/qwen-code-webui"
    )

    # First, check if qwen-code-webui is already installed
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    # Try to find in PATH
    if command -v qwen-code-webui &>/dev/null; then
        which qwen-code-webui
        return 0
    fi

    # Not found, check if npm is available
    print_warning "qwen-code-webui not found" >&2
    if command -v npm &>/dev/null; then
        print_info "npm is available, installing qwen-code-webui..." >&2
        print_info "This may take several minutes, please wait..." >&2
        print_info "Downloading qwen-code-webui (~50MB)..." >&2
        if npm install -g qwen-code-webui >&2; then
            print_success "qwen-code-webui installed successfully" >&2
            # Check and install qwen-code CLI (required by qwen-code-webui)
            if ! command -v qwen &>/dev/null; then
                print_info "" >&2
                print_info "qwen-code CLI not found, installing..." >&2
                print_info "This is required for qwen-code-webui to function" >&2
                print_info "Downloading qwen-code (~30MB)..." >&2
                npm install -g @qwen-code/qwen-code >&2 || print_warning "Failed to install qwen-code CLI" >&2
            fi
            # Create symlinks in /usr/bin (if running as root)
            create_webui_symlinks >&2
            # Try to find again after installation
            if command -v qwen-code-webui &>/dev/null; then
                which qwen-code-webui
                return 0
            fi
            # Check common paths again
            for candidate in "${candidates[@]}"; do
                if [ -x "$candidate" ]; then
                    echo "$candidate"
                    return 0
                fi
            done
        else
            print_error "Failed to install qwen-code-webui via npm" >&2
            return 1
        fi
    else
        # npm not available, need to install Node.js first
        print_info "npm not available, installing Node.js 20.x via NodeSource..." >&2
        if install_webui >&2; then
            # Try to find again after installation
            if command -v qwen-code-webui &>/dev/null; then
                which qwen-code-webui
                return 0
            fi
            # Check common paths again
            for candidate in "${candidates[@]}"; do
                if [ -x "$candidate" ]; then
                    echo "$candidate"
                    return 0
                fi
            done
        fi
    fi

    return 1
}

# Install the cross-user agent launcher wrapper (Issue #1395).
# The wrapper lets the openace service launch agent CLIs (claude-code/qwen-code/
# openclaw) with cwd=project_path under a user-private repo, by chdir'ing as
# root then dropping to the target user via runuser. Must run BEFORE
# configure_sudoers so the sudoers rule (which keys off -x $wrapper_path) sees it.
install_run_as_wrapper() {
    local install_dir="$1"
    local src="$install_dir/scripts/openace-run-as.sh"
    local dst="/usr/local/bin/openace-run-as"

    if [ ! -f "$src" ]; then
        print_warning "openace-run-as.sh not found at $src; skipping wrapper install"
        return 1
    fi
    if ! cp "$src" "$dst" 2>/dev/null; then
        print_warning "Failed to copy openace-run-as.sh to $dst (need root?)"
        return 1
    fi
    chown root:root "$dst" 2>/dev/null || true
    chmod 755 "$dst"

    local guard_src="$install_dir/app/modules/workspace/autonomous/agent_bin"
    local guard_dst="/usr/local/libexec/openace-agent-bin"
    if [ ! -d "$guard_src" ]; then
        print_warning "Autonomous agent command guards not found at $guard_src"
        return 1
    fi
    install -d -o root -g root -m 755 "$guard_dst" || return 1
    local guard_name
    for guard_name in _guard_exec.py git gh python python3 pytest; do
        if [ ! -f "$guard_src/$guard_name" ]; then
            print_warning "Missing autonomous agent command guard: $guard_name"
            return 1
        fi
        install -o root -g root -m 755 "$guard_src/$guard_name" "$guard_dst/$guard_name" \
            || return 1
    done

    # The AI process must never share the repository owner's GitHub/SSH
    # credentials.  Provision a locked, non-login principal used only by the
    # isolated run-as path; the wrapper grants per-worktree ACLs at launch.
    if ! id openace-agent >/dev/null 2>&1; then
        local nologin_shell
        nologin_shell="$(command -v nologin 2>/dev/null || echo /bin/false)"
        useradd --system --create-home --home-dir /var/lib/openace-agent \
            --shell "$nologin_shell" openace-agent || {
            print_warning "Failed to create credentialless openace-agent account"
            return 1
        }
    fi
    print_success "Installed run-as wrapper to $dst"
    return 0
}

# Configure the minimal privilege required by every local autonomous workflow,
# including the default single-user install.  This is intentionally separate
# from the broad multi-user workspace sudoers file.
configure_autonomous_agent_sudoers() {
    local run_user="$1"
    local install_dir="$2"
    local wrapper_path="/usr/local/bin/openace-run-as"
    local sudoers_file="/etc/sudoers.d/open-ace-autonomous-agent"

    install_run_as_wrapper "$install_dir" || return 1
    cat > "$sudoers_file" << SUDOERS_EOF
# Credentialless autonomous agent launcher (generated by Open ACE installer)
$run_user ALL=(root) NOPASSWD: $wrapper_path --isolated *
SUDOERS_EOF
    chmod 440 "$sudoers_file"
    if ! visudo -c -f "$sudoers_file" >/dev/null 2>&1; then
        unlink "$sudoers_file" 2>/dev/null || true
        print_warning "Invalid autonomous agent sudoers configuration"
        return 1
    fi
    # Retire the standalone broad rule emitted by older/manual deployments.
    # The new file above is already validated and grants the same service user
    # only the isolated form, so leaving this file active would negate it.
    local legacy_sudoers_file="/etc/sudoers.d/openace-run-as"
    if [ -f "$legacy_sudoers_file" ] && grep -qF "$wrapper_path" "$legacy_sudoers_file"; then
        mv "$legacy_sudoers_file" "${legacy_sudoers_file}.disabled.$(date +%s)"
        print_warning "Disabled legacy broad autonomous-agent sudoers rule"
    fi
    print_success "Configured credentialless autonomous agent launcher for $run_user"
}

# Install the same credentialless-agent boundary on an SSH deployment target.
# Both fresh installs and upgrades call this after copying the current scripts.
configure_autonomous_agent_remote() {
    local remote="$1"
    local target_path="$2"
    local staged_wrapper

    staged_wrapper=$(ssh "$remote" "mktemp /tmp/openace-run-as.XXXXXX") || return 1
    if ! [[ "$staged_wrapper" =~ ^/tmp/openace-run-as\.[A-Za-z0-9]+$ ]]; then
        print_warning "Remote host returned an invalid wrapper staging path"
        return 1
    fi
    if ! scp "$SOURCE_DIR/scripts/openace-run-as.sh" "$remote:$staged_wrapper"; then
        ssh "$remote" "rm -f '$staged_wrapper'" 2>/dev/null || true
        return 1
    fi
    if ! ssh "$remote" "bash -s -- '$staged_wrapper' '$DEPLOY_USER' '$target_path'" <<'REMOTE_AUTONOMOUS_SETUP'
set -euo pipefail
staged_wrapper="$1"
deploy_user="$2"
target_path="$3"
rule_tmp=""
cleanup_remote_setup() {
    rm -f "$staged_wrapper" "$rule_tmp"
}
trap cleanup_remote_setup EXIT HUP INT TERM
as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

as_root install -o root -g root -m 755 "$staged_wrapper" /usr/local/bin/openace-run-as
guard_src="$target_path/app/modules/workspace/autonomous/agent_bin"
guard_dst="/usr/local/libexec/openace-agent-bin"
[ -d "$guard_src" ] || { echo "Missing autonomous agent command guards: $guard_src" >&2; exit 1; }
as_root install -d -o root -g root -m 755 "$guard_dst"
for guard_name in _guard_exec.py git gh python python3 pytest; do
    [ -f "$guard_src/$guard_name" ] || { echo "Missing agent guard: $guard_name" >&2; exit 1; }
    as_root install -o root -g root -m 755 "$guard_src/$guard_name" "$guard_dst/$guard_name"
done
if ! id openace-agent >/dev/null 2>&1; then
    nologin_shell="$(command -v nologin 2>/dev/null || echo /bin/false)"
    as_root useradd --system --create-home --home-dir /var/lib/openace-agent \
        --shell "$nologin_shell" openace-agent
fi

service_user="$(systemctl show open-ace.service -p User --value 2>/dev/null || true)"
[ -n "$service_user" ] || service_user="$deploy_user"
if ! [[ "$service_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || ! id "$service_user" >/dev/null 2>&1; then
    echo "Invalid remote open-ace service user: $service_user" >&2
    exit 1
fi
rule_tmp="$(mktemp)"
printf '%s\n' \
    '# Credentialless autonomous agent launcher (generated by Open ACE installer)' \
    "$service_user ALL=(root) NOPASSWD: /usr/local/bin/openace-run-as --isolated *" \
    > "$rule_tmp"
# Validate before touching the active sudoers include. Install to an ignored
# dot-file first, then rename atomically so interruption cannot leave a partial
# rule that locks out subsequent sudo recovery.
as_root visudo -c -f "$rule_tmp" >/dev/null
as_root install -o root -g root -m 440 "$rule_tmp" \
    /etc/sudoers.d/.open-ace-autonomous-agent.new
as_root mv /etc/sudoers.d/.open-ace-autonomous-agent.new \
    /etc/sudoers.d/open-ace-autonomous-agent

if [ -f /etc/sudoers.d/openace-run-as ] && \
   grep -qF /usr/local/bin/openace-run-as /etc/sudoers.d/openace-run-as; then
    as_root mv /etc/sudoers.d/openace-run-as \
        "/etc/sudoers.d/openace-run-as.disabled.$(date +%s)"
fi

service_file="$(systemctl show open-ace.service -p FragmentPath --value 2>/dev/null || true)"
if [ -n "$service_file" ] && [ -f "$service_file" ]; then
    if grep -q '^NoNewPrivileges=' "$service_file"; then
        as_root sed -i 's/^NoNewPrivileges=.*/NoNewPrivileges=false/' "$service_file"
    else
        as_root sed -i '/^\[Service\]/a NoNewPrivileges=false' "$service_file"
    fi
    as_root systemctl daemon-reload
fi
REMOTE_AUTONOMOUS_SETUP
    then
        print_warning "Failed to configure credentialless autonomous agent on $remote"
        return 1
    fi
    print_success "Configured credentialless autonomous agent on $remote"
}

# Install the cross-user file write wrapper (Issue #1916).
# In Package non-root multi-user mode the service account cannot write to a
# user's 0700 home directory, and cp/tee/mv are NOT in the sudoers OPENACE_UTILS
# whitelist. This wrapper is invoked as root (via a dedicated sudoers rule) and
# drops to the target user via runuser to write uploaded file content. Must run
# BEFORE configure_sudoers so the sudoers rule (which keys off -x $wrapper_path)
# sees it.
install_write_as_wrapper() {
    local install_dir="$1"
    local src="$install_dir/scripts/openace-write-as.sh"
    local dst="/usr/local/bin/openace-write-as"

    if [ ! -f "$src" ]; then
        print_warning "openace-write-as.sh not found at $src; skipping wrapper install"
        return 1
    fi
    if ! cp "$src" "$dst" 2>/dev/null; then
        print_warning "Failed to copy openace-write-as.sh to $dst (need root?)"
        return 1
    fi
    chown root:root "$dst" 2>/dev/null || true
    chmod 755 "$dst"
    print_success "Installed write-as wrapper to $dst"
    return 0
}

# Configure sudoers for multi-user workspace mode
# Uses incremental update: only adds/modifies $run_user's rules, preserves other users' rules
configure_sudoers() {
    local run_user="$1"
    local install_dir="$2"  # Installation directory (e.g., /home/openace)

    print_header "Configure Sudo Permissions"

    # Check if running as root
    if [ "$(id -u)" -ne 0 ]; then
        print_error "Root privileges required to configure sudoers"
        print_info "Please run the installation script with sudo"
        return 1
    fi

    # Ensure symlinks are created first (if running as root)
    if [ "$EUID" -eq 0 ]; then
        create_webui_symlinks
    fi

    # Use /usr/bin/qwen-code-webui as preferred path (symlink created above)
    # Fallback to find_webui_executable if symlink doesn't exist
    local webui_path="/usr/bin/qwen-code-webui"
    if [ ! -x "$webui_path" ]; then
        webui_path=$(find_webui_executable 2>/dev/null)
    fi

    if [ -z "$webui_path" ]; then
        print_warning "qwen-code-webui executable not found"
        print_info "Please install qwen-code-webui first:"
        print_info "  npm install -g qwen-code-webui"
        print_info ""
        print_info "After installation, manually configure sudoers:"
        print_info "  sudo visudo -f /etc/sudoers.d/open-ace-webui"
        print_info "  Add: $run_user ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *"
        return 1
    fi

    print_success "Using qwen-code-webui path: $webui_path"

    # Create sudoers file
    local sudoers_file="/etc/sudoers.d/open-ace-webui"

    # Build fetch script rules (check all 5 scripts)
    # 【修复 Issue #1977】支持 Python 3.10+ 类型注解语法
    # 使用安装的 Python 版本，而非系统 /usr/bin/python3（可能是 3.9）
    local python_bin="${install_dir}/agent_bin/python3"
    if [ ! -x "$python_bin" ]; then
        python_bin="/usr/local/bin/python3.12"
    fi
    if [ ! -x "$python_bin" ]; then
        python_bin="/usr/bin/python3"
    fi
    local fetch_scripts=("fetch_qwen.py" "fetch_claude.py" "fetch_openclaw.py" "fetch_codex.py" "fetch_zcode.py")
    local fetch_rules=""
    for script in "${fetch_scripts[@]}"; do
        local script_path="$install_dir/scripts/$script"
        if [ -f "$script_path" ]; then
            fetch_rules="${fetch_rules}
# Allow $run_user to run $script as root for multi-user data collection
$run_user ALL=(root) NOPASSWD: $python_bin $script_path *"
        fi
    done

    # Add /usr/local/bin/qwen-code-webui rule if it exists (Node.js v20+ may be installed there)
    local webui_local_path="/usr/local/bin/qwen-code-webui"
    local webui_local_rule=""
    if [ -x "$webui_local_path" ] && [ "$webui_path" != "$webui_local_path" ]; then
        webui_local_rule="$run_user ALL=(ALL) NOPASSWD: $webui_local_path *"
    fi

    # 【修复 Issue #1262】使用 Cmnd_Alias 引用，避免重复定义命令列表
    # utility_rule 在用户规则中引用 OPENACE_UTILS Cmnd_Alias
    local utility_rule="$run_user ALL=(ALL) NOPASSWD: OPENACE_UTILS"

    # 【修复 Issue #1395】autonomous 开发 CLI 工具权限
    local cli_rule="$run_user ALL=(ALL) NOPASSWD: OPENACE_CLI"

    # 【修复 Issue #1395】跨用户启动 agent CLI 的 run-as wrapper 权限。
    # wrapper 以 root 身份 cd 到项目目录，再用 runuser 切换到目标用户 exec CLI，
    # 解决 Popen(cwd=用户私有目录) 的 [Errno 13]。只授权 wrapper 本身，不放开
    # bash/node/claude。wrapper 路径必须与 scripts/openace-run-as.sh 安装位置一致。
    local wrapper_path="/usr/local/bin/openace-run-as"
    local wrapper_rule=""
    if [ -x "$wrapper_path" ]; then
        wrapper_rule="$run_user ALL=(root) NOPASSWD: $wrapper_path --isolated *"
    fi

    # 【修复 Issue #1916】跨用户文件写入 wrapper 权限。
    # Package 非 root 多用户模式下，服务账号无法写入用户 0700 家目录，
    # 且 cp/tee/mv 不在 OPENACE_UTILS 白名单。该 wrapper 以 root 身份运行，
    # 内部用 runuser 切换到目标用户写文件。只授权 wrapper 本身。
    local write_as_wrapper_path="/usr/local/bin/openace-write-as"
    local write_as_wrapper_rule=""
    if [ -x "$write_as_wrapper_path" ]; then
        write_as_wrapper_rule="$run_user ALL=(root) NOPASSWD: $write_as_wrapper_path *"
    fi

    # Build current user's complete rule block (avoid empty lines from empty variables)
    local current_user_rules="# Rules for $run_user (updated on $(date '+%Y-%m-%d %H:%M:%S'))
$run_user ALL=(ALL) NOPASSWD: $webui_path *"

    # Only add webui_local_rule if not empty
    if [ -n "$webui_local_rule" ]; then
        current_user_rules="${current_user_rules}
${webui_local_rule}"
    fi

    # Always add utility and CLI rules (they reference Cmnd_Alias, never empty)
    current_user_rules="${current_user_rules}
${utility_rule}
${cli_rule}"

    # Add run-as wrapper rule if the wrapper is installed (Issue #1395)
    if [ -n "$wrapper_rule" ]; then
        current_user_rules="${current_user_rules}
${wrapper_rule}"
    fi

    # Add write-as wrapper rule if the wrapper is installed (Issue #1916)
    if [ -n "$write_as_wrapper_rule" ]; then
        current_user_rules="${current_user_rules}
${write_as_wrapper_rule}"
    fi

    # Only add fetch_rules if not empty
    if [ -n "$fetch_rules" ]; then
        current_user_rules="${current_user_rules}
${fetch_rules}"
    fi

    # Build header and defaults section
    local header="# Open ACE WebUI - Multi-user mode sudo configuration
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Allows the service account to run qwen-code-webui as other users
# and perform file system operations as other users"

    local defaults_section="# Preserve auth environment variables for qwen CLI authentication
# and webui environment variables for sudo env_keep passing
# GH_TOKEN and GIT_* vars are for autonomous dev GitHub operations (Issue #1517).
Defaults env_keep += \"OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR OPENACE_PROXY_TOKEN OPENACE_PROXY_URL SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH GH_TOKEN GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL\"

# Fix: Add /usr/local/bin to secure_path for Node.js v20+ compatibility
# qwen-code-webui requires Node.js >= 20, which may be installed in /usr/local/bin
# secure_path overrides PATH for sudo commands, so we must include /usr/local/bin here
# Order matters: /usr/local/bin first ensures newer Node.js is found before legacy versions
Defaults secure_path = /usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin"

    # 【修复 Issue #1262】Cmnd_Alias 独立定义，避免重复
    # 所有命令必须添加 * 后缀以允许任意参数（如 'test -r', 'chown user:group path'）
    # useradd 和 id 命令用于 Package 版 multi-user mode 创建系统用户（代码层面验证 uid >= 1000）
    # 【修复 Issue #1395】添加 autonomous 开发所需的 CLI 工具和 git/gh 命令
    # CLI 工具路径可能是 /usr/bin/qwen-code 或 /usr/local/bin/qwen-code（取决于 npm 安装方式）
    # 使用通配符覆盖所有可能的 CLI 工具路径
    local cmnd_alias_section="# Utility commands for multi-user workspace operations
# Commands must have '*' suffix to allow arguments (Issue #1262)
# useradd/id: for creating system users in Package multi-user mode (uid >= 1000 validated in code)
# git/gh: for autonomous development workflows (Issue #1395)
# cat/rm: for personal-files download/delete as the file owner (Issue #1902).
#   sudo -u <owner> cat|rm <path> — DAC constrains operations to files the
#   target user already owns/can access.
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *, /usr/bin/useradd *, /usr/bin/id *, /usr/bin/rm *, /usr/bin/find *, /usr/bin/git *, /usr/bin/gh *, /usr/local/bin/git *, /usr/local/bin/gh *

# Autonomous development CLI tools (Issue #1395)
# Allow running qwen-code/codex/etc. as target user for permission isolation
Cmnd_Alias OPENACE_CLI = /usr/bin/qwen-code *, /usr/local/bin/qwen-code *, /usr/bin/codex *, /usr/local/bin/codex *, /usr/bin/qwen *, /usr/local/bin/qwen *, /usr/bin/claude *, /usr/local/bin/claude *, /usr/bin/openclaw *, /usr/local/bin/openclaw *, /usr/bin/zcode *, /usr/local/bin/zcode *"

    # ===== Incremental update logic =====
    if [ -f "$sudoers_file" ]; then
        # 【修复 P0】Extract and preserve OTHER USERS' rules only.
        # The header, defaults, Cmnd_Alias definitions, and the current
        # $run_user's rules are all regenerated below, so they must NOT be
        # carried over — otherwise stale comments, duplicate Cmnd_Alias
        # definitions (visudo: "duplicate Cmnd_Alias"), or the current user's
        # own stale rule block leak into the rewritten file.
        #
        # Allow-list approach: keep only genuine user-spec lines belonging to
        # OTHER users, i.e. lines shaped "<user> ALL=...". A deny-list of
        # comment substrings is fragile (new comments silently slip through),
        # so match the structural pattern instead. This drops orphans
        # (comments, Cmnd_Alias, Defaults) and the current user's block.
        local other_user_rules=""
        while IFS= read -r line; do
            # Skip blank lines and comments outright.
            [[ -z "$line" || "$line" == \#* ]] && continue
            # Skip Defaults / Cmnd_Alias / host-alias etc. (regenerated below).
            [[ "$line" == Defaults* ]] && continue
            [[ "$line" == Cmnd_Alias* || "$line" == Host_Alias* || "$line" == User_Alias* || "$line" == Runas_Alias* ]] && continue
            # Skip the current user's rules (regenerated in current_user_rules).
            # A user-spec looks like "$run_user ALL=(...) NOPASSWD: ...".
            [[ "$line" == "$run_user "* ]] && continue
            # Anything else that isn't a "<token> ALL=" user-spec is structural
            # noise we don't know how to preserve safely — drop it rather than
            # risk a visudo failure.
            [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_-]*\ ALL= ]] || continue
            other_user_rules="${other_user_rules}
${line}"
        done < "$sudoers_file"

        # Trim leading empty line from other_user_rules
        other_user_rules=$(echo "$other_user_rules" | sed '/^$/d' | grep -v "^$" || true)

        # 【修复 P0】Verify fetch script paths match current install_dir (not just names)
        local need_update=false

        # Check webui rule for $run_user (match user+path on same line, not path
        # alone). On a service-user switch the sudoers file still holds the old
        # user's path rules, so a path-only grep would hit and skip the update,
        # leaving the new run_user without sudo permission (#1197 review).
        # Rule lines look like "$run_user ALL=(ALL) NOPASSWD: $webui_path *",
        # so we grep for lines starting with "$run_user " that also contain the path.
        if ! grep -E "^${run_user} .*(NOPASSWD: )?${webui_path}( |\*|$)" "$sudoers_file" 2>/dev/null && \
           ! grep -E "^${run_user} .*(NOPASSWD: )?/usr/local/bin/qwen-code-webui( |\*|$)" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers missing webui rule for user '$run_user'"
            need_update=true
        fi

        # Verify fetch script paths (check user+full-path on same line)
        for script in "${fetch_scripts[@]}"; do
            local script_path="$install_dir/scripts/$script"
            if [ -f "$script_path" ]; then
                if ! grep -E "^${run_user} .*(NOPASSWD: )?${script_path}( |\*|$)" "$sudoers_file" 2>/dev/null; then
                    print_warning "Sudoers missing or misconfigured rule for: $script_path ($run_user)"
                    need_update=true
                    break
                fi
            fi
        done

        # 【修复 P2】Check chown rule (consistent with docker-entrypoint.sh)
        # chown is defined in OPENACE_UTILS Cmnd_Alias (line 1862), not as a direct user rule
        # Check if OPENACE_UTILS Cmnd_Alias exists and contains chown
        if ! grep -q "Cmnd_Alias OPENACE_UTILS" "$sudoers_file" 2>/dev/null || \
           ! grep -E "Cmnd_Alias OPENACE_UTILS.*chown" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers missing OPENACE_UTILS Cmnd_Alias or chown command"
            need_update=true
        fi

        # Check secure_path
        if ! grep -q "secure_path.*usr/local/bin" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers missing secure_path configuration"
            need_update=true
        fi

        # 【修复 Issue #1395 (PR #1467 评论)】Check run-as wrapper rule.
        # The wrapper is installed just before configure_sudoers, but the
        # incremental update above only rewrites the file when an existing
        # check trips. A pre-#1467 sudoers file already has webui / utils /
        # secure_path, so without this check the wrapper rule never gets
        # added on upgrade — stock deployments stayed broken (the agent
        # launch failed with "sudo: a password is required").
        # Match user+path on the same line (like the webui/fetch checks above):
        # a file-global grep would false-pass when another user already has a
        # wrapper rule (other_user_rules are preserved), skipping the current
        # user's authorization.
        local wrapper_path="/usr/local/bin/openace-run-as"
        if [ -x "$wrapper_path" ] && \
           ! grep -E "^${run_user} .*(NOPASSWD: )?${wrapper_path} --isolated( |\*|$)" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers missing run-as wrapper rule for user '$run_user' (wrapper installed but not authorized)"
            need_update=true
        fi

        # 【修复 Issue #1916】Check write-as wrapper rule.
        # Mirrors the run-as probe above: the wrapper is installed before
        # configure_sudoers, but incremental upgrades skip the rewrite when
        # existing checks pass, leaving the new wrapper unauthorized. Match
        # user+path on the same line so a foreign user's rule doesn't false-pass.
        local write_as_wrapper_path="/usr/local/bin/openace-write-as"
        if [ -x "$write_as_wrapper_path" ] && \
           ! grep -E "^${run_user} .*(NOPASSWD: )?${write_as_wrapper_path}( |\*|$)" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers missing write-as wrapper rule for user '$run_user' (wrapper installed but not authorized)"
            need_update=true
        fi

        # 【修复 Issue #1395】Check OPENACE_CLI Cmnd_Alias completeness.
        # The CLI list grew over time (qwen-code/codex/qwen → +claude/openclaw/
        # zcode). Without this probe a pre-existing sudoers file whose other
        # checks all pass would skip the rewrite, leaving the stale short CLI
        # list in place — claude/openclaw/zcode would lack direct-sudo fallback
        # authorization. Check the Cmnd_Alias definition line for each newer
        # CLI path; any missing one trips a rewrite.
        local cli_alias_line=""
        cli_alias_line=$(grep "Cmnd_Alias OPENACE_CLI" "$sudoers_file" 2>/dev/null || true)
        if [ -n "$cli_alias_line" ]; then
            for cli in claude openclaw zcode; do
                if ! echo "$cli_alias_line" | grep -qE "/${cli} \*"; then
                    print_warning "Sudoers OPENACE_CLI missing '$cli' (CLI list incomplete)"
                    need_update=true
                    break
                fi
            done
        fi

        # 【修复 Issue #1522】Check env_keep contains GH_TOKEN and GIT_* vars.
        # The env_keep list grew over time (Issue #1517 added GH_TOKEN/GIT_* vars).
        # Without this check, incremental upgrades skip the rewrite when other
        # checks pass, leaving GH_TOKEN missing and breaking autonomous dev
        # workflows (gh issue view fails with "exit 4: populate the GH_TOKEN").
        if ! grep -q "GH_TOKEN" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers env_keep missing GH_TOKEN (autonomous dev will fail)"
            need_update=true
        fi

        if ! grep -q "GIT_AUTHOR_NAME" "$sudoers_file" 2>/dev/null; then
            print_warning "Sudoers env_keep missing GIT_* vars (git commits may have wrong author)"
            need_update=true
        fi

        # 【新增】Warn about sudoers vs systemd service user mismatch
        if command -v systemctl &>/dev/null && systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
            local svc_file=$(systemctl show open-ace.service -p FragmentPath 2>/dev/null | cut -d= -f2)
            if [ -z "$svc_file" ] || [ ! -f "$svc_file" ]; then
                svc_file="/etc/systemd/system/open-ace.service"
            fi
            local service_user=$(grep "^User=" "$svc_file" 2>/dev/null | cut -d= -f2)
            if [ -z "$service_user" ]; then
                service_user="root"
            fi
            if [ -n "$service_user" ] && [ "$service_user" != "$run_user" ]; then
                print_warning "Sudoers user '$run_user' does not match systemd service user '$service_user'"
                print_info "This may cause WebUI startup issues in multi-user workspace mode"
            fi
        fi

        if [ "$need_update" = false ]; then
            # All rules complete, no need to update
            print_success "Sudoers rules already exist with correct paths and secure_path"
            return 0
        fi

        print_info "Updating sudoers configuration for user '$run_user'..."
    fi

    # Combine final content: header + defaults + cmnd_alias + other users' rules + current user's rules
    # 【修复 Issue #1262】添加 cmnd_alias_section，确保 Cmnd_Alias 在用户规则之前定义
    local sudoers_content="${header}

${defaults_section}

${cmnd_alias_section}"

    if [ -n "$other_user_rules" ]; then
        sudoers_content="${sudoers_content}

${other_user_rules}"
    fi

    sudoers_content="${sudoers_content}

${current_user_rules}
"

    # Back up the existing sudoers file before overwriting so a visudo
    # failure restores the last-known-good state rather than deleting the
    # file and leaving the service with no sudoers at all (which took the
    # 159 deployment down — agent launches got "a password is required").
    local sudoers_backup=""
    if [ -f "$sudoers_file" ]; then
        sudoers_backup="${sudoers_file}.bak.$(date +%s)"
        if cp -p "$sudoers_file" "$sudoers_backup" 2>/dev/null; then
            print_info "Backed up existing sudoers to $sudoers_backup"
        else
            sudoers_backup=""  # couldn't back up; fall through to rm on failure
        fi
    fi

    # Write sudoers file
    echo "$sudoers_content" > "$sudoers_file"
    chmod 440 "$sudoers_file"

    # Validate sudoers syntax
    if visudo -c -f "$sudoers_file" &>/dev/null; then
        print_success "Sudoers configured successfully: $sudoers_file"
        print_info "Service account '$run_user' can execute:"
        print_info "  sudo -u <username> $webui_path --port <port>"
        print_info "  sudo python3 <fetch_script> --multi-user --config <config_path> (for multi-user data collection)"
    else
        print_error "Sudoers syntax error, rolling back..."
        # Restore the pre-write backup if we have one (keeps the service
        # functional on a botched rewrite). Only rm if there was no prior
        # file (fresh install where a bad file is worse than none).
        if [ -n "$sudoers_backup" ] && [ -f "$sudoers_backup" ]; then
            cp -p "$sudoers_backup" "$sudoers_file"
            chmod 440 "$sudoers_file"
            print_warning "Restored previous sudoers from $sudoers_backup (service keeps running)"
        else
            rm -f "$sudoers_file"
        fi
        return 1
    fi

    # Install system-level Python dependencies for fetch scripts
    # Fetch scripts run as root via sudo, so they need dependencies in system Python
    print_info "Installing system-level Python dependencies for fetch scripts..."
    local fetch_deps="requests websockets"
    if command -v pip3 &>/dev/null; then
        pip3 install $fetch_deps 2>/dev/null && print_success "System dependencies installed: $fetch_deps" || \
            print_warning "Failed to install some system dependencies, fetch scripts may need manual setup"
    elif command -v pip &>/dev/null; then
        pip install $fetch_deps 2>/dev/null && print_success "System dependencies installed: $fetch_deps" || \
            print_warning "Failed to install some system dependencies, fetch scripts may need manual setup"
    else
        print_warning "pip not found, skipping system dependencies installation"
        print_info "Fetch scripts may need: pip install requests websockets"
    fi

    return 0
}

# ============================================================================
# Systemd Service Functions
# ============================================================================

get_web_port_from_config() {
    local config_file="$HOME/.open-ace/config.json"
    local default_port="19888"

    if [ -f "$config_file" ]; then
        # Try to extract port from config.json
        local port=$(grep -o '"web_port"[[:space:]]*:[[:space:]]*[0-9]*' "$config_file" 2>/dev/null | grep -o '[0-9]*$')
        if [ -n "$port" ]; then
            echo "$port"
            return
        fi
    fi

    echo "$default_port"
}

# Run pip as a specific user (to avoid root warnings)
run_pip_as_user() {
    local user="$1"
    shift
    local pip_cmd="$@"

    if [ "$EUID" -eq 0 ] && [ -n "$user" ] && [ "$user" != "root" ]; then
        # Running as root but user specified - run pip as that user
        su - "$user" -c "$pip_cmd"
    else
        # Run pip directly
        $pip_cmd
    fi
}

install_systemd_service() {
    local target_path="$1"
    local user="$2"
    local port="${3:-19888}"
    local host="${4:-0.0.0.0}"

    local service_template="$SOURCE_DIR/scripts/open-ace.service"
    local service_file="/etc/systemd/system/open-ace.service"

    if [ ! -f "$service_template" ]; then
        print_error "Service template not found: $service_template"
        return 1
    fi

    # Check if systemd is available
    if ! command -v systemctl &>/dev/null; then
        print_warning "systemctl not found. Skipping systemd service installation."
        print_info "You can manually run the web server with: cd $target_path && python3 server.py"
        return 0
    fi

    # Check if running as root or with sudo
    if [ "$EUID" -ne 0 ]; then
        print_warning "Root privileges required to install systemd service."
        print_info "Please run: sudo $0 --config $CONFIG_FILE"
        print_info "Or manually install the service after installation."
        return 1
    fi

    # Get user's primary group
    local group=$(id -gn "$user")

    # Get user's HOME directory dynamically (handles non-standard paths like /var/lib/username)
    local home_dir=$(getent passwd "$user" | cut -d: -f6)
    if [ -z "$home_dir" ]; then
        # Fallback to standard path if getent fails
        home_dir="/home/$user"
        print_warning "Could not determine HOME directory for $user, using fallback: $home_dir"
    fi
    print_info "User HOME directory: $home_dir"

    # Detect Python path (use the same Python that runs the install script)
    local python_path=""
    if command -v python3 &>/dev/null; then
        python_path=$(which python3)
    elif command -v python &>/dev/null; then
        python_path=$(which python)
    else
        print_error "Python not found"
        return 1
    fi
    print_info "Using Python: $python_path"

    # Generate SECRET_KEY for Flask session and API key encryption
    local secret_key="${SECRET_KEY:-$(openssl rand -hex 32)}"
    print_info "Generated SECRET_KEY for Flask encryption"

    # Create service file from template
    print_info "Creating systemd service file..."
    sed -e "s|__USER__|$user|g" \
        -e "s|__GROUP__|$group|g" \
        -e "s|__INSTALL_PATH__|$target_path|g" \
        -e "s|__PORT__|$port|g" \
        -e "s|__HOST__|$host|g" \
        -e "s|__PYTHON__|$python_path|g" \
        -e "s|__HOME__|$home_dir|g" \
        -e "s|__SECRET_KEY__|$secret_key|g" \
        -e "s|__WORKSPACE_BASE_DIR__|$WORKSPACE_BASE_DIR|g" \
        "$service_template" > "$service_file"

    if [ $? -ne 0 ]; then
        print_error "Failed to create service file"
        return 1
    fi

    # If multi-user workspace mode is enabled, allow sudo (set NoNewPrivileges=false)
    # This is needed for the service to run qwen-code-webui as other users via sudo
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        print_info "Multi-user workspace enabled, allowing sudo in service..."
        sed -i 's/NoNewPrivileges=true/NoNewPrivileges=false/' "$service_file"
    fi

    # Reload systemd daemon
    print_info "Reloading systemd daemon..."
    systemctl daemon-reload

    # Enable the service
    print_info "Enabling open-ace service..."
    systemctl enable open-ace.service

    # Check if service is already running
    if systemctl is-active --quiet open-ace.service; then
        print_info "Restarting open-ace service..."
        systemctl restart open-ace.service
    else
        print_info "Starting open-ace service..."
        systemctl start open-ace.service
    fi

    # Check service status
    sleep 2
    if systemctl is-active --quiet open-ace.service; then
        print_success "Systemd service installed and started successfully"
        print_info "Service name: open-ace"
        print_info "Status: systemctl status open-ace"
        print_info "Logs: journalctl -u open-ace -f"
        print_info "Web interface: http://localhost:$port"
    else
        print_error "Service failed to start"
        print_info "Showing last 20 lines of logs:"
        journalctl -u open-ace -n 20 --no-pager
        print_info "For full logs: journalctl -u open-ace -n 50"
        return 1
    fi

    return 0
}

install_systemd_service_remote() {
    local remote="$1"
    local target_path="$2"
    local user="$3"
    local port="${4:-19888}"
    local host="${5:-0.0.0.0}"

    local service_template="$SOURCE_DIR/scripts/open-ace.service"

    # Check if template exists
    if [ ! -f "$service_template" ]; then
        print_error "Service template not found: $service_template"
        return 1
    fi

    # Check if systemd is available on remote
    if ! ssh "$remote" "command -v systemctl &>/dev/null"; then
        print_warning "systemctl not found on remote machine. Skipping systemd service installation."
        print_info "You can manually run the web server with: ssh $remote 'cd $target_path && python3 server.py'"
        return 0
    fi

    # Get user's primary group
    local group=$(id -gn "$user")

    # Get user's HOME directory dynamically from remote system
    print_info "Detecting HOME directory on remote system..."
    local home_dir=$(ssh "$remote" "getent passwd '$user' | cut -d: -f6")
    if [ -z "$home_dir" ]; then
        # Fallback to standard path if getent fails
        home_dir="/home/$user"
        print_warning "Could not determine HOME directory for $user on remote, using fallback: $home_dir"
    fi
    print_info "User HOME directory on remote: $home_dir"

    # Detect Python path on remote system
    print_info "Detecting Python on remote system..."
    local python_path=$(ssh "$remote" "which python3 || which python")
    if [ -z "$python_path" ]; then
        print_error "Python not found on remote system"
        return 1
    fi
    print_info "Using Python on remote: $python_path"

    # Generate SECRET_KEY for Flask session and API key encryption
    local secret_key="${SECRET_KEY:-$(openssl rand -hex 32)}"
    print_info "Generated SECRET_KEY for Flask encryption"

    # Generate service file content locally using sed
    local service_content=$(sed -e "s|__USER__|$user|g" \
        -e "s|__GROUP__|$group|g" \
        -e "s|__INSTALL_PATH__|$target_path|g" \
        -e "s|__PORT__|$port|g" \
        -e "s|__HOST__|$host|g" \
        -e "s|__PYTHON__|$python_path|g" \
        -e "s|__HOME__|$home_dir|g" \
        -e "s|__SECRET_KEY__|$secret_key|g" \
        -e "s|__WORKSPACE_BASE_DIR__|$WORKSPACE_BASE_DIR|g" \
        "$service_template")

    # If multi-user workspace mode is enabled, allow sudo (set NoNewPrivileges=false)
    # This is needed for the service to run qwen-code-webui as other users via sudo
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        print_info "Multi-user workspace enabled, allowing sudo in service..."
        service_content=$(echo "$service_content" | sed 's/NoNewPrivileges=true/NoNewPrivileges=false/')
    fi

    # Create service file on remote
    print_info "Creating systemd service on remote machine..."

    # Check sudo access and create service file
    local ssh_result=$(ssh "$remote" "
        # Check if we have sudo access
        if ! sudo -n true 2>/dev/null; then
            echo 'SUDO_REQUIRED'
            exit 0
        fi
    ")

    if [ "$ssh_result" = "SUDO_REQUIRED" ]; then
        print_warning "Remote requires sudo password. Skipping systemd service installation."
        print_info "Please run: ssh $remote 'sudo $0 --config <config-file>'"
        return 1
    fi

    # Pipe service content directly to remote
    print_info "Copying service file to remote..."
    echo "$service_content" | ssh "$remote" "sudo tee /etc/systemd/system/open-ace.service > /dev/null"

    # Reload, enable and start service
    local result=$(ssh "$remote" "
        sudo systemctl daemon-reload
        sudo systemctl enable open-ace.service

        # Start or restart service
        if sudo systemctl is-active --quiet open-ace.service; then
            sudo systemctl restart open-ace.service
        else
            sudo systemctl start open-ace.service
        fi

        # Wait and check status
        sleep 2
        if sudo systemctl is-active --quiet open-ace.service; then
            echo 'SERVICE_STARTED'
        else
            echo 'SERVICE_FAILED'
        fi
    ")

    case "$result" in
        SERVICE_STARTED)
            print_success "Systemd service installed and started on remote machine"
            print_info "Service name: open-ace"
            print_info "Status: ssh $remote 'sudo systemctl status open-ace'"
            print_info "Logs: ssh $remote 'sudo journalctl -u open-ace -f'"
            print_info "Web interface: http://$remote:$port"
            ;;
        SERVICE_FAILED)
            print_error "Service failed to start on remote machine"
            print_info "Check logs with: ssh $remote 'sudo journalctl -u open-ace -n 50'"
            return 1
            ;;
    esac

    return 0
}

# ============================================================================
# Config File Parsing
# ============================================================================

parse_config_file() {
    if [ ! -f "$CONFIG_FILE" ]; then
        print_error "Config file not found: $CONFIG_FILE"
        exit 1
    fi

    print_info "Reading configuration from: $CONFIG_FILE"

    # Source the config file (it should be a shell script with variable assignments)
    source "$CONFIG_FILE"

    # Validate source directory
    validate_source_dir

    # Determine install mode based on DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        INSTALL_MODE="local"
    else
        INSTALL_MODE="deploy"
    fi

    # Set default service port if not specified
    if [ -z "$SERVICE_PORT" ]; then
        SERVICE_PORT=$(get_web_port_from_config)
    fi
}

# ============================================================================
# Interactive Configuration
# ============================================================================

# Verify systemd service configuration consistency before upgrade
# Called in interactive_config() after user confirms upgrade, before do_upgrade()
# Sets UPGRADE_SWITCH_SERVICE global variable
verify_upgrade_systemd_config() {
    # Only check in systemd environments
    if ! command -v systemctl &>/dev/null; then
        return 0
    fi

    if ! systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
        return 0
    fi

    # Get actual service file path (not hardcoded)
    local service_file=$(systemctl show open-ace.service -p FragmentPath 2>/dev/null | cut -d= -f2)
    if [ -z "$service_file" ] || [ ! -f "$service_file" ]; then
        service_file="/etc/systemd/system/open-ace.service"
    fi

    local current_path=$(grep "^WorkingDirectory=" "$service_file" 2>/dev/null | cut -d= -f2)
    # User= not present defaults to root
    local current_user=$(grep "^User=" "$service_file" 2>/dev/null | cut -d= -f2)
    if [ -z "$current_user" ]; then
        current_user="root"
    fi

    local need_update=false
    UPGRADE_SWITCH_SERVICE="no"

    # Check path mismatch
    if [ -n "$current_path" ] && [ "$current_path" != "$DEPLOY_PATH" ]; then
        print_warning "Systemd service path mismatch:"
        print_info "  Current systemd WorkingDirectory: $current_path"
        print_info "  Upgrade target path: $DEPLOY_PATH"
        need_update=true
    fi

    # Check user mismatch
    if [ "$current_user" != "$DEPLOY_USER" ]; then
        print_warning "Systemd service user mismatch:"
        print_info "  Current systemd User: $current_user"
        print_info "  Upgrade target user: $DEPLOY_USER"
        need_update=true
    fi

    if [ "$need_update" = true ]; then
        print_warning "Updating systemd service will restart the running service."
        prompt_yesno "Do you want to switch the default service to this installation?" "n" UPGRADE_SWITCH_SERVICE
        if [ "$UPGRADE_SWITCH_SERVICE" != "yes" ]; then
            print_warning "Service configuration will NOT be updated."
            print_warning "The systemd service will continue running from: $current_path (user: $current_user)"
            print_warning "After upgrade, code files at $DEPLOY_PATH will be updated but NOT served by systemd."
            print_info "To manually switch later: edit $service_file and restart the service."
            echo ""
            prompt_yesno "Continue upgrade anyway?" "n" continue_anyway
            if [ "$continue_anyway" != "yes" ]; then
                print_info "Upgrade cancelled."
                exit 0
            fi
        fi
    fi
}

# Detect existing local installation and load config from it
# Returns 0 if upgrade detected (config loaded), 1 if fresh install
detect_and_load_local_upgrade() {
    local candidate_paths=()

    # Priority 1: From systemd service file (highest priority - matches running service)
    # Use systemctl show to get actual service file path (not hardcoded)
    if command -v systemctl &>/dev/null && systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
        local service_file=$(systemctl show open-ace.service -p FragmentPath 2>/dev/null | cut -d= -f2)
        if [ -z "$service_file" ] || [ ! -f "$service_file" ]; then
            service_file="/etc/systemd/system/open-ace.service"
        fi

        # Use "^WorkingDirectory=" to avoid matching comment lines
        local systemd_path=$(grep "^WorkingDirectory=" "$service_file" 2>/dev/null | cut -d= -f2)
        if [ -n "$systemd_path" ] && [ -d "$systemd_path" ] && [ -f "$systemd_path/server.py" ]; then
            print_info "Found running systemd service pointing to: $systemd_path"
            if [[ ! " ${candidate_paths[*]} " =~ " ${systemd_path} " ]]; then
                candidate_paths+=("$systemd_path")
            fi
        fi
    fi

    # Priority 2: Scan for users with .open-ace/config.json (most reliable indicator)
    # This finds existing installations even if they're in non-standard locations
    local config_based_paths=""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS: check /Users/*/.open-ace/config.json
        for user_home in /Users/*; do
            if [ -d "$user_home/.open-ace" ] && [ -f "$user_home/.open-ace/config.json" ]; then
                local user_name=$(basename "$user_home")
                # Check if there's an installation in this user's home
                if [ -d "$user_home" ] && [ -f "$user_home/server.py" ]; then
                    config_based_paths="$user_home"
                    break
                fi
                # Also check for open-ace subdirectory
                if [ -d "$user_home/open-ace" ] && [ -f "$user_home/open-ace/server.py" ]; then
                    config_based_paths="$user_home/open-ace"
                    break
                fi
            fi
        done
    else
        # Linux: check /home/*/.open-ace/config.json
        for user_home in /home/*; do
            if [ -d "$user_home/.open-ace" ] && [ -f "$user_home/.open-ace/config.json" ]; then
                local user_name=$(basename "$user_home")
                # Check if there's an installation in this user's home
                if [ -d "$user_home" ] && [ -f "$user_home/server.py" ]; then
                    config_based_paths="$user_home"
                    break
                fi
                # Also check for open-ace subdirectory
                if [ -d "$user_home/open-ace" ] && [ -f "$user_home/open-ace/server.py" ]; then
                    config_based_paths="$user_home/open-ace"
                    break
                fi
            fi
        done
        # Also check root's config if running as root
        if [ -z "$config_based_paths" ] && [ -f "/root/.open-ace/config.json" ]; then
            if [ -d "/root" ] && [ -f "/root/server.py" ]; then
                config_based_paths="/root"
            elif [ -d "/root/open-ace" ] && [ -f "/root/open-ace/server.py" ]; then
                config_based_paths="/root/open-ace"
            fi
        fi
    fi

    # Add config-based path (avoid duplicate)
    if [ -n "$config_based_paths" ]; then
        if [[ ! " ${candidate_paths[*]} " =~ " ${config_based_paths} " ]]; then
            candidate_paths+=("$config_based_paths")
        fi
    fi

    # Priority 3: Check openace user's home (if exists)
    local openace_home
    if [[ "$OSTYPE" == "darwin"* ]]; then
        openace_home="/Users/openace"
    else
        openace_home="/home/openace"
    fi
    if check_openace_user_exists && [ -d "$openace_home" ]; then
        if [[ ! " ${candidate_paths[*]} " =~ " ${openace_home} " ]]; then
            candidate_paths+=("$openace_home")
        fi
    fi

    # Priority 4: Check current user's open-ace directory
    if [[ ! " ${candidate_paths[*]} " =~ " ${HOME}/open-ace " ]]; then
        candidate_paths+=("$HOME/open-ace")
    fi

    # Priority 5: Check root's open-ace (if running as root and has config)
    if [ "$EUID" -eq 0 ] && [ -d "/root/open-ace" ] && [ -f "/root/open-ace/server.py" ]; then
        if [[ ! " ${candidate_paths[*]} " =~ " /root/open-ace " ]]; then
            candidate_paths+=("/root/open-ace")
        fi
    fi

    # Refactored traversal: filter valid paths first, then select
    # This avoids the for-loop return-0 bypassing multi-install selection
    local valid_paths=()
    for path in "${candidate_paths[@]}"; do
        if [ -d "$path" ] && [ -f "$path/server.py" ]; then
            valid_paths+=("$path")
        fi
    done

    if [ ${#valid_paths[@]} -eq 0 ]; then
        # No valid installation found
        return 1
    fi

    # If multiple valid installations, let user select (use prompt_input for /dev/tty compatibility)
    local target_path=""
    if [ ${#valid_paths[@]} -gt 1 ]; then
        print_warning "Multiple installations detected:"
        for i in "${!valid_paths[@]}"; do
            local path_owner=""
            if [[ "$OSTYPE" == "darwin"* ]]; then
                path_owner=$(stat -f "%Su" "${valid_paths[$i]}" 2>/dev/null || echo "?")
            else
                path_owner=$(stat -c "%U" "${valid_paths[$i]}" 2>/dev/null || echo "?")
            fi
            print_info "  [$i] ${valid_paths[$i]} (owner: $path_owner)"
        done
        prompt_input "Select installation to upgrade [0-$(( ${#valid_paths[@]} - 1 ))]" "0" selection
        if [ -n "$selection" ] && [ "$selection" -ge 0 ] && [ "$selection" -lt ${#valid_paths[@]} ]; then
            target_path="${valid_paths[$selection]}"
        else
            target_path="${valid_paths[0]}"
        fi
    else
        target_path="${valid_paths[0]}"
    fi

    print_info "Existing installation found at: $target_path"

    # Get owner of the directory
    local detected_owner=""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        detected_owner=$(stat -f "%Su" "$target_path" 2>/dev/null || echo "")
    else
        detected_owner=$(stat -c "%U" "$target_path" 2>/dev/null || echo "")
    fi

    # Handle UNKNOWN or invalid user
    if [ -z "$detected_owner" ] || [ "$detected_owner" = "UNKNOWN" ] || ! user_exists "$detected_owner"; then
        print_warning "Could not determine valid owner for: $target_path"
        print_info "Detected owner: '$detected_owner' (not a valid user)"
        print_info "Using current user: $USER"
        DEPLOY_USER="$USER"
    else
        DEPLOY_USER="$detected_owner"
    fi

    DEPLOY_PATH="$target_path"
    INSTALL_MODE="local"
    INSTALL_SERVICE="no"

    # Read port from existing config
    local config_file
    if [ "$DEPLOY_USER" = "root" ]; then
        config_file="/root/.open-ace/config.json"
    elif [ "$DEPLOY_USER" = "${USER}" ] && [ "$EUID" -ne 0 ]; then
        config_file="$HOME/.open-ace/config.json"
    else
        if [[ "$OSTYPE" == "darwin"* ]]; then
            config_file="/Users/$DEPLOY_USER/.open-ace/config.json"
        else
            config_file="/home/$DEPLOY_USER/.open-ace/config.json"
        fi
    fi

    if [ -f "$config_file" ]; then
        local port=$(grep -o '"web_port"[[:space:]]*:[[:space:]]*[0-9]*' "$config_file" 2>/dev/null | grep -o '[0-9]*$')
        if [ -n "$port" ]; then
            SERVICE_PORT="$port"
        fi
        # Preserve config path for database configuration reuse
        EXISTING_CONFIG_PATH="$config_file"

        # Read WORKSPACE_ENABLED from existing config (upgrade should respect original setting)
        # Python prints True/False (capitalized), but shell expects true/false (lowercase)
        local enabled=$(python3 -c "import json; c=json.load(open('$config_file')); print(c.get('workspace', {}).get('enabled', 'true'))" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        if [ -n "$enabled" ]; then
            WORKSPACE_ENABLED="$enabled"
            print_info "Read WORKSPACE_ENABLED=$WORKSPACE_ENABLED from existing config"
        fi

        # Read WORKSPACE_MULTI_USER_MODE from existing config (upgrade should respect original setting)
        # Python prints True/False (capitalized), but shell expects true/false (lowercase)
        local multi_user=$(python3 -c "import json; c=json.load(open('$config_file')); print(c.get('workspace', {}).get('multi_user_mode', 'true'))" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        if [ -n "$multi_user" ]; then
            WORKSPACE_MULTI_USER_MODE="$multi_user"
            print_info "Read WORKSPACE_MULTI_USER_MODE=$WORKSPACE_MULTI_USER_MODE from existing config"
        fi
    fi

    return 0
}

interactive_config() {
    print_header "Open ACE - Installation Configuration"

    # Ask for installation mode first (needed to know where to look)
    echo -e "${BLUE}Select installation mode:${NC}"
    echo "  1) Local (install on this machine)"
    echo "  2) Deploy (deploy to a remote machine via SSH)"
    echo ""
    prompt_input "Enter choice" "1" mode_choice

    case $mode_choice in
        1)
            INSTALL_MODE="local"
            # Initialize deployment user based on openace user availability
            init_deploy_user "true"

            # Check for existing local installation
            if detect_and_load_local_upgrade; then
                echo ""
                print_warning "Existing installation detected!"
                print_info "  User: $DEPLOY_USER"
                print_info "  Path: $DEPLOY_PATH"
                if [ -n "$SERVICE_PORT" ]; then
                    print_info "  Port: $SERVICE_PORT"
                fi
                echo ""
                prompt_yesno "Upgrade existing installation?" "y" DO_UPGRADE
                if [ "$DO_UPGRADE" = "yes" ]; then
                    # Verify systemd configuration consistency before upgrade
                    # (before do_upgrade() to allow user to cancel before irreversible changes)
                    verify_upgrade_systemd_config
                    # Skip all parameter prompts, go straight to upgrade
                    return 0
                else
                    # User doesn't want to upgrade, fall through to normal config
                    print_info "Proceeding with fresh installation configuration..."
                    DEPLOY_USER=""
                    DEPLOY_PATH=""
                    init_deploy_user "true"
                fi
            fi

            configure_local
            ;;
        2)
            INSTALL_MODE="deploy"
            # Initialize deployment user (remote will be checked later)
            init_deploy_user "false"

            # For remote deploy, we need host info first to check for existing install
            echo ""
            echo -e "${YELLOW}Configuring remote deployment...${NC}"
            echo ""
            prompt_input "Remote host IP" "" DEPLOY_HOST
            if [ -z "$DEPLOY_HOST" ]; then
                print_error "Remote host is required"
                exit 1
            fi
            prompt_input "Remote user" "$DEPLOY_USER" DEPLOY_USER
            prompt_deploy_path "Deployment path" "/home/$DEPLOY_USER/open-ace"

            # Safety check: warn if deploying to home directory
            if ! check_deploy_path_safety; then
                print_error "Deployment cancelled"
                exit 1
            fi

            # Check for existing remote installation
            local remote="$DEPLOY_USER@$DEPLOY_HOST"
            if ssh -o ConnectTimeout=5 "$remote" "[ -d '$DEPLOY_PATH' ] && [ -f '$DEPLOY_PATH/server.py' ]" 2>/dev/null; then
                print_warning "Existing installation found at: $DEPLOY_HOST:$DEPLOY_PATH"
                prompt_yesno "Upgrade existing installation?" "y" DO_UPGRADE
                if [ "$DO_UPGRADE" = "yes" ]; then
                    # Skip remaining parameter prompts, go straight to upgrade
                    INSTALL_SERVICE="no"
                    DO_UPGRADE="yes"
                    return 0
                else
                    print_info "Proceeding with fresh installation configuration..."
                fi
            fi

            # Continue with remaining deploy config (service, workspace, etc.)
            configure_deploy_remaining
            ;;
        *)
            print_error "Invalid choice"
            exit 1
            ;;
    esac
}

configure_local() {
    echo ""
    echo -e "${YELLOW}Configuring deployment...${NC}"
    echo ""

    # Store original user for comparison
    local original_user="$DEPLOY_USER"
    local original_path="$DEPLOY_PATH"

    prompt_input "Deployment user" "$DEPLOY_USER" DEPLOY_USER

    # If user was changed, suggest updating the path
    if [ -n "$original_user" ] && [ "$DEPLOY_USER" != "$original_user" ]; then
        local user_home
        if [ "$DEPLOY_USER" = "root" ]; then
            user_home="/root"
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            user_home="/Users/$DEPLOY_USER"
        else
            user_home="/home/$DEPLOY_USER"
        fi

        # Check if current path matches the old user's home
        if [[ "$original_path" == *"$original_user"* ]] && [[ "$original_path" != *"$DEPLOY_USER"* ]]; then
            # Path contains old username but not new username - suggest update
            local suggested_path="$user_home/open-ace"
            print_info "User changed from '$original_user' to '$DEPLOY_USER'"
            print_info "Current path ($original_path) does not match new user's home ($user_home)"
            prompt_deploy_path "Deployment path" "$suggested_path"
        else
            # Path might still be valid, just confirm it
            prompt_deploy_path "Deployment path" "$original_path"
        fi
    else
        prompt_deploy_path "Deployment path" "$DEPLOY_PATH"
    fi

    # Remove trailing slash from path to avoid double slashes
    DEPLOY_PATH="${DEPLOY_PATH%/}"

    # Safety check: warn if deploying to home directory
    if ! check_deploy_path_safety; then
        print_error "Deployment cancelled"
        exit 1
    fi

    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        # Get default port from config or use 19888
        local default_port=$(get_web_port_from_config)
        prompt_input "Web server port" "$default_port" SERVICE_PORT
        prompt_input "Web server host" "$SERVICE_HOST" SERVICE_HOST
    fi

    # Ask about multi-user workspace mode
    echo ""
    echo -e "${BLUE}=== Workspace Multi-user Mode Configuration ===${NC}"
    echo -e "${YELLOW}Multi-user mode starts a separate qwen-code-webui process for each user${NC}"
    prompt_yesno "Enable multi-user mode?" "y" enable_multi_user
    if [ "$enable_multi_user" = "yes" ]; then
        WORKSPACE_MULTI_USER_MODE="true"
        prompt_input "Port pool start" "$WORKSPACE_PORT_RANGE_START" WORKSPACE_PORT_RANGE_START
        prompt_input "Port pool end" "$WORKSPACE_PORT_RANGE_END" WORKSPACE_PORT_RANGE_END
        prompt_input "Max instances" "$WORKSPACE_MAX_INSTANCES" WORKSPACE_MAX_INSTANCES
        prompt_input "Idle timeout (minutes)" "$WORKSPACE_IDLE_TIMEOUT" WORKSPACE_IDLE_TIMEOUT
    fi

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Local (this machine)"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    if [ "$install_service" = "yes" ]; then
        echo "  Systemd service: Yes"
        echo "  Web port: $SERVICE_PORT"
        echo "  Web host: $SERVICE_HOST"
    else
        echo "  Systemd service: No"
    fi
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "  Multi-user workspace: Enabled"
        echo "    - Port range: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
        echo "    - Max instances: $WORKSPACE_MAX_INSTANCES"
        echo "    - Idle timeout: $WORKSPACE_IDLE_TIMEOUT min"
    fi
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi

    # Store service installation preference
    INSTALL_SERVICE="$install_service"
}

configure_deploy() {
    echo ""
    echo -e "${YELLOW}Configuring remote deployment...${NC}"
    echo ""

    prompt_input "Remote host IP" "" DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        print_error "Remote host is required"
        exit 1
    fi

    prompt_input "Remote user" "$DEPLOY_USER" DEPLOY_USER
    prompt_deploy_path "Deployment path" "/home/$DEPLOY_USER/open-ace"

    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service on remote?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        prompt_input "Web server port" "19888" SERVICE_PORT
        prompt_input "Web server host" "$SERVICE_HOST" SERVICE_HOST
    fi

    # Ask about multi-user workspace mode
    echo ""
    echo -e "${BLUE}=== Workspace Multi-user Mode Configuration ===${NC}"
    echo -e "${YELLOW}Multi-user mode starts a separate qwen-code-webui process for each user${NC}"
    prompt_yesno "Enable multi-user mode?" "y" enable_multi_user
    if [ "$enable_multi_user" = "yes" ]; then
        WORKSPACE_MULTI_USER_MODE="true"
        prompt_input "Port pool start" "$WORKSPACE_PORT_RANGE_START" WORKSPACE_PORT_RANGE_START
        prompt_input "Port pool end" "$WORKSPACE_PORT_RANGE_END" WORKSPACE_PORT_RANGE_END
        prompt_input "Max instances" "$WORKSPACE_MAX_INSTANCES" WORKSPACE_MAX_INSTANCES
        prompt_input "Idle timeout (minutes)" "$WORKSPACE_IDLE_TIMEOUT" WORKSPACE_IDLE_TIMEOUT
    fi

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Deploy (via SSH)"
    echo "  Host: $DEPLOY_HOST"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    if [ "$install_service" = "yes" ]; then
        echo "  Systemd service: Yes"
        echo "  Web port: $SERVICE_PORT"
        echo "  Web host: $SERVICE_HOST"
    else
        echo "  Systemd service: No"
    fi
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "  Multi-user workspace: Enabled"
        echo "    - Port range: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
        echo "    - Max instances: $WORKSPACE_MAX_INSTANCES"
        echo "    - Idle timeout: $WORKSPACE_IDLE_TIMEOUT min"
    fi
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi

    # Store service installation preference
    INSTALL_SERVICE="$install_service"
}

# Remaining deploy configuration (after host/user/path are already set)
# Used when upgrade was declined and we need to ask service/workspace questions
configure_deploy_remaining() {
    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service on remote?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        prompt_input "Web server port" "19888" SERVICE_PORT
        prompt_input "Web server host" "$SERVICE_HOST" SERVICE_HOST
    fi

    # Ask about multi-user workspace mode
    echo ""
    echo -e "${BLUE}=== Workspace Multi-user Mode Configuration ===${NC}"
    echo -e "${YELLOW}Multi-user mode starts a separate qwen-code-webui process for each user${NC}"
    prompt_yesno "Enable multi-user mode?" "y" enable_multi_user
    if [ "$enable_multi_user" = "yes" ]; then
        WORKSPACE_MULTI_USER_MODE="true"
        prompt_input "Port pool start" "$WORKSPACE_PORT_RANGE_START" WORKSPACE_PORT_RANGE_START
        prompt_input "Port pool end" "$WORKSPACE_PORT_RANGE_END" WORKSPACE_PORT_RANGE_END
        prompt_input "Max instances" "$WORKSPACE_MAX_INSTANCES" WORKSPACE_MAX_INSTANCES
        prompt_input "Idle timeout (minutes)" "$WORKSPACE_IDLE_TIMEOUT" WORKSPACE_IDLE_TIMEOUT
    fi

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Deploy (via SSH)"
    echo "  Host: $DEPLOY_HOST"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    if [ "$install_service" = "yes" ]; then
        echo "  Systemd service: Yes"
        echo "  Web port: $SERVICE_PORT"
        echo "  Web host: $SERVICE_HOST"
    else
        echo "  Systemd service: No"
    fi
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "  Multi-user workspace: Enabled"
        echo "    - Port range: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
        echo "    - Max instances: $WORKSPACE_MAX_INSTANCES"
        echo "    - Idle timeout: $WORKSPACE_IDLE_TIMEOUT min"
    fi
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi

    # Store service installation preference
    INSTALL_SERVICE="$install_service"
}

# ============================================================================
# Local Installation
# ============================================================================

install_local() {
    print_header "Installing on Local Machine"

    # Check Python version first (Open ACE requires Python >= 3.10)
    check_python_version

    # Validate source directory first
    validate_source_dir

    local target_path="$DEPLOY_PATH"
    # Config directory should be in the install user's home, not current user's home
    local config_dir
    if [ "$DEPLOY_USER" = "root" ]; then
        config_dir="/root/.open-ace"
    elif [ "$DEPLOY_USER" = "${USER}" ] && [ "$EUID" -ne 0 ]; then
        # Running as non-root, and deploying to current user
        config_dir="$HOME/.open-ace"
    else
        # Deploying to a different user (e.g., openace)
        # Determine user's home directory based on OS
        if [[ "$OSTYPE" == "darwin"* ]]; then
            config_dir="/Users/$DEPLOY_USER/.open-ace"
        else
            config_dir="/home/$DEPLOY_USER/.open-ace"
        fi
    fi

    # Setup PostgreSQL (detect or install) - skip for upgrade (DB config already exists)
    if [ "$DO_UPGRADE" != "yes" ]; then
        setup_postgresql
    fi

    # If upgrade was already confirmed in interactive_config, skip re-checking
    if [ "$DO_UPGRADE" = "yes" ]; then
        do_upgrade "$target_path" "$config_dir" "$DEPLOY_USER"
    elif [ -d "$target_path" ] && [ -f "$target_path/server.py" ]; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade

        if [ "$upgrade" = "yes" ]; then
            do_upgrade "$target_path" "$config_dir" "$DEPLOY_USER"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    elif [ -d "$target_path" ]; then
        # Directory exists but no valid installation
        print_warning "Directory exists at: $target_path but no valid installation found"
        print_info "Will perform fresh installation (existing directory contents will be preserved/merged)"
        do_fresh_install "$target_path" "$config_dir" "$DEPLOY_USER"
    else
        do_fresh_install "$target_path" "$config_dir" "$DEPLOY_USER"
    fi

    # Install systemd service if requested
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        print_header "Installing Systemd Service"
        install_systemd_service "$target_path" "$DEPLOY_USER" "$SERVICE_PORT" "$SERVICE_HOST"
    fi

    # For upgrade mode: handle systemd service configuration
    # This ensures new code takes effect after upgrade
    if [ "$DO_UPGRADE" = "yes" ] && command -v systemctl &>/dev/null; then
        if systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
            # Get actual service file path (not hardcoded)
            local service_file=$(systemctl show open-ace.service -p FragmentPath 2>/dev/null | cut -d= -f2)
            if [ -z "$service_file" ] || [ ! -f "$service_file" ]; then
                service_file="/etc/systemd/system/open-ace.service"
            fi

            # If user chose to switch service to this installation, update service config via sed
            # (Preserves SECRET_KEY, avoids double restart, avoids overwriting custom modifications)
            if [ "$UPGRADE_SWITCH_SERVICE" = "yes" ]; then
                print_info "Updating systemd service configuration..."

                local new_group=$(id -gn "$DEPLOY_USER")
                local new_home=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
                if [ -z "$new_home" ]; then
                    new_home="/home/$DEPLOY_USER"
                fi

                # Use sed to directly modify: preserves SECRET_KEY and other custom fields
                sed -i -e "s|^User=.*|User=$DEPLOY_USER|" \
                       -e "s|^Group=.*|Group=$new_group|" \
                       -e "s|^WorkingDirectory=.*|WorkingDirectory=$target_path|" \
                       -e "s|^Environment=HOME=.*|Environment=HOME=$new_home|" \
                       "$service_file"

                # If ExecStart uses old path, update it too
                sed -i "s|ExecStart=.*python3.*server.py|ExecStart=$(which python3) $target_path/server.py|" "$service_file"

                # Sync NoNewPrivileges setting based on WORKSPACE_MULTI_USER_MODE (read from config.json)
                if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
                    sed -i 's/NoNewPrivileges=true/NoNewPrivileges=false/' "$service_file"
                else
                    sed -i 's/NoNewPrivileges=false/NoNewPrivileges=true/' "$service_file"
                fi

                # Update or add WORKSPACE_BASE_DIR (Issue #1217)
                if grep -q "^Environment=WORKSPACE_BASE_DIR=" "$service_file" 2>/dev/null; then
                    sed -i "s|^Environment=WORKSPACE_BASE_DIR=.*|Environment=WORKSPACE_BASE_DIR=$WORKSPACE_BASE_DIR|" "$service_file"
                else
                    sed -i "/^Environment=SECRET_KEY=/a Environment=WORKSPACE_BASE_DIR=$WORKSPACE_BASE_DIR" "$service_file"
                fi

                print_success "Service configuration updated (SECRET_KEY preserved)"
            fi

            # Check if systemd service is missing SECRET_KEY (upgrade from older version)
            local current_secret=$(grep "^Environment=SECRET_KEY=" "$service_file" 2>/dev/null | cut -d'=' -f3)
            if [ -z "$current_secret" ]; then
                print_warning "Adding missing SECRET_KEY to systemd service..."
                local secret_key="${SECRET_KEY:-$(openssl rand -hex 32)}"
                sed -i "/^Environment=HOME=/a Environment=SECRET_KEY=$secret_key" "$service_file"
                print_info "Generated SECRET_KEY for Flask encryption"
            fi

            # Check and fix WORKSPACE_BASE_DIR (Issue #1217, #1308)
            # WORKSPACE_BASE_DIR should always be /home for Package version
            # This ensures user paths are /home/{username} instead of /home/{service_user}/{username}
            local current_workspace_base=$(grep "^Environment=WORKSPACE_BASE_DIR=" "$service_file" 2>/dev/null | cut -d'=' -f3)
            if [ -z "$current_workspace_base" ]; then
                print_warning "Adding missing WORKSPACE_BASE_DIR to systemd service..."
                sed -i "/^Environment=SECRET_KEY=/a Environment=WORKSPACE_BASE_DIR=/home" "$service_file"
                print_info "Set WORKSPACE_BASE_DIR=/home (Issue #1217, #1308)"
            elif [ "$current_workspace_base" != "/home" ]; then
                print_warning "Fixing incorrect WORKSPACE_BASE_DIR (was: $current_workspace_base)..."
                sed -i "s|^Environment=WORKSPACE_BASE_DIR=.*|Environment=WORKSPACE_BASE_DIR=/home|" "$service_file"
                print_info "Fixed WORKSPACE_BASE_DIR=/home (Issue #1308)"
            fi

            print_info "Restarting open-ace service..."
            systemctl daemon-reload
            systemctl restart open-ace.service
            sleep 2
            if systemctl is-active --quiet open-ace.service; then
                print_success "Service restarted successfully"
            else
                print_warning "Service restart failed, check with: systemctl status open-ace"
            fi
            INSTALL_SERVICE="yes"
        fi
    fi

    # Every local autonomous workflow uses the credentialless agent account,
    # even in default single-user mode. Resolve the real service identity and
    # install only the narrow isolated-launch rule unconditionally.
    local autonomous_run_user="$DEPLOY_USER"
    local autonomous_install_dir="$target_path"
    local autonomous_service_file=""
    if command -v systemctl &>/dev/null && systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
        autonomous_service_file=$(systemctl show open-ace.service -p FragmentPath 2>/dev/null | cut -d= -f2)
        [ -n "$autonomous_service_file" ] && [ -f "$autonomous_service_file" ] || autonomous_service_file="/etc/systemd/system/open-ace.service"
        local detected_service_user
        detected_service_user=$(grep "^User=" "$autonomous_service_file" 2>/dev/null | cut -d= -f2)
        [ -z "$detected_service_user" ] || autonomous_run_user="$detected_service_user"
        local detected_service_path
        detected_service_path=$(grep "^WorkingDirectory=" "$autonomous_service_file" 2>/dev/null | cut -d= -f2)
        [ -z "$detected_service_path" ] || autonomous_install_dir="$detected_service_path"
    fi
    if ! configure_autonomous_agent_sudoers "$autonomous_run_user" "$autonomous_install_dir"; then
        print_warning "Credentialless autonomous agent launcher setup failed; local autonomous workflows will fail closed"
    fi
    # The service must be allowed to invoke the single root-owned launcher.
    if [ -n "$autonomous_service_file" ] && [ -f "$autonomous_service_file" ]; then
        if grep -q '^NoNewPrivileges=' "$autonomous_service_file"; then
            sed -i 's/NoNewPrivileges=.*/NoNewPrivileges=false/' "$autonomous_service_file"
        else
            sed -i '/^\[Service\]/a NoNewPrivileges=false' "$autonomous_service_file"
        fi
        systemctl daemon-reload
        systemctl try-restart open-ace.service || \
            print_warning "Restart open-ace manually to activate autonomous agent isolation"
    fi

    # Configure sudoers for multi-user workspace mode
    # sudoers run_user should match the systemd service's actual running user
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        # Stop existing qwen-code-webui systemd service first
        stop_webui_systemd_service

        # Determine the correct sudoers user and install_dir
        # If user declined service switch, sudoers should configure for the systemd service's actual User=
        local sudoers_run_user="$DEPLOY_USER"
        local sudoers_install_dir="$target_path"

        if command -v systemctl &>/dev/null && systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
            local svc_file=$(systemctl show open-ace.service -p FragmentPath 2>/dev/null | cut -d= -f2)
            if [ -z "$svc_file" ] || [ ! -f "$svc_file" ]; then
                svc_file="/etc/systemd/system/open-ace.service"
            fi
            local service_user=$(grep "^User=" "$svc_file" 2>/dev/null | cut -d= -f2)
            if [ -z "$service_user" ]; then
                service_user="root"
            fi

            # If user declined service switch, sudoers must match systemd service user
            if [ "$UPGRADE_SWITCH_SERVICE" != "yes" ] && [ -n "$service_user" ] && [ "$service_user" != "$DEPLOY_USER" ]; then
                print_warning "Configuring sudoers for service user '$service_user' (not upgrade user '$DEPLOY_USER')"
                print_info "This ensures sudoers matches the systemd service configuration"
                sudoers_run_user="$service_user"
                local service_path=$(grep "^WorkingDirectory=" "$svc_file" 2>/dev/null | cut -d= -f2)
                if [ -n "$service_path" ]; then
                    sudoers_install_dir="$service_path"
                fi
            fi
        fi

        # Install the run-as wrapper BEFORE configure_sudoers (Issue #1395):
        # the sudoers rule keys off `[ -x /usr/local/bin/openace-run-as ]`.
        install_run_as_wrapper "$sudoers_install_dir"

        # Install the write-as wrapper BEFORE configure_sudoers (Issue #1916):
        # the sudoers rule keys off `[ -x /usr/local/bin/openace-write-as ]`.
        install_write_as_wrapper "$sudoers_install_dir"

        configure_sudoers "$sudoers_run_user" "$sudoers_install_dir"
        if [ $? -ne 0 ]; then
            print_warning "Sudoers configuration failed, multi-user mode may not work properly"
            print_info "Please manually configure /etc/sudoers.d/open-ace-webui"
        fi
    fi

    print_header "Local Installation Complete!"
    print_info "Installation path: $target_path"
    print_info "Config directory: $config_dir"
    echo ""
    if [ "$INSTALL_SERVICE" = "yes" ] && command -v systemctl &>/dev/null; then
        echo "Service management:"
        echo "  systemctl status open-ace"
        echo "  systemctl start open-ace"
        echo "  systemctl stop open-ace"
        echo "  systemctl restart open-ace"
        echo ""
        echo "View logs:"
        echo "  journalctl -u open-ace -f"
    else
        echo "To start the web server:"
        echo "  cd $target_path && python3 server.py"
    fi
    echo ""
}

# ============================================================================
# Remote Deployment
# ============================================================================

install_deploy() {
    print_header "Deploying to Remote Machine"

    # Validate source directory first
    validate_source_dir

    local remote="$DEPLOY_USER@$DEPLOY_HOST"
    local target_path="$DEPLOY_PATH"

    # Check Python version on remote system (Open ACE requires Python >= 3.10)
    check_python_version_remote "$remote" || exit 1

    # Test SSH connection
    print_info "Testing SSH connection..."
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$remote" "echo 'Connection OK'" 2>/dev/null; then
        print_warning "SSH connection requires password or key setup"
        ssh -o ConnectTimeout=10 "$remote" "echo 'Connection OK'" || {
            print_error "Cannot connect to $remote"
            exit 1
        }
    fi
    print_success "SSH connection OK"

    # If upgrade was already confirmed in interactive_config, skip re-checking
    if [ "$DO_UPGRADE" = "yes" ]; then
        do_upgrade_remote "$remote" "$target_path"
    elif ssh "$remote" "[ -d '$target_path' ] && [ -f '$target_path/server.py' ]"; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade

        if [ "$upgrade" = "yes" ]; then
            do_upgrade_remote "$remote" "$target_path"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    elif ssh "$remote" "[ -d '$target_path' ]"; then
        # Directory exists but no valid installation
        print_warning "Directory exists at: $target_path but no valid installation found"
        print_info "Will perform fresh installation (existing directory contents will be preserved/merged)"
        do_fresh_install_remote "$remote" "$target_path"
    else
        do_fresh_install_remote "$remote" "$target_path"
    fi

    # Install systemd service if requested
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        print_header "Installing Systemd Service on Remote"
        install_systemd_service_remote "$remote" "$target_path" "$DEPLOY_USER" "$SERVICE_PORT" "$SERVICE_HOST"
    fi

    # Fresh and upgrade paths converge here, after the unit exists when one
    # was requested, so the helper can bind sudoers to its actual User= and
    # update NoNewPrivileges before the final restart.
    configure_autonomous_agent_remote "$remote" "$target_path" || exit 1
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        ssh "$remote" "sudo systemctl restart open-ace.service"
    fi

    print_header "Remote Deployment Complete!"
    print_info "Remote host: $DEPLOY_HOST"
    print_info "Installation path: $target_path"
    echo ""
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        echo "Service management on remote:"
        echo "  ssh $remote 'sudo systemctl status open-ace'"
        echo "  ssh $remote 'sudo systemctl start open-ace'"
        echo "  ssh $remote 'sudo systemctl stop open-ace'"
        echo "  ssh $remote 'sudo systemctl restart open-ace'"
        echo ""
        echo "View logs on remote:"
        echo "  ssh $remote 'sudo journalctl -u open-ace -f'"
    else
        echo "To start the web server on remote:"
        echo "  ssh $remote 'cd $target_path && python3 server.py'"
    fi
    echo ""
}

# ============================================================================
# Installation Functions
# ============================================================================

do_fresh_install() {
    local target_path="$1"
    local config_dir="$2"
    local install_user="$3"

    print_info "Performing fresh installation..."

    # Check if source and target are the same directory
    local source_abs="$(cd "$SOURCE_DIR" 2>/dev/null && pwd)"
    local target_abs="$(cd "$target_path" 2>/dev/null && pwd 2>/dev/null || echo "$target_path")"

    if [ "$source_abs" = "$target_abs" ]; then
        print_warning "Source and target directories are the same: $target_path"
        print_info "Skipping file copy (running from installation directory)"
    else
        # Create directories
        mkdir -p "$target_path"
        mkdir -p "$target_path/logs"

        # Copy files
        print_info "Copying files..."
        cp -r "$SOURCE_DIR"/* "$target_path/"

        # Set permissions
        chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
        chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true
    fi

    # Ensure logs directory exists
    mkdir -p "$target_path/logs"
    mkdir -p "$config_dir"

    # Create default config if not exists
    if [ ! -f "$config_dir/config.json" ]; then
        if [ -f "$target_path/config/config.json.sample" ]; then
            cp "$target_path/config/config.json.sample" "$config_dir/config.json"
            print_info "Created config file: $config_dir/config.json"
            # Update database configuration if PostgreSQL was set up
            if [ -n "$DB_PASSWORD" ] && [ "$DB_INSTALL_METHOD" != "" ]; then
                update_config_database "$config_dir/config.json"
            else
                print_warning "Please edit the config file with your database settings."
            fi
            # Update workspace configuration with webui path
            # First ensure symlinks are created (if running as root)
            if [ "$EUID" -eq 0 ]; then
                create_webui_symlinks
            fi
            # Use /usr/bin/qwen-code-webui as preferred path (symlink created above)
            # Fallback to find_webui_executable if symlink doesn't exist
            local webui_path="/usr/bin/qwen-code-webui"
            if [ ! -x "$webui_path" ]; then
                if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
                    webui_path=$(find_webui_executable)
                else
                    webui_path=$(find_webui_executable 2>/dev/null)
                fi
            fi
            update_config_workspace "$config_dir/config.json" "$webui_path"

            # Generate and set secret_key for Flask session and API key encryption
            local secret_key="${SECRET_KEY:-$(openssl rand -hex 32)}"
            update_config_secret_key "$config_dir/config.json" "$secret_key"
        fi
    fi

    # Fix ownership if running as root and a different user is specified
    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
        print_info "Setting ownership to $install_user..."
        chown -R "$install_user:$(id -gn "$install_user")" "$target_path"
        if [ -d "$config_dir" ]; then
            chown -R "$install_user:$(id -gn "$install_user")" "$config_dir"
        fi
    fi

    # Install system psycopg2 package first (avoids segfault from psycopg2-binary)
    # See Issue #38: psycopg2-binary 2.9.11 causes segfault on some Linux systems
    if [ "$DB_INSTALL_METHOD" != "sqlite" ]; then
        print_info "Checking for psycopg2 system package..."
        if ! python3 -c "import psycopg2" 2>/dev/null; then
            print_info "Installing system package python3-psycopg2..."
            if command -v dnf &>/dev/null; then
                dnf install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with dnf"
            elif command -v yum &>/dev/null; then
                yum install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with yum"
            elif command -v apt-get &>/dev/null; then
                apt-get install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with apt-get"
            else
                print_warning "Could not install python3-psycopg2 automatically. Please install it manually."
            fi
        else
            print_success "psycopg2 already available"
        fi
    fi

    # Check build dependencies before installing Python packages (gevent, bcrypt need gcc)
    check_build_dependencies

    # Install Python dependencies
    print_info "Installing Python dependencies..."

    # Install system psycopg2 package first (avoids segfault from psycopg2-binary)
    # See Issue #38: psycopg2-binary 2.9.11 causes segfault on some Linux systems
    if [ "$DB_INSTALL_METHOD" != "sqlite" ]; then
        print_info "Checking for psycopg2 system package..."
        if ! python3 -c "import psycopg2" 2>/dev/null; then
            print_info "Installing system package python3-psycopg2..."
            if command -v dnf &>/dev/null; then
                dnf install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with dnf"
            elif command -v yum &>/dev/null; then
                yum install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with yum"
            elif command -v apt-get &>/dev/null; then
                apt-get install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with apt-get"
            else
                print_warning "Could not install python3-psycopg2 automatically. Please install it manually."
            fi
        else
            print_success "psycopg2 already available"
        fi
    fi

    if [ -f "$target_path/requirements.txt" ]; then
        # Check if pip is available
        if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
            print_error "pip is not installed."
            print_info "Please run the following command as root to install pip:"
            if command -v dnf &>/dev/null; then
                print_info "  dnf install python3-pip"
            elif command -v yum &>/dev/null; then
                print_info "  yum install python3-pip"
            elif command -v apt-get &>/dev/null; then
                print_info "  apt-get install python3-pip"
            else
                print_info "  (install python3-pip using your package manager)"
            fi
            print_info "Then run this script again."
            exit 1
        fi

        # Create temp requirements excluding:
        # - psycopg2-binary: use system package instead (avoids segfault, see Issue #38)
        # - psycogreen: only has source dist, installed separately after build tools
        TEMP_REQ=$(mktemp)
        grep -v -e "psycopg2-binary" -e "psycogreen" "$target_path/requirements.txt" > "$TEMP_REQ" || true
        chmod 644 "$TEMP_REQ"  # Make readable for non-root users

        # Install dependencies (prefer vendor directory for offline install, fallback to network)
        local _pip_cmd=""
        if command -v pip3 &>/dev/null; then _pip_cmd="pip3"; elif command -v pip &>/dev/null; then _pip_cmd="pip"; fi
        local offline_install_failed=false

        if [ -d "$target_path/vendor" ] && [ "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            print_info "Installing from vendor directory (offline mode)..."
            # Pre-install setuptools + wheel for building source distributions
            if ls "$target_path/vendor"/setuptools*.whl 1>/dev/null 2>&1 || ls "$target_path/vendor"/wheel*.whl 1>/dev/null 2>&1; then
                print_info "Installing build tools (setuptools, wheel)..."
                if [ -n "$_pip_cmd" ]; then
                    # Install setuptools first (needed for setup.py based packages)
                    run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-index --find-links="$target_path/vendor" setuptools 2>/dev/null || true
                    # Install wheel separately (needed for bdist_wheel command)
                    if ls "$target_path/vendor"/wheel*.whl 1>/dev/null 2>&1; then
                        run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-index --find-links="$target_path/vendor" wheel 2>/dev/null || true
                    fi
                fi
            fi
            # Try offline installation from vendor directory
            if [ -n "$_pip_cmd" ]; then
                if run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-index --find-links="$target_path/vendor" -r "$TEMP_REQ" 2>&1; then
                    print_success "Dependencies installed from vendor"
                else
                    offline_install_failed=true
                    print_warning "Vendor directory installation failed (wheels may be incompatible with Python $(python3 --version 2>&1 | awk '{print $2}'))"
                    print_info "Falling back to online installation..."
                fi
            fi
            # Install psycogreen separately (source dist, needs build tools) - only if offline succeeded
            if [ "$offline_install_failed" = false ] && [ "$DB_INSTALL_METHOD" != "sqlite" ]; then
                print_info "Installing psycogreen..."
                if [ -n "$_pip_cmd" ]; then
                    run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-build-isolation --no-index --find-links="$target_path/vendor" psycogreen 2>/dev/null || \
                        print_warning "psycogreen install failed (non-critical, PostgreSQL connection pooling may not work optimally)"
                fi
            fi
        fi

        # Fallback to online installation if vendor install failed or vendor directory doesn't exist
        if [ "$offline_install_failed" = true ] || [ ! -d "$target_path/vendor" ] || [ ! "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            if [ -n "$_pip_cmd" ]; then
                print_info "Installing dependencies from network..."
                if run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location -r "$TEMP_REQ" 2>&1; then
                    print_success "Dependencies installed from network"
                else
                    print_error "Failed to install dependencies. Please check network connectivity and requirements.txt"
                fi
                # Install psycogreen from network
                if [ "$DB_INSTALL_METHOD" != "sqlite" ]; then
                    print_info "Installing psycogreen..."
                    run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-build-isolation psycogreen 2>/dev/null || \
                        print_warning "psycogreen install failed (non-critical)"
                fi
            else
                print_error "pip not found. Cannot install dependencies."
            fi
        fi
        rm -f "$TEMP_REQ"
    fi

    # Initialize database schema (Issue #1095: detect existing schema)
    print_info "Initializing database schema..."

    # Determine database type
    local db_type="postgresql"
    if [ -f "$config_dir/config.json" ]; then
        db_type=$(python3 -c "import json; c=json.load(open('$config_dir/config.json')); print(c.get('database', {}).get('type', 'postgresql'))")
    fi

    local schema_file=""
    if [ "$db_type" = "postgresql" ]; then
        schema_file="$target_path/schema/schema-postgres.sql"
        check_psql_client
    else
        schema_file="$target_path/schema/schema-sqlite.sql"
    fi

    # PostgreSQL: Check for existing schema before execution (Issue #1095)
    if [ "$db_type" = "postgresql" ]; then
        local db_url=$(python3 -c "import json; c=json.load(open('$config_dir/config.json')); print(c.get('database', {}).get('url', ''))")
        if [ -n "$db_url" ]; then
            cd "$target_path"

            # Check if Open ACE schema already exists
            local schema_exists="no"
            if check_app_schema_exists "$db_url"; then
                schema_exists="yes"
            fi

            if [ "$schema_exists" = "yes" ]; then
                # Existing schema: verify minimum supported revision, then upgrade
                print_info "Existing Open ACE schema detected, verifying minimum supported revision..."
                if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
                    su - "$install_user" -c "cd '$target_path' && python3 scripts/check_min_revision.py" || {
                        print_error "Database revision is below the minimum supported starting point (baseline_2026_06_23)."
                        print_info "Restore a known-healthy backup already on the baseline lineage, then re-run the upgrade."
                        exit 1
                    }
                else
                    python3 scripts/check_min_revision.py || {
                        print_error "Database revision is below the minimum supported starting point (baseline_2026_06_23)."
                        print_info "Restore a known-healthy backup already on the baseline lineage, then re-run the upgrade."
                        exit 1
                    }
                fi
                print_info "Running database migrations..."
                if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
                    su - "$install_user" -c "cd '$target_path' && python3 -m alembic upgrade head" && print_success "Database upgraded to latest version" || print_warning "alembic upgrade failed"
                else
                    python3 -m alembic upgrade head && print_success "Database upgraded to latest version" || print_warning "alembic upgrade failed"
                fi
            else
                # Fresh database: execute schema + stamp
                print_info "Executing full schema for fresh database..."
                local db_host=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.hostname or 'localhost')")
                local db_port=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.port or 5432)")
                local db_name=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print((u.path or '/').lstrip('/'))")
                local db_user=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.username or '')")
                local db_pass=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$db_url'); print(u.password or '')")

                if [ -f "$schema_file" ]; then
                    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
                        su - "$install_user" -c "cd '$target_path' && PGPASSWORD='$db_pass' psql -h '$db_host' -p '$db_port' -U '$db_user' -d '$db_name' -f '$schema_file'" && print_success "Database schema created" || print_warning "Failed to execute schema"
                    else
                        PGPASSWORD="$db_pass" psql -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" -f "$schema_file" && print_success "Database schema created" || print_warning "Failed to execute schema"
                    fi

                    # Mark alembic version as head
                    print_info "Marking database version..."
                    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
                        su - "$install_user" -c "cd '$target_path' && python3 -m alembic stamp head" && print_success "Database version marked" || print_warning "Failed to stamp version"
                    else
                        python3 -m alembic stamp head && print_success "Database version marked" || print_warning "Failed to stamp version"
                    fi
                else
                    print_warning "Schema file not found: $schema_file"
                fi
            fi
            cd - > /dev/null
        else
            print_warning "Database URL not found in config, skipping schema execution"
        fi
    else
        # SQLite: always execute schema (no existing schema detection for SQLite)
        cd "$target_path"
        if [ -f "$schema_file" ]; then
            print_info "Executing SQLite schema..."
            python3 -c "import sqlite3; c=sqlite3.connect('$config_dir/openace.db'); c.executescript(open('$schema_file').read())" && print_success "SQLite schema created" || print_warning "Failed to execute SQLite schema"
            print_info "Marking database version..."
            python3 -m alembic stamp head && print_success "Database version marked" || print_warning "Failed to stamp version"
        else
            print_warning "Schema file not found: $schema_file"
        fi
        cd - > /dev/null
    fi

    # Create default admin user
    print_info "Creating default admin user..."
    if [ -f "$target_path/scripts/init_db.py" ]; then
        # Run init_db.py as the install user to ensure it can access installed packages
        # Pass install_user as system_account for multi-user workspace mode
        if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
            # Running as root, but need to run as install_user to access their pip packages
            cd "$target_path"
            if su - "$install_user" -c "cd '$target_path' && OPENACE_SYSTEM_ACCOUNT='$install_user' python3 scripts/init_db.py"; then
                print_success "Default admin user created (system_account=$install_user)"
            else
                print_warning "Failed to create default admin user. You may need to run scripts/init_db.py manually."
            fi
            cd - > /dev/null
        else
            # Running as the target user already
            cd "$target_path"
            if OPENACE_SYSTEM_ACCOUNT="$install_user" python3 scripts/init_db.py; then
                print_success "Default admin user created (system_account=$install_user)"
            else
                print_warning "Failed to create default admin user. You may need to run scripts/init_db.py manually."
            fi
            cd - > /dev/null
        fi
    else
        print_warning "init_db.py not found, skipping default user creation"
    fi

    print_success "Fresh installation completed"
}

do_upgrade() {
    local target_path="$1"
    local config_dir="$2"
    local install_user="$3"
    local backup_dir="/tmp/open-ace-backup-$(date +%Y%m%d%H%M%S)"

    print_info "Upgrading existing installation..."

    # Check if source and target are the same directory
    local source_abs="$(cd "$SOURCE_DIR" 2>/dev/null && pwd)"
    local target_abs="$(cd "$target_path" 2>/dev/null && pwd 2>/dev/null || echo "$target_path")"

    if [ "$source_abs" = "$target_abs" ]; then
        print_warning "Source and target directories are the same: $target_path"
        print_info "Skipping file copy (running from installation directory)"
    else
        # Backup data files
        mkdir -p "$backup_dir"

        # Backup config directory
        if [ -d "$config_dir" ]; then
            print_info "Backing up config directory..."
            cp -r "$config_dir" "$backup_dir/"
        fi

        # Backup database in target path (if any)
        if [ -f "$target_path/usage.db" ]; then
            print_info "Backing up database..."
            cp "$target_path/usage.db" "$backup_dir/"
        fi

        # Backup frontend build artifacts if source doesn't have them (Issue #1943)
        # static/js/dist is not tracked in git, so upgrading from git repo would lose it
        local preserve_frontend=false
        if [ -d "$target_path/static/js/dist" ] && [ ! -d "$SOURCE_DIR/static/js/dist" ]; then
            print_info "Backing up frontend build artifacts (static/js/dist)..."
            mkdir -p "$backup_dir/static/js"
            cp -r "$target_path/static/js/dist" "$backup_dir/static/js/"
            preserve_frontend=true
        fi

        # Update files (preserve logs, data, and config)
        print_info "Updating files..."
        # Remove old files except logs, data, and config directory
        local config_basename=$(basename "$config_dir")

        # Check if SOURCE_DIR is inside target_path to prevent accidental deletion
        # Use normalized paths (source_abs/target_abs) for consistency
        local source_exclude=""
        if [[ "$source_abs" == "$target_abs"/* ]]; then
            local source_rel="${source_abs#$target_abs/}"
            local source_top=$(echo "$source_rel" | cut -d'/' -f1)
            source_exclude="$source_top"
            print_warning "Source directory is inside target path: $source_abs"
            print_info "Preserving '$source_top' directory during cleanup"
        fi

        # List directories that will be deleted and ask for confirmation
        local delete_list
        if [ -n "$source_exclude" ]; then
            delete_list=$(find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" ! -name "$source_exclude" 2>/dev/null)
        else
            delete_list=$(find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" 2>/dev/null)
        fi
        if [ -n "$delete_list" ]; then
            echo ""
            echo -e "${YELLOW}The following items will be deleted:${NC}"
            echo "$delete_list" | while read -r item; do
                echo "  - $(basename "$item")"
            done
            echo ""
            local skip_delete="no"
            prompt_yesno "Confirm deletion of the above items?" "y" confirm_delete
            if [ "$confirm_delete" != "yes" ]; then
                print_warning "Skipping directory cleanup, only overwriting files"
                skip_delete="yes"
            fi
            if [ "$skip_delete" = "no" ]; then
                if [ -n "$source_exclude" ]; then
                    find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" ! -name "$source_exclude" -exec rm -rf {} +
                else
                    find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" -exec rm -rf {} +
                fi
            fi
        else
            # No files to delete (shouldn't normally happen during upgrade)
            if [ -n "$source_exclude" ]; then
                find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" ! -name "$source_exclude" -exec rm -rf {} +
            else
                find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" -exec rm -rf {} +
            fi
        fi
        # Copy new files
        cp -r "$SOURCE_DIR"/* "$target_path/"

        # Set permissions
        chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
        chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true

        # Restore frontend build artifacts if source didn't have them (Issue #1943)
        if [ "$preserve_frontend" = true ]; then
            print_info "Restoring frontend build artifacts (source directory lacks static/js/dist)..."
            mkdir -p "$target_path/static/js"
            cp -r "$backup_dir/static/js/dist" "$target_path/static/js/"
            print_warning "Preserved existing frontend build artifacts"
            print_warning "To rebuild frontend with latest code, run:"
            print_warning "  cd $target_path/frontend && npm install && npm run build"
        fi
    fi

    # Fix ownership if running as root and a different user is specified
    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
        print_info "Setting ownership to $install_user..."
        chown -R "$install_user:$(id -gn "$install_user")" "$target_path"
        if [ -d "$config_dir" ]; then
            chown -R "$install_user:$(id -gn "$install_user")" "$config_dir"
        fi
    fi

    # Ensure config.json exists (upgrade may have lost it)
    if [ ! -f "$config_dir/config.json" ]; then
        print_warning "Config file not found: $config_dir/config.json"
        # Try to restore from backup
        if [ -d "$backup_dir" ] && [ -f "$backup_dir/.open-ace/config.json" ]; then
            print_info "Restoring config file from backup..."
            mkdir -p "$config_dir"
            cp "$backup_dir/.open-ace/config.json" "$config_dir/config.json"
        elif [ -f "$target_path/config/config.json.sample" ]; then
            print_info "Creating config file from sample..."
            mkdir -p "$config_dir"
            cp "$target_path/config/config.json.sample" "$config_dir/config.json"
            # Try to get database password from existing database URL in backup or environment
            if [ -n "$DB_TYPE" ] && [ -n "$DB_HOST" ] && [ -n "$DB_PASSWORD" ]; then
                update_config_database "$config_dir/config.json"
            elif [ -f "$backup_dir/.open-ace/config.json" ]; then
                # Extract password from backup config and use it
                local backup_db_url=$(python3 -c "import json; c=json.load(open('$backup_dir/.open-ace/config.json')); print(c.get('database', {}).get('url', ''))" 2>/dev/null)
                if [ -n "$backup_db_url" ]; then
                    # Update config with backup's database URL
                    python3 -c "
import json
with open('$config_dir/config.json', 'r') as f:
    config = json.load(f)
config['database'] = {'type': 'postgresql', 'url': '$backup_db_url'}
with open('$config_dir/config.json', 'w') as f:
    json.dump(config, f, indent=2)
"
                    print_info "Restored database URL from backup"
                fi
            fi
        else
            print_warning "Cannot create config file. Manual setup required."
        fi
        # Fix ownership
        if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ] && [ -f "$config_dir/config.json" ]; then
            chown "$install_user:$(id -gn "$install_user")" "$config_dir/config.json"
        fi
    fi

    # Update workspace configuration (only for fresh install, not upgrade)
    if [ "$DO_UPGRADE" != "yes" ] && [ -f "$config_dir/config.json" ]; then
        # First ensure symlinks are created (if running as root)
        if [ "$EUID" -eq 0 ]; then
            create_webui_symlinks
        fi
        # Use /usr/bin/qwen-code-webui as preferred path (symlink created above)
        # Fallback to find_webui_executable if symlink doesn't exist
        local webui_path="/usr/bin/qwen-code-webui"
        if [ ! -x "$webui_path" ]; then
            if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
                webui_path=$(find_webui_executable)
            else
                webui_path=$(find_webui_executable 2>/dev/null)
            fi
        fi
        update_config_workspace "$config_dir/config.json" "$webui_path"

        # Check and set secret_key if missing (upgrade from older version)
        local current_secret=$(python3 -c "import json; c=json.load(open('$config_dir/config.json')); print(c.get('secret_key', ''))" 2>/dev/null)
        if [ -z "$current_secret" ] || [ "$current_secret" = "<SECRET_KEY>" ]; then
            print_warning "Adding missing secret_key to config.json..."
            local secret_key="${SECRET_KEY:-$(openssl rand -hex 32)}"
            update_config_secret_key "$config_dir/config.json" "$secret_key"
        fi
    fi

    # Check build dependencies before installing Python packages (gevent, bcrypt need gcc)
    check_build_dependencies

    # Install Python dependencies
    print_info "Installing Python dependencies..."

    # Install system psycopg2 package first (avoids segfault from psycopg2-binary)
    # See Issue #38: psycopg2-binary 2.9.11 causes segfault on some Linux systems
    if [ "$DB_INSTALL_METHOD" != "sqlite" ]; then
        print_info "Checking for psycopg2 system package..."
        if ! python3 -c "import psycopg2" 2>/dev/null; then
            print_info "Installing system package python3-psycopg2..."
            if command -v dnf &>/dev/null; then
                dnf install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with dnf"
            elif command -v yum &>/dev/null; then
                yum install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with yum"
            elif command -v apt-get &>/dev/null; then
                apt-get install -y python3-psycopg2 || print_warning "Failed to install python3-psycopg2 with apt-get"
            else
                print_warning "Could not install python3-psycopg2 automatically. Please install it manually."
            fi
        else
            print_success "psycopg2 already available"
        fi
    fi

    if [ -f "$target_path/requirements.txt" ]; then
        # Check if pip is available
        if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
            print_error "pip is not installed."
            print_info "Please run the following command as root to install pip:"
            if command -v dnf &>/dev/null; then
                print_info "  dnf install python3-pip"
            elif command -v yum &>/dev/null; then
                print_info "  yum install python3-pip"
            elif command -v apt-get &>/dev/null; then
                print_info "  apt-get install python3-pip"
            else
                print_info "  (install python3-pip using your package manager)"
            fi
            print_info "Then run this script again."
            exit 1
        fi

        # Install dependencies (prefer vendor directory for offline install)
        # Exclude psycopg2-binary (use system package instead) and psycogreen (source dist, handled separately)
        TEMP_REQ=$(mktemp)
        grep -v -e "psycopg2-binary" -e "psycogreen" "$target_path/requirements.txt" > "$TEMP_REQ" || true
        chmod 644 "$TEMP_REQ"  # Make readable for non-root users

        local _pip_cmd=""
        if command -v pip3 &>/dev/null; then _pip_cmd="pip3"; elif command -v pip &>/dev/null; then _pip_cmd="pip"; fi
        local offline_install_failed=false

        if [ -d "$target_path/vendor" ] && [ "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            print_info "Installing from vendor directory (offline mode)..."
            # Pre-install setuptools + wheel for building source distributions
            if ls "$target_path/vendor"/setuptools*.whl 1>/dev/null 2>&1 || ls "$target_path/vendor"/wheel*.whl 1>/dev/null 2>&1; then
                print_info "Installing build tools (setuptools, wheel)..."
                if [ -n "$_pip_cmd" ]; then
                    # Install setuptools first (needed for setup.py based packages)
                    run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-index --find-links="$target_path/vendor" setuptools 2>/dev/null || true
                    # Install wheel separately (needed for bdist_wheel command)
                    if ls "$target_path/vendor"/wheel*.whl 1>/dev/null 2>&1; then
                        run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-index --find-links="$target_path/vendor" wheel 2>/dev/null || true
                    fi
                fi
            fi
            # Try offline installation from vendor directory
            if [ -n "$_pip_cmd" ]; then
                if run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-index --find-links="$target_path/vendor" -r "$TEMP_REQ" 2>&1; then
                    print_success "Dependencies installed from vendor"
                else
                    offline_install_failed=true
                    print_warning "Vendor directory installation failed (wheels may be incompatible with Python $(python3 --version 2>&1 | awk '{print $2}'))"
                    print_info "Falling back to online installation..."
                fi
            fi
            # Install psycogreen separately (source dist, needs build tools) - only if offline succeeded
            if [ "$offline_install_failed" = false ]; then
                print_info "Installing psycogreen..."
                if [ -n "$_pip_cmd" ]; then
                    run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-build-isolation --no-index --find-links="$target_path/vendor" psycogreen 2>/dev/null || \
                        print_warning "psycogreen install failed (non-critical)"
                fi
            fi
        fi

        # Fallback to online installation if vendor install failed or vendor directory doesn't exist
        if [ "$offline_install_failed" = true ] || [ ! -d "$target_path/vendor" ] || [ ! "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            if [ -n "$_pip_cmd" ]; then
                print_info "Installing dependencies from network..."
                if run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location -r "$TEMP_REQ" 2>&1; then
                    print_success "Dependencies installed from network"
                else
                    print_error "Failed to install dependencies. Please check network connectivity and requirements.txt"
                fi
                # Install psycogreen from network
                print_info "Installing psycogreen..."
                run_pip_as_user "$install_user" $_pip_cmd install --user --no-warn-script-location --no-build-isolation psycogreen 2>/dev/null || \
                    print_warning "psycogreen install failed (non-critical)"
            else
                print_error "pip not found. Cannot install dependencies."
            fi
        fi
        rm -f "$TEMP_REQ"
    fi

    # Create default admin user (if not exists)
    print_info "Ensuring default admin user exists..."
    if [ -f "$target_path/scripts/init_db.py" ]; then
        # Run init_db.py as the install user to ensure it can access installed packages
        # Pass install_user as system_account for multi-user workspace mode
        if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
            # Running as root, but need to run as install_user to access their pip packages
            cd "$target_path"
            if su - "$install_user" -c "cd '$target_path' && OPENACE_SYSTEM_ACCOUNT='$install_user' python3 scripts/init_db.py"; then
                print_success "Default admin user ready (system_account=$install_user)"
            else
                print_warning "Failed to create default admin user. You may need to run scripts/init_db.py manually."
            fi
            cd - > /dev/null
        else
            # Running as the target user already
            cd "$target_path"
            if OPENACE_SYSTEM_ACCOUNT="$install_user" python3 scripts/init_db.py; then
                print_success "Default admin user ready (system_account=$install_user)"
            else
                print_warning "Failed to create default admin user. You may need to run scripts/init_db.py manually."
            fi
            cd - > /dev/null
        fi
    else
        print_warning "init_db.py not found, skipping default user creation"
    fi

    # Run database migrations (alembic upgrade head)
    print_info "Running database migrations..."
    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
        su - "$install_user" -c "cd '$target_path' && python3 scripts/check_min_revision.py" || {
            print_error "Database revision is below the minimum supported starting point (baseline_2026_06_23)."
            print_info "Restore a known-healthy backup already on the baseline lineage, then re-run the upgrade."
            exit 1
        }
    else
        cd "$target_path"
        python3 scripts/check_min_revision.py || {
            print_error "Database revision is below the minimum supported starting point (baseline_2026_06_23)."
            print_info "Restore a known-healthy backup already on the baseline lineage, then re-run the upgrade."
            cd - > /dev/null
            exit 1
        }
        cd - > /dev/null
    fi
    if [ -f "$target_path/alembic.ini" ] && [ -d "$target_path/migrations" ]; then
        if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
            if su - "$install_user" -c "cd '$target_path' && python3 -m alembic upgrade head"; then
                print_success "Database migrations applied"
            else
                print_warning "Database migration failed. You may need to run 'alembic upgrade head' manually."
            fi
        else
            cd "$target_path"
            if python3 -m alembic upgrade head; then
                print_success "Database migrations applied"
            else
                print_warning "Database migration failed. You may need to run 'alembic upgrade head' manually."
            fi
            cd - > /dev/null
        fi
    else
        print_warning "Alembic not found, skipping database migrations"
    fi

    print_success "Upgrade completed"
    print_info "Backup saved to: $backup_dir"
}

do_fresh_install_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Performing fresh remote installation..."

    # Create directories
    ssh "$remote" "mkdir -p '$target_path' '$target_path/logs'"

    # Copy files
    print_info "Copying files to remote..."
    scp -r "$SOURCE_DIR"/* "$remote:$target_path/"

    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"

    # Create config directory
    ssh "$remote" "mkdir -p '~/.open-ace'"

    # Check build dependencies before installing Python packages (gevent, bcrypt need gcc)
    check_build_dependencies_remote "$remote" || exit 1
    # Check PostgreSQL client for schema execution
    check_psql_client_remote "$remote"

    # Install Python dependencies
    print_info "Installing Python dependencies on remote..."
    ssh "$remote" "
        cd '$target_path'
        # Check if pip is available
        if ! command -v pip3 >/dev/null 2>&1 && ! command -v pip >/dev/null 2>&1; then
            echo 'ERROR: pip is not installed on remote machine.'
            echo 'Please install pip first:'
            if command -v dnf >/dev/null 2>&1; then
                echo '  dnf install python3-pip'
            elif command -v yum >/dev/null 2>&1; then
                echo '  yum install python3-pip'
            elif command -v apt-get >/dev/null 2>&1; then
                echo '  apt-get install python3-pip'
            else
                echo '  (install python3-pip using your package manager)'
            fi
            exit 1
        fi
        # Install dependencies (prefer vendor directory for offline install)
        # Exclude psycopg2-binary (use system package instead) and psycogreen (source dist, handled separately)
        TEMP_REQ=\$(mktemp)
        grep -v -e 'psycopg2-binary' -e 'psycogreen' requirements.txt > \$TEMP_REQ || true
        offline_install_failed=false
        if [ -d 'vendor' ] && [ \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing from vendor directory (offline mode)...'
            # Pre-install setuptools + wheel for building source distributions
            if ls vendor/setuptools*.whl 1>/dev/null 2>&1 || ls vendor/wheel*.whl 1>/dev/null 2>&1; then
                echo 'Installing build tools (setuptools, wheel)...'
                if command -v pip3 >/dev/null 2>&1; then
                    pip3 install --user --no-warn-script-location --no-index --find-links=vendor setuptools 2>/dev/null || true
                    if ls vendor/wheel*.whl 1>/dev/null 2>&1; then
                        pip3 install --user --no-warn-script-location --no-index --find-links=vendor wheel 2>/dev/null || true
                    fi
                elif command -v pip >/dev/null 2>&1; then
                    pip install --user --no-warn-script-location --no-index --find-links=vendor setuptools 2>/dev/null || true
                    if ls vendor/wheel*.whl 1>/dev/null 2>&1; then
                        pip install --user --no-warn-script-location --no-index --find-links=vendor wheel 2>/dev/null || true
                    fi
                fi
            fi
            # Try offline installation from vendor directory
            if command -v pip3 >/dev/null 2>&1; then
                if ! pip3 install --user --no-warn-script-location --no-index --find-links=vendor -r \$TEMP_REQ 2>&1; then
                    offline_install_failed=true
                    echo 'Warning: Vendor directory installation failed (wheels may be incompatible with current Python version)'
                    echo 'Falling back to online installation...'
                fi
            elif command -v pip >/dev/null 2>&1; then
                if ! pip install --user --no-warn-script-location --no-index --find-links=vendor -r \$TEMP_REQ 2>&1; then
                    offline_install_failed=true
                    echo 'Warning: Vendor directory installation failed (wheels may be incompatible with current Python version)'
                    echo 'Falling back to online installation...'
                fi
            fi
            # Install psycogreen separately (source dist, needs build tools) - only if offline succeeded
            if [ \"\$offline_install_failed\" = false ]; then
                echo 'Installing psycogreen...'
                if command -v pip3 >/dev/null 2>&1; then
                    pip3 install --user --no-warn-script-location --no-build-isolation --no-index --find-links=vendor psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
                elif command -v pip >/dev/null 2>&1; then
                    pip install --user --no-warn-script-location --no-build-isolation --no-index --find-links=vendor psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
                fi
            fi
        fi
        # Fallback to online installation if vendor install failed or vendor directory doesn't exist
        if [ \"\$offline_install_failed\" = true ] || [ ! -d 'vendor' ] || [ ! \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing dependencies from network...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-warn-script-location -r \$TEMP_REQ || echo 'ERROR: Failed to install dependencies'
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-warn-script-location -r \$TEMP_REQ || echo 'ERROR: Failed to install dependencies'
            fi
            # Install psycogreen from network
            echo 'Installing psycogreen...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-warn-script-location --no-build-isolation psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-warn-script-location --no-build-isolation psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
            fi
        fi
        rm -f \$TEMP_REQ
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

    # Initialize database schema
    print_info "Initializing database schema on remote..."
    ssh "$remote" "
        cd '$target_path'
        # Determine database type from config
        db_type='postgresql'
        config_file=\$(python3 -c \"import os; print(os.path.expanduser('~/.open-ace/config.json'))\" 2>/dev/null)
        if [ -f \"\$config_file\" ]; then
            db_type=\$(python3 -c \"import json; c=json.load(open('\$config_file')); print(c.get('database', {}).get('type', 'postgresql'))\" 2>/dev/null || echo 'postgresql')
        fi

        if [ \"\$db_type\" = 'postgresql' ]; then
            schema_file='schema/schema-postgres.sql'
            db_url=\$(python3 -c \"import json; c=json.load(open('\$config_file')); print(c.get('database', {}).get('url', ''))\" 2>/dev/null)
            if [ -n \"\$db_url\" ] && [ -f \"\$schema_file\" ]; then
                eval \$(python3 -c \"
from urllib.parse import urlparse
u = urlparse('\$db_url')
print(f'db_host={u.hostname or \"localhost\"}')
print(f'db_port={u.port or 5432}')
print(f'db_name={(u.path or \"/\").lstrip(\"/\")}')
print(f'db_user={u.username or \"\"}')
pw = u.password or ''
print(f\"db_pass='{pw}'\")
\")
                if [ -z \"\$db_name\" ]; then
                    echo 'ERROR: Could not parse database name from URL'
                    exit 1
                fi

                # Check for existing schema (Issue #1095)
                schema_exists=\$(PGPASSWORD=\"\$db_pass\" psql -h \"\$db_host\" -p \"\$db_port\" -U \"\$db_user\" -d \"\$db_name\" -tAc \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users', 'agent_sessions', 'session_messages')\" 2>/dev/null || echo \"0\")

                if [ \"\$schema_exists\" = \"3\" ]; then
                    echo 'Existing Open ACE schema detected (3 sentinel tables found)'
                    # Verify minimum supported revision, then upgrade
                    if ! python3 scripts/check_min_revision.py; then
                        echo 'ERROR: database revision is below the minimum supported starting point (baseline_2026_06_23).'
                        echo '       Restore a known-healthy backup already on the baseline lineage, then re-run the upgrade.'
                        exit 1
                    fi
                    echo 'Running database migrations...'
                    python3 -m alembic upgrade head || echo 'ERROR: alembic upgrade failed'
                else
                    echo 'Executing full schema for fresh database...'
                    if PGPASSWORD=\"\$db_pass\" psql -h \"\$db_host\" -p \"\$db_port\" -U \"\$db_user\" -d \"\$db_name\" -f \"\$schema_file\"; then
                        echo 'Database schema created'
                        # Mark alembic version as head
                        python3 -m alembic stamp head || echo 'ERROR: Failed to stamp version'
                    else
                        echo 'ERROR: Failed to execute PostgreSQL schema.'
                        exit 1
                    fi
                fi
            else
                echo 'ERROR: Database URL not found or schema file missing'
                exit 1
            fi
        elif [ -f 'schema/schema-sqlite.sql' ]; then
            if python3 -c \"import sqlite3, os; c=sqlite3.connect(os.path.expanduser('~/.open-ace/ace.db')); c.executescript(open('schema/schema-sqlite.sql').read())\"; then
                echo 'SQLite schema created'
                # Mark alembic version for SQLite
                python3 -m alembic stamp head || echo 'Warning: Failed to stamp SQLite version'
            else
                echo 'ERROR: Failed to execute SQLite schema.'
                exit 1
            fi
        fi
    " || {
        print_error "Failed to initialize database schema on remote."
        exit 1
    }

    # Note: For PostgreSQL with existing schema, alembic upgrade was already done above
    # Only need to handle SQLite here (SQLite schema already stamped above)

    # Create default admin user
    print_info "Creating default admin user on remote..."
    ssh "$remote" "
        cd '$target_path'
        if [ -f 'scripts/init_db.py' ]; then
            if OPENACE_SYSTEM_ACCOUNT='$DEPLOY_USER' python3 scripts/init_db.py; then
                echo 'Default admin user created successfully (system_account=$DEPLOY_USER)'
            else
                echo 'Warning: Failed to create default admin user. You may need to run scripts/init_db.py manually.'
            fi
        else
            echo 'Warning: init_db.py not found, skipping default user creation'
        fi
    "

    print_success "Fresh remote installation completed"
}

do_upgrade_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Upgrading remote installation..."

    # Backup data files
    local backup_dir="/tmp/open-ace-backup-$(date +%Y%m%d%H%M%S)"
    ssh "$remote" "mkdir -p '$backup_dir'"

    # Backup config directory
    ssh "$remote" "if [ -d '~/.open-ace' ]; then cp -r ~/.open-ace '$backup_dir/'; fi"

    # Backup database
    ssh "$remote" "if [ -f '$target_path/usage.db' ]; then cp '$target_path/usage.db' '$backup_dir/'; fi"

    # Update files (preserve logs, data, and config)
    print_info "Updating remote files..."

    # Note: For remote upgrade, SOURCE_DIR is local and target_path is remote.
    # They are on different machines, so SOURCE_DIR cannot be inside target_path.
    # No need to check for SOURCE_DIR containment - just proceed with normal cleanup.

    # List directories that will be deleted and ask for confirmation
    local remote_delete_list
    remote_delete_list=$(ssh "$remote" "cd '$target_path' && find . -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name '.open-ace' 2>/dev/null")
    if [ -n "$remote_delete_list" ]; then
        echo ""
        echo -e "${YELLOW}The following items will be deleted on remote:${NC}"
        echo "$remote_delete_list" | while read -r item; do
            echo "  - $(basename "$item")"
        done
        echo ""
        local remote_skip_delete="no"
        prompt_yesno "Confirm deletion of the above remote items?" "y" confirm_remote_delete
        if [ "$confirm_remote_delete" != "yes" ]; then
            print_warning "Skipping remote directory cleanup, only overwriting files"
            remote_skip_delete="yes"
        fi
        if [ "$remote_skip_delete" = "no" ]; then
            ssh "$remote" "cd '$target_path' && find . -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name '.open-ace' -exec rm -rf {} +"
        fi
    else
        ssh "$remote" "cd '$target_path' && find . -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name '.open-ace' -exec rm -rf {} +"
    fi
    scp -r "$SOURCE_DIR"/* "$remote:$target_path/"

    # Upgrade did not historically run the remote dependency probe. The
    # credentialless launcher requires these runtime tools as well as the
    # existing native Python build dependencies.
    check_build_dependencies_remote "$remote" || exit 1

    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"

    # Sync remote-agent directory to ~/.open-ace-agent (for agent service)
    print_info "Syncing remote-agent files to ~/.open-ace-agent..."
    ssh "$remote" "mkdir -p ~/.open-ace-agent ~/.open-ace-agent/cli_adapters"
    scp -r "$SOURCE_DIR/remote-agent/*.py" "$remote:~/.open-ace-agent/"
    scp -r "$SOURCE_DIR/remote-agent/cli_adapters/*.py" "$remote:~/.open-ace-agent/cli_adapters/"
    ssh "$remote" "chmod +x ~/.open-ace-agent/*.py ~/.open-ace-agent/cli_adapters/*.py 2>/dev/null || true"
    ssh "$remote" "systemctl restart open-ace-agent 2>/dev/null || echo 'Note: open-ace-agent service not found or not installed'"

    # Install Python dependencies
    print_info "Installing Python dependencies on remote..."
    ssh "$remote" "
        cd '$target_path'
        # Check if pip is available
        if ! command -v pip3 >/dev/null 2>&1 && ! command -v pip >/dev/null 2>&1; then
            echo 'ERROR: pip is not installed on remote machine.'
            echo 'Please install pip first:'
            if command -v dnf >/dev/null 2>&1; then
                echo '  dnf install python3-pip'
            elif command -v yum >/dev/null 2>&1; then
                echo '  yum install python3-pip'
            elif command -v apt-get >/dev/null 2>&1; then
                echo '  apt-get install python3-pip'
            else
                echo '  (install python3-pip using your package manager)'
            fi
            exit 1
        fi
        # Install dependencies (prefer vendor directory for offline install)
        # Exclude psycopg2-binary (use system package instead) and psycogreen (source dist, handled separately)
        TEMP_REQ=\$(mktemp)
        grep -v -e 'psycopg2-binary' -e 'psycogreen' requirements.txt > \$TEMP_REQ || true
        offline_install_failed=false
        if [ -d 'vendor' ] && [ \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing from vendor directory (offline mode)...'
            # Pre-install setuptools + wheel for building source distributions
            if ls vendor/setuptools*.whl 1>/dev/null 2>&1 || ls vendor/wheel*.whl 1>/dev/null 2>&1; then
                echo 'Installing build tools (setuptools, wheel)...'
                if command -v pip3 >/dev/null 2>&1; then
                    pip3 install --user --no-warn-script-location --no-index --find-links=vendor setuptools 2>/dev/null || true
                    if ls vendor/wheel*.whl 1>/dev/null 2>&1; then
                        pip3 install --user --no-warn-script-location --no-index --find-links=vendor wheel 2>/dev/null || true
                    fi
                elif command -v pip >/dev/null 2>&1; then
                    pip install --user --no-warn-script-location --no-index --find-links=vendor setuptools 2>/dev/null || true
                    if ls vendor/wheel*.whl 1>/dev/null 2>&1; then
                        pip install --user --no-warn-script-location --no-index --find-links=vendor wheel 2>/dev/null || true
                    fi
                fi
            fi
            # Try offline installation from vendor directory
            if command -v pip3 >/dev/null 2>&1; then
                if ! pip3 install --user --no-warn-script-location --no-index --find-links=vendor -r \$TEMP_REQ 2>&1; then
                    offline_install_failed=true
                    echo 'Warning: Vendor directory installation failed (wheels may be incompatible with current Python version)'
                    echo 'Falling back to online installation...'
                fi
            elif command -v pip >/dev/null 2>&1; then
                if ! pip install --user --no-warn-script-location --no-index --find-links=vendor -r \$TEMP_REQ 2>&1; then
                    offline_install_failed=true
                    echo 'Warning: Vendor directory installation failed (wheels may be incompatible with current Python version)'
                    echo 'Falling back to online installation...'
                fi
            fi
            # Install psycogreen separately (source dist, needs build tools) - only if offline succeeded
            if [ \"\$offline_install_failed\" = false ]; then
                echo 'Installing psycogreen...'
                if command -v pip3 >/dev/null 2>&1; then
                    pip3 install --user --no-warn-script-location --no-build-isolation --no-index --find-links=vendor psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
                elif command -v pip >/dev/null 2>&1; then
                    pip install --user --no-warn-script-location --no-build-isolation --no-index --find-links=vendor psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
                fi
            fi
        fi
        # Fallback to online installation if vendor install failed or vendor directory doesn't exist
        if [ \"\$offline_install_failed\" = true ] || [ ! -d 'vendor' ] || [ ! \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing dependencies from network...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-warn-script-location -r \$TEMP_REQ || echo 'ERROR: Failed to install dependencies'
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-warn-script-location -r \$TEMP_REQ || echo 'ERROR: Failed to install dependencies'
            fi
            # Install psycogreen from network
            echo 'Installing psycogreen...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-warn-script-location --no-build-isolation psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-warn-script-location --no-build-isolation psycogreen 2>/dev/null || echo 'Warning: psycogreen install failed (non-critical)'
            fi
        fi
        rm -f \$TEMP_REQ
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

    # Create default admin user (if not exists)
    print_info "Ensuring default admin user exists on remote..."
    ssh "$remote" "
        cd '$target_path'
        if [ -f 'scripts/init_db.py' ]; then
            if OPENACE_SYSTEM_ACCOUNT='$DEPLOY_USER' python3 scripts/init_db.py; then
                echo 'Default admin user ready (system_account=$DEPLOY_USER)'
            else
                echo 'Warning: Failed to create default admin user. You may need to run scripts/init_db.py manually.'
            fi
        else
            echo 'Warning: init_db.py not found, skipping default user creation'
        fi
    "

    # Run database migrations (alembic upgrade head)
    print_info "Running database migrations on remote..."
    ssh "$remote" "
        cd '$target_path'
        if ! python3 scripts/check_min_revision.py; then
            echo 'ERROR: database revision is below the minimum supported starting point (baseline_2026_06_23).'
            echo '       Restore a known-healthy backup already on the baseline lineage, then re-run the upgrade.'
            exit 1
        fi
        if [ -f 'alembic.ini' ] && [ -d 'migrations' ]; then
            if python3 -m alembic upgrade head; then
                echo 'Database migrations applied'
            else
                echo 'Warning: Database migration failed. You may need to run alembic upgrade head manually.'
            fi
        else
            echo 'Warning: Alembic not found, skipping database migrations'
        fi
    "

    print_success "Remote upgrade completed"
    print_info "Backup saved to: $backup_dir on $DEPLOY_HOST"

    # Check if systemd service exists on remote and update SECRET_KEY if missing
    if ssh "$remote" "command -v systemctl &>/dev/null && systemctl is-enabled --quiet open-ace.service 2>/dev/null"; then
        print_info "Checking systemd service on remote..."
        local service_file="/etc/systemd/system/open-ace.service"
        local current_secret=$(ssh "$remote" "grep '^Environment=SECRET_KEY=' $service_file 2>/dev/null | cut -d'=' -f3")
        if [ -z "$current_secret" ]; then
            print_warning "Adding missing SECRET_KEY to systemd service on remote..."
            local secret_key="${SECRET_KEY:-$(openssl rand -hex 32)}"
            ssh "$remote" "sudo sed -i '/^Environment=HOME=/a Environment=SECRET_KEY=$secret_key' $service_file && sudo systemctl daemon-reload && sudo systemctl restart open-ace.service"
            print_info "Generated SECRET_KEY for Flask encryption"
        else
            print_info "Restarting systemd service on remote..."
            ssh "$remote" "sudo systemctl restart open-ace.service"
        fi

        # Check if systemd service is missing WORKSPACE_BASE_DIR (Issue #1217)
        local current_workspace_base=$(ssh "$remote" "grep '^Environment=WORKSPACE_BASE_DIR=' $service_file 2>/dev/null | cut -d'=' -f3")
        if [ -z "$current_workspace_base" ]; then
            print_warning "Adding missing WORKSPACE_BASE_DIR to systemd service on remote..."
            ssh "$remote" "sudo sed -i '/^Environment=SECRET_KEY=/a Environment=WORKSPACE_BASE_DIR=$WORKSPACE_BASE_DIR' $service_file && sudo systemctl daemon-reload && sudo systemctl restart open-ace.service"
            print_info "Set WORKSPACE_BASE_DIR=$WORKSPACE_BASE_DIR"
        fi
    fi
}

# ============================================================================
# Main
# ============================================================================

show_help() {
    echo "Open ACE - Installation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --config FILE   Use configuration file"
    echo "  --help, -h      Show this help message"
    echo ""
    echo "Configuration File Format (shell script):"
    echo ""
    echo "  # Local installation (install on this machine)"
    echo "  DEPLOY_USER=\${USER}"
    echo "  DEPLOY_PATH=\$HOME/open-ace"
    echo ""
    echo "  # Remote deployment (deploy via SSH)"
    echo "  DEPLOY_HOST=192.168.1.100"
    echo "  DEPLOY_USER=openclaw"
    echo "  DEPLOY_PATH=/home/openclaw/open-ace"
    echo ""
    echo "  # Systemd service configuration (optional)"
    echo "  INSTALL_SERVICE=yes              # Install as systemd service"
    echo "  SERVICE_PORT=19888               # Web server port"
    echo "  SERVICE_HOST=0.0.0.0             # Web server host"
    echo ""
    echo "  # Database configuration (optional - auto-detected if not specified)"
    echo "  DB_HOST=localhost                 # Database host"
    echo "  DB_PORT=5432                      # Database port"
    echo "  DB_NAME=openace                   # Database name"
    echo "  DB_USER=openace                   # Database user"
    echo "  DB_PASSWORD=yourpassword          # Database password (required for existing DB)"
    echo "  DB_INSTALL_METHOD=existing        # 'existing', 'binary', or 'docker'"
    echo ""
    echo "  # Multi-user workspace mode (optional)"
    echo "  WORKSPACE_MULTI_USER_MODE=true   # Enable multi-user mode"
    echo "  WORKSPACE_PORT_RANGE_START=3100  # Port pool start"
    echo "  WORKSPACE_PORT_RANGE_END=3200    # Port pool end"
    echo "  WORKSPACE_MAX_INSTANCES=30       # Max concurrent instances"
    echo "  WORKSPACE_IDLE_TIMEOUT=30        # Idle timeout (minutes)"
    echo "  WORKSPACE_BASE_DIR=/home         # Workspace base directory (default: /home)"
    echo ""
    echo "Database Configuration:"
    echo "  If DB_HOST and DB_PASSWORD are set, the installer will use existing database."
    echo "  If DB_INSTALL_METHOD is 'binary' or 'docker', will install new PostgreSQL."
    echo ""
    echo "Multi-User Workspace Mode:"
    echo "  Requires qwen-code-webui installed:"
    echo "    npm install -g qwen-code-webui"
    echo ""
    echo "  The installer will auto-configure sudoers for user switching."
    echo "  Each user needs a system account and ~/.qwen/ directory."
    echo ""
    echo "Examples:"
    echo "  $0                              # Interactive mode"
    echo "  $0 --config install.conf        # Use config file"
    echo ""
    echo "After installation with systemd service:"
    echo "  systemctl status open-ace   # Check service status"
    echo "  systemctl start open-ace    # Start service"
    echo "  systemctl stop open-ace     # Stop service"
    echo "  systemctl restart open-ace  # Restart service"
    echo "  journalctl -u open-ace -f   # View logs"
    echo ""
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config|-c)
            CONFIG_FILE="$2"
            shift 2
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

# Main execution
print_header "Open ACE - Installer"

if [ -n "$CONFIG_FILE" ]; then
    parse_config_file
else
    # Check if running in interactive terminal
    if [ ! -t 0 ] && [ ! -t 1 ]; then
        print_error "Not running in an interactive terminal."
        print_info "Please use --config option for non-interactive installation."
        print_info ""
        print_info "Example:"
        print_info "  $0 --config install.conf"
        print_info ""
        print_info "Or run with a terminal:"
        print_info "  bash -i $0"
        exit 1
    fi
    interactive_config
fi

# Perform installation
case $INSTALL_MODE in
    local)
        install_local
        ;;
    deploy)
        install_deploy
        ;;
    *)
        print_error "Invalid INSTALL_MODE: $INSTALL_MODE"
        exit 1
        ;;
esac
