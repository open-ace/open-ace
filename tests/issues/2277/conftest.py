"""Test configuration for Issue #2277 scheduler worker tests.

Handles gevent initialization for tests that import scheduler_worker.
"""
from __future__ import annotations

import os

# Must be set before importing scheduler_worker
os.environ["SCHEDULER_MODE"] = "scheduler"

# Apply gevent monkey patch before any other imports to avoid lock issues
try:
    from gevent import monkey

    monkey.patch_all()
except ImportError:
    pass  # gevent not available in some test environments