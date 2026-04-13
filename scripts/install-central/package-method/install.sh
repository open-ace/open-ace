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
# SOURCE_DIR should be the package root (where cli.py, web.py, etc. are located)
# Package structure: package_root/scripts/install-central/package-method/install.sh
# So we need to go up 3 levels from SCRIPT_DIR to reach package_root
# SCRIPT_DIR = scripts/install-central/package-method
# LEVEL 1 up = scripts/install-central
# LEVEL 2 up = scripts
# LEVEL 3 up = package_root (where web.py, cli.py are)
SOURCE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Validate SOURCE_DIR - must contain web.py or cli.py
validate_source_dir() {
    if [ ! -f "$SOURCE_DIR/web.py" ] && [ ! -f "$SOURCE_DIR/cli.py" ]; then
        print_error "SOURCE_DIR is invalid: $SOURCE_DIR"
        print_error "Expected to find web.py or cli.py in package root"
        print_info "Please ensure you're running install.sh from a valid Open ACE package"
        print_info "Current SCRIPT_DIR: $SCRIPT_DIR"
        exit 1
    fi
    print_info "Source directory validated: $SOURCE_DIR"
}

# Default values
CONFIG_FILE=""
INSTALL_MODE=""  # "local" or "deploy"

# Deployment settings (for both local and deploy modes)
DEPLOY_HOST=""        # Empty for local mode, required for deploy mode
DEPLOY_USER=""        # Will be set after checking for openace user
DEPLOY_PATH=""        # Will be set based on DEPLOY_USER

# ============================================================================
# Python Version Check
# ============================================================================

# Check Python version (Open ACE requires Python >= 3.9)
check_python_version() {
    if ! command -v python3 &>/dev/null; then
        print_error "Python 3 is not installed"
        print_info "Open ACE requires Python 3.9 or later"
        print_info ""
        print_info "On CentOS/RHEL 7, you can install Python 3.9 via:"
        print_info "  yum install rh-python39 rh-python39-python-pip"
        print_info "  source /opt/rh/rh-python39/enable"
        print_info ""
        print_info "On Ubuntu/Debian:"
        print_info "  apt install python3.9 python3.9-venv python3.9-dev"
        exit 1
    fi
    
    local python_version=$(python3 -c "import sys; print(sys.version_info.major * 100 + sys.version_info.minor)")
    
    if [ "$python_version" -lt 309 ]; then
        local actual_version=$(python3 --version 2>&1 | head -1)
        print_error "Python version too old: $actual_version"
        print_error "Open ACE requires Python 3.9 or later"
        print_info ""
        print_info "On CentOS/RHEL 7, you can install Python 3.9 via Software Collections:"
        print_info "  yum install centos-release-scl"
        print_info "  yum install rh-python39 rh-python39-python-pip"
        print_info "  source /opt/rh/rh-python39/enable  # Activate Python 3.9"
        print_info "  Then run this install script again"
        print_info ""
        print_info "On Rocky Linux 8/9 or RHEL 8/9:"
        print_info "  dnf install python39 python39-pip"
        print_info "  alternatives --set python3 /usr/bin/python3.9"
        print_info ""
        print_info "On Ubuntu/Debian:"
        print_info "  apt install python3.9 python3.9-venv python3.9-dev"
        print_info "  update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.9 1"
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
        print_info "Open ACE requires Python 3.9 or later"
        return 1
    fi
    
    local python_version=$(ssh "$remote" "python3 -c \"import sys; print(sys.version_info.major * 100 + sys.version_info.minor)\"")
    
    if [ "$python_version" -lt 309 ]; then
        local actual_version=$(ssh "$remote" "python3 --version 2>&1 | head -1")
        print_error "Python version too old on $remote: $actual_version"
        print_error "Open ACE requires Python 3.9 or later"
        return 1
    fi
    
    local actual_version=$(ssh "$remote" "python3 --version 2>&1 | head -1")
    print_success "Python version on $remote: $actual_version (OK)"
    return 0
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

        # Ask for connection details
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
        export _WS_ENABLED="$WORKSPACE_MULTI_USER_MODE"
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
config['workspace']['max_instances'] = int(os.environ.get('_WS_MAX_INSTANCES', '20'))
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

# Systemd service settings
SERVICE_PORT=""       # Web server port (will be read from config or use default)
SERVICE_HOST="0.0.0.0" # Web server host

# Multi-user workspace mode settings
WORKSPACE_MULTI_USER_MODE="true"
WORKSPACE_PORT_RANGE_START="3100"
WORKSPACE_PORT_RANGE_END="3200"
WORKSPACE_MAX_INSTANCES="20"
WORKSPACE_IDLE_TIMEOUT="30"
WORKSPACE_URL=""      # Workspace URL (will be set based on host_name or server_url)

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

# Configure sudoers for multi-user workspace mode
configure_sudoers() {
    local run_user="$1"

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
    local sudoers_content="# Open ACE WebUI - Multi-user mode sudo configuration
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Allows the service account to run qwen-code-webui as other users
# and perform file system operations as other users

$run_user ALL=(ALL) NOPASSWD: $webui_path *
$run_user ALL=(ALL) NOPASSWD: /usr/bin/test, /usr/bin/ls, /usr/bin/cat, /usr/bin/stat, /usr/bin/mkdir
"

    # Check if sudoers file already exists
    if [ -f "$sudoers_file" ]; then
        if grep -q "$webui_path" "$sudoers_file" 2>/dev/null; then
            print_success "Sudoers rule already exists"
            return 0
        fi
        print_info "Updating existing sudoers file..."
    fi

    # Write sudoers file
    echo "$sudoers_content" > "$sudoers_file"
    chmod 440 "$sudoers_file"

    # Validate sudoers syntax
    if visudo -c -f "$sudoers_file" &>/dev/null; then
        print_success "Sudoers configured successfully: $sudoers_file"
        print_info "Service account '$run_user' can execute:"
        print_info "  sudo -u <username> $webui_path --port <port>"
    else
        print_error "Sudoers syntax error, rolling back..."
        rm -f "$sudoers_file"
        return 1
    fi

    return 0
}

# ============================================================================
# Systemd Service Functions
# ============================================================================

get_web_port_from_config() {
    local config_file="$HOME/.open-ace/config.json"
    local default_port="5000"

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
    local port="${3:-5000}"
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
        print_info "You can manually run the web server with: cd $target_path && python3 web.py"
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

    # Create service file from template
    print_info "Creating systemd service file..."
    sed -e "s|__USER__|$user|g" \
        -e "s|__GROUP__|$group|g" \
        -e "s|__INSTALL_PATH__|$target_path|g" \
        -e "s|__PORT__|$port|g" \
        -e "s|__HOST__|$host|g" \
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
    local port="${4:-5000}"
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
        print_info "You can manually run the web server with: ssh $remote 'cd $target_path && python3 web.py'"
        return 0
    fi

    # Get user's primary group
    local group=$(id -gn "$user")

    # Generate service file content locally using sed
    local service_content=$(sed -e "s|__USER__|$user|g" \
        -e "s|__GROUP__|$group|g" \
        -e "s|__INSTALL_PATH__|$target_path|g" \
        -e "s|__PORT__|$port|g" \
        -e "s|__HOST__|$host|g" \
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

interactive_config() {
    print_header "Open ACE - Installation Configuration"

    # Ask for installation mode
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
            configure_local
            ;;
        2)
            INSTALL_MODE="deploy"
            # Initialize deployment user (remote will be checked later)
            init_deploy_user "false"
            configure_deploy
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

    prompt_input "Deployment user" "$DEPLOY_USER" DEPLOY_USER
    prompt_input "Deployment path" "$DEPLOY_PATH" DEPLOY_PATH

    # Remove trailing slash from path to avoid double slashes
    DEPLOY_PATH="${DEPLOY_PATH%/}"

    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        # Get default port from config or use 5000
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
    prompt_input "Deployment path" "/home/$DEPLOY_USER/open-ace" DEPLOY_PATH

    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service on remote?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        prompt_input "Web server port" "5000" SERVICE_PORT
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

    # Check Python version first (Open ACE requires Python >= 3.9)
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

    # Setup PostgreSQL (detect or install)
    setup_postgresql

    # Check if already installed (must have web.py to be considered valid installation)
    if [ -d "$target_path" ] && [ -f "$target_path/web.py" ]; then
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

    # Configure sudoers for multi-user workspace mode
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        # Stop existing qwen-code-webui systemd service first
        stop_webui_systemd_service
        configure_sudoers "$DEPLOY_USER"
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
        echo "  cd $target_path && python3 web.py"
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

    # Check Python version on remote system (Open ACE requires Python >= 3.9)
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

    # Check if already installed (must have web.py to be considered valid installation)
    if ssh "$remote" "[ -d '$target_path' ] && [ -f '$target_path/web.py' ]"; then
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
        echo "  ssh $remote 'cd $target_path && python3 web.py'"
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
        
        # Create temp requirements excluding psycopg2-binary (use system package instead)
        TEMP_REQ=$(mktemp)
        grep -v "psycopg2-binary" "$target_path/requirements.txt" > "$TEMP_REQ" || true
        chmod 644 "$TEMP_REQ"  # Make readable for non-root users

        # Install dependencies (prefer vendor directory for offline install)
        if [ -d "$target_path/vendor" ] && [ "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            print_info "Installing from vendor directory (offline mode)..."
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user --no-index --find-links="$target_path/vendor" -r "$TEMP_REQ" && print_success "Dependencies installed from vendor"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user --no-index --find-links="$target_path/vendor" -r "$TEMP_REQ" && print_success "Dependencies installed from vendor"
            fi
        else
            # Install from network
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user -r "$TEMP_REQ" && print_success "Dependencies installed with pip3"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user -r "$TEMP_REQ" && print_success "Dependencies installed with pip"
            fi
        fi
        rm -f "$TEMP_REQ"
    fi

    # Initialize database schema
    print_info "Initializing database schema..."
    
    # Determine database type and execute appropriate schema
    local db_type="postgresql"
    if [ -f "$config_dir/config.json" ]; then
        db_type=$(python3 -c "import json; c=json.load(open('$config_dir/config.json')); print(c.get('database', {}).get('type', 'postgresql'))")
    fi
    
    local schema_file=""
    if [ "$db_type" = "postgresql" ]; then
        schema_file="$target_path/schema/schema-postgres.sql"
    else
        schema_file="$target_path/schema/schema-sqlite.sql"
    fi
    
    if [ -f "$schema_file" ]; then
        print_info "Executing schema: $schema_file"
        if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
            # Running as root, execute as install_user
            cd "$target_path"
            if [ "$db_type" = "postgresql" ]; then
                # Get database connection info from config
                local db_url=$(python3 -c "import json; c=json.load(open('$config_dir/config.json')); print(c.get('database', {}).get('url', ''))")
                if [ -n "$db_url" ]; then
                    # Parse database URL (use BRE syntax for compatibility)
                    local db_host=$(echo "$db_url" | sed -n 's/.*@\([^:]*\):.*/\1/p')
                    local db_port=$(echo "$db_url" | sed -n 's/.*:\([0-9]*\)\/.*/\1/p')
                    local db_name=$(echo "$db_url" | sed -n 's/.*\/\([^?]*\).*/\1/p')
                    local db_user=$(echo "$db_url" | sed -n 's/.*\/\/\([^:@]*\):.*/\1/p')
                    local db_pass=$(echo "$db_url" | sed -n 's/.*:\/\/[^:]*:\([^@]*\)@.*/\1/p')
                    
                    if su - "$install_user" -c "cd '$target_path' && PGPASSWORD='$db_pass' psql -h '$db_host' -p '$db_port' -U '$db_user' -d '$db_name' -f '$schema_file'"; then
                        print_success "Database schema created"
                    else
                        print_warning "Failed to execute schema. You may need to run it manually."
                    fi
                else
                    print_warning "Database URL not found in config, skipping schema execution"
                fi
            else
                # SQLite - execute directly
                if su - "$install_user" -c "cd '$target_path' && python3 -c \"import sqlite3; c=sqlite3.connect('$config_dir/openace.db'); c.executescript(open('$schema_file').read())\""; then
                    print_success "Database schema created"
                else
                    print_warning "Failed to execute SQLite schema"
                fi
            fi
            cd - > /dev/null
        else
            # Running as target user
            cd "$target_path"
            if [ "$db_type" = "postgresql" ]; then
                local db_url=$(python3 -c "import json; c=json.load(open('$config_dir/config.json')); print(c.get('database', {}).get('url', ''))")
                if [ -n "$db_url" ]; then
                    # Parse database URL (use BRE syntax for compatibility)
                    local db_host=$(echo "$db_url" | sed -n 's/.*@\([^:]*\):.*/\1/p')
                    local db_port=$(echo "$db_url" | sed -n 's/.*:\([0-9]*\)\/.*/\1/p')
                    local db_name=$(echo "$db_url" | sed -n 's/.*\/\([^?]*\).*/\1/p')
                    local db_user=$(echo "$db_url" | sed -n 's/.*\/\/\([^:@]*\):.*/\1/p')
                    local db_pass=$(echo "$db_url" | sed -n 's/.*:\/\/[^:]*:\([^@]*\)@.*/\1/p')
                    
                    if PGPASSWORD="$db_pass" psql -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" -f "$schema_file"; then
                        print_success "Database schema created"
                    else
                        print_warning "Failed to execute schema"
                    fi
                fi
            else
                python3 -c "import sqlite3; c=sqlite3.connect('$config_dir/openace.db'); c.executescript(open('$schema_file').read())"
                print_success "Database schema created"
            fi
            cd - > /dev/null
        fi
    else
        print_warning "Schema file not found: $schema_file"
    fi
    
    # Mark alembic version as head (skip running migrations)
    print_info "Marking database version..."
    if [ -f "$target_path/alembic.ini" ] && [ -d "$target_path/migrations" ]; then
        if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
            cd "$target_path"
            if su - "$install_user" -c "cd '$target_path' && python3 -m alembic stamp head"; then
                print_success "Database version marked as current"
            else
                print_warning "Failed to stamp database version. You may need to run 'alembic stamp head' manually."
            fi
            cd - > /dev/null
        else
            cd "$target_path"
            if python3 -m alembic stamp head; then
                print_success "Database version marked as current"
            else
                print_warning "Failed to stamp database version"
            fi
            cd - > /dev/null
        fi
    else
        print_warning "Alembic not found, skipping version stamp"
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

        # Update files (preserve logs, data, and config)
        print_info "Updating files..."
        # Remove old files except logs, data, and config directory
        local config_basename=$(basename "$config_dir")
        find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name "$config_basename" -exec rm -rf {} +
        # Copy new files
        cp -r "$SOURCE_DIR"/* "$target_path/"

        # Set permissions
        chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
        chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true
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

    # Update workspace configuration (for both new and upgrade modes)
    if [ -f "$config_dir/config.json" ]; then
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
    fi

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
        # Create temp requirements excluding psycopg2-binary (use system package instead)
        TEMP_REQ=$(mktemp)
        grep -v "psycopg2-binary" "$target_path/requirements.txt" > "$TEMP_REQ" || true
        chmod 644 "$TEMP_REQ"  # Make readable for non-root users

        if [ -d "$target_path/vendor" ] && [ "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            print_info "Installing from vendor directory (offline mode)..."
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user --no-index --find-links="$target_path/vendor" -r "$TEMP_REQ" && print_success "Dependencies installed from vendor"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user --no-index --find-links="$target_path/vendor" -r "$TEMP_REQ" && print_success "Dependencies installed from vendor"
            fi
        else
            # Install from network
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user -r "$TEMP_REQ" && print_success "Dependencies installed with pip3"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user -r "$TEMP_REQ" && print_success "Dependencies installed with pip"
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
        # Exclude psycopg2-binary (use system package instead)
        TEMP_REQ=\$(mktemp)
        grep -v 'psycopg2-binary' requirements.txt > \$TEMP_REQ || true
        if [ -d 'vendor' ] && [ \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing from vendor directory (offline mode)...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-index --find-links=vendor -r \$TEMP_REQ
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-index --find-links=vendor -r \$TEMP_REQ
            fi
        else
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user -r \$TEMP_REQ
            elif command -v pip >/dev/null 2>&1; then
                pip install --user -r \$TEMP_REQ
            fi
        fi
        rm -f \$TEMP_REQ
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

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
    ssh "$remote" "cd '$target_path' && find . -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' ! -name '.open-ace' -exec rm -rf {} +"
    scp -r "$SOURCE_DIR"/* "$remote:$target_path/"

    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"

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
        # Exclude psycopg2-binary (use system package instead)
        TEMP_REQ=\$(mktemp)
        grep -v 'psycopg2-binary' requirements.txt > \$TEMP_REQ || true
        if [ -d 'vendor' ] && [ \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing from vendor directory (offline mode)...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-index --find-links=vendor -r \$TEMP_REQ
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-index --find-links=vendor -r \$TEMP_REQ
            fi
        else
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user -r \$TEMP_REQ
            elif command -v pip >/dev/null 2>&1; then
                pip install --user -r \$TEMP_REQ
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

    print_success "Remote upgrade completed"
    print_info "Backup saved to: $backup_dir on $DEPLOY_HOST"
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
    echo "  SERVICE_PORT=5000                # Web server port"
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
    echo "  WORKSPACE_MAX_INSTANCES=20       # Max concurrent instances"
    echo "  WORKSPACE_IDLE_TIMEOUT=30        # Idle timeout (minutes)"
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