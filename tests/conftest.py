#!/usr/bin/env python3
"""
Pytest fixtures for Open ACE tests.

IMPORTANT: All database tests use isolated temporary databases.
NO test data should ever be written to the production database.
"""

import pytest
import asyncio
import tempfile
import os
import sys
import sqlite3

# Add scripts/shared to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
shared_path = os.path.join(project_root, 'scripts', 'shared')
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)

# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


class TestConfig:
    """Test configuration that mimics the real config module."""
    def __init__(self, temp_dir):
        self.CONFIG_DIR = temp_dir
        self.CONFIG_PATH = os.path.join(temp_dir, 'config.json')
        self.DB_DIR = temp_dir
        self.DB_PATH = os.path.join(temp_dir, 'test_ace.db')
        self.REMOTE_USER = 'test_user'
        self.REMOTE_CONFIG_DIR = '/tmp/test-config'
        self.REMOTE_DB_PATH = '/tmp/test-config/ace.db'


@pytest.fixture
def isolated_db(tmp_path):
    """
    Create a completely isolated database for testing.
    
    This fixture ensures tests NEVER touch the production database.
    It creates a fresh database in a temporary directory for each test.
    """
    temp_dir = str(tmp_path)
    
    # Create a test config
    test_config = TestConfig(temp_dir)
    
    # Import db module and override its paths
    import db as db_module
    import config as config_module
    
    # Store original values
    original_db_dir = db_module.DB_DIR
    original_db_path = db_module.DB_PATH
    original_config_dir = config_module.CONFIG_DIR
    original_config_db_dir = config_module.DB_DIR
    original_config_db_path = config_module.DB_PATH
    
    # Override with test paths
    db_module.DB_DIR = temp_dir
    db_module.DB_PATH = test_config.DB_PATH
    config_module.CONFIG_DIR = temp_dir
    config_module.DB_DIR = temp_dir
    config_module.DB_PATH = test_config.DB_PATH
    
    # Also override the config reference inside db module
    db_module.config = test_config
    
    try:
        # Initialize the test database
        db_module.init_database()
        
        # Verify we're using the test database, not production
        assert db_module.DB_PATH == test_config.DB_PATH
        assert not db_module.DB_PATH.endswith('.open-ace/ace.db')
        
        yield db_module
        
    finally:
        # Restore original values
        db_module.DB_DIR = original_db_dir
        db_module.DB_PATH = original_db_path
        config_module.CONFIG_DIR = original_config_dir
        config_module.DB_DIR = original_config_db_dir
        config_module.DB_PATH = original_config_db_path
        
        # Close any open connections
        try:
            conn = sqlite3.connect(test_config.DB_PATH)
            conn.close()
        except:
            pass


@pytest.fixture
def sample_usage_data():
    """Sample usage data for testing."""
    return {
        'date': '2026-03-09',
        'tool_name': 'test_tool',
        'tokens_used': 1000,
        'input_tokens': 800,
        'output_tokens': 200,
        'cache_tokens': 100,
        'request_count': 5,
        'models_used': ['gpt-4', 'claude-3'],
        'host_name': 'test-host'
    }


@pytest.fixture
def sample_message_data():
    """Sample message data for testing."""
    return {
        'date': '2026-03-09',
        'tool_name': 'test_tool',
        'message_id': 'msg_001',
        'parent_id': None,
        'role': 'user',
        'content': 'Hello, this is a test message.',
        'tokens_used': 50,
        'input_tokens': 40,
        'output_tokens': 10,
        'model': 'gpt-4',
        'timestamp': '2026-03-09T10:30:00Z',
        'sender_id': 'user_001',
        'sender_name': 'Test User',
        'host_name': 'test-host'
    }


# =============================================================================
# Playwright Fixtures for UI Tests
# =============================================================================

# Test configuration for UI tests
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
TEST_USERNAME = os.environ.get('TEST_USERNAME', 'admin')
TEST_PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'


@pytest.fixture(scope="session")
def browser_type_launch_args():
    """Browser launch arguments."""
    return {
        "headless": HEADLESS,
    }


@pytest.fixture(scope="session")
def browser_context_args():
    """Browser context arguments."""
    return {
        "viewport": {"width": 1280, "height": 900},
    }


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def playwright_browser():
    """Create a browser instance for the test session."""
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        yield browser
        await browser.close()


@pytest.fixture
async def page(playwright_browser):
    """Create a new page for each test."""
    context = await playwright_browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    page.set_default_timeout(10000)
    
    yield page
    
    await context.close()


@pytest.fixture
async def logged_in_page(page):
    """Create a page that is already logged in."""
    await page.goto(f"{BASE_URL}/login")
    await page.wait_for_load_state('networkidle')
    
    await page.fill('input[name="username"]', TEST_USERNAME)
    await page.fill('input[name="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    # Wait for redirect
    await page.wait_for_url(lambda url: '/login' not in url, timeout=15000)
    await page.wait_for_load_state('networkidle')
    
    yield page


@pytest.fixture
def test_base_url():
    """Return the base URL for tests."""
    return BASE_URL


@pytest.fixture
def test_credentials():
    """Return test credentials."""
    return {
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
    }


@pytest.fixture(params=[
    ("admin", "admin123", "Admin"),
    ("testuser", "testuser", "NormalUser"),
])
def user_credentials(request):
    """Parametrized user credentials for testing different user types."""
    username, password, user_type = request.param
    return {
        "username": username,
        "password": password,
        "user_type": user_type,
    }