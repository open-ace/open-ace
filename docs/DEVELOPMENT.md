# Development Guide

> **ACE** = **AI Computing Explorer**

This guide covers setting up a development environment and contributing to Open ACE.

## Development Setup

### Prerequisites

- Python 3.9+
- Git
- A code editor (VS Code, PyCharm, etc.)

### Setup Steps

```bash
# Clone the repository
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install pytest pytest-cov playwright

# Initialize configuration
python3 cli.py config init
```

## Project Structure

```
open-ace/
├── cli.py              # CLI entry point
├── web.py              # Web server entry point
├── requirements.txt    # Python dependencies
│
├── scripts/            # Core scripts
│   ├── fetch_*.py      # Data collection scripts
│   ├── shared/         # Shared modules
│   │   ├── config.py   # Configuration
│   │   ├── db.py       # Database operations
│   │   └── utils.py    # Utilities
│   └── migrations/     # Database migrations
│
├── templates/          # HTML templates (Jinja2)
├── static/             # Static files (CSS, JS)
│   ├── css/
│   └── js/
│
├── tests/              # Test files
│   ├── issues/         # Issue-specific tests
│   └── ui/             # UI tests
│
└── docs/               # Documentation
```

## Code Style

We follow [PEP 8](https://pep8.org/) style guidelines:

- Use 4 spaces for indentation
- Maximum line length: 100 characters
- Use meaningful variable and function names
- Add docstrings to functions and classes

### Example

```python
def get_daily_usage(date: str, tool_name: str = None) -> dict:
    """
    Get token usage for a specific date.

    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Optional tool filter

    Returns:
        Dictionary with usage statistics
    """
    conn = get_connection()
    cursor = conn.cursor()

    if tool_name:
        cursor.execute(
            "SELECT * FROM daily_usage WHERE date = ? AND tool_name = ?",
            (date, tool_name)
        )
    else:
        cursor.execute(
            "SELECT * FROM daily_usage WHERE date = ?",
            (date,)
        )

    return cursor.fetchall()
```

## Testing

### Run Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_db.py

# Run with coverage
pytest --cov=scripts/shared tests/
```

### Test Organization

```
tests/
├── conftest.py         # Shared fixtures
├── test_config.py      # Config module tests
├── test_db.py          # Database tests
├── test_utils.py       # Utility tests
├── issues/             # Issue-specific tests
│   ├── 15/
│   ├── 20/
│   └── ...
└── ui/                 # UI tests (Playwright)
    ├── test_screenshot.py
    └── ...
```

### Writing Tests

```python
import pytest
from scripts.shared import db

def test_get_connection():
    """Test database connection."""
    conn = db.get_connection()
    assert conn is not None
    conn.close()

def test_get_daily_usage():
    """Test daily usage query."""
    result = db.get_daily_usage("2026-03-21")
    assert isinstance(result, list)
```

## UI Testing with Playwright

### Setup

```bash
# Install Playwright
pip install playwright

# Install browsers
playwright install chromium
```

### Running UI Tests

```bash
# Run UI tests
pytest tests/ui/

# Run specific test
pytest tests/ui/test_screenshot.py
```

### Example UI Test

```python
import asyncio
from playwright.async_api import async_playwright

async def test_login():
    """Test login functionality."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Navigate to login
        await page.goto('http://localhost:5000/login')

        # Fill form
        await page.fill('#username', 'admin')
        await page.fill('#password', 'admin123')
        await page.click('button[type="submit"]')

        # Wait for redirect
        await page.wait_for_url('http://localhost:5000/')

        await browser.close()
```

## Database Migrations

Database schema migrations use Alembic. See `migrations/` directory for migration files.

```bash
# Run migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"
```

### Data Migration

For data migration scripts (e.g., SQLite to PostgreSQL), see `scripts/utils/`:

```bash
# Migrate from SQLite to PostgreSQL
python3 scripts/utils/migrate_to_postgres.py
```

## Adding a New Data Source

To add support for a new AI tool:

1. Create `scripts/fetch_newtool.py`
2. Implement log parsing logic
3. Add to configuration template
4. Add tests

### Template

```python
#!/usr/bin/env python3
"""Fetch usage data from NewTool."""

import os
import sys
from pathlib import Path

# Add shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shared'))

import db
import utils

def fetch_newtool(days: int = 7):
    """Fetch NewTool usage data."""
    log_path = Path.home() / '.newtool' / 'logs'

    for log_file in log_path.glob('*.jsonl'):
        # Parse log file
        # Extract token usage
        # Save to database
        pass

if __name__ == '__main__':
    fetch_newtool()
```

## Debugging

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Database Inspection

```bash
# Open database
sqlite3 ~/.open-ace/usage.db

# Query tables
.tables
.schema daily_usage
SELECT * FROM daily_usage LIMIT 10;
```

## Release Process

1. Update `VERSION` file
2. Update `CHANGELOG.md`
3. Create git tag
4. Build release package

```bash
# Build release
./scripts/release.sh --version 1.1.0
```

## Getting Help

- Check existing documentation in `docs/`
- Search existing issues on GitHub
- Open a new issue for bugs or feature requests