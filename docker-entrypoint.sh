#!/bin/bash
# Docker entrypoint script for Open ACE
# Initializes database and starts the application

set -e

# Check if we're running a specific command (not the app)
if [ "$1" != "" ] && [ "$1" != "gunicorn" ]; then
    # Running a custom command (e.g., alembic, python script)
    exec "$@"
fi

echo "Starting Open ACE..."

# Run database migrations if DATABASE_URL is set and database is not initialized
if [ -n "$DATABASE_URL" ]; then
    # Check if alembic_version table exists (indicates migrations have been run)
    if ! python3 -c "import psycopg2; conn = psycopg2.connect('$DATABASE_URL'); cur = conn.cursor(); cur.execute(\"SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'\"); exit(0 if cur.fetchone() else 1)" 2>/dev/null; then
        echo "Running database migrations..."
        alembic upgrade head
        echo "Migrations completed."
    else
        echo "Database already initialized, skipping migrations."
    fi
fi

exec gunicorn \
    --bind 0.0.0.0:5001 \
    --workers 2 \
    --threads 4 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    "app:create_app()"