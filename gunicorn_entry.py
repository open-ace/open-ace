#!/usr/bin/env python3
"""Gunicorn entry point wrapper for Open ACE.

This wrapper monkey-patches gevent BEFORE gunicorn (or any transitive dep)
imports urllib3. This prevents urllib3.util.ssl_ RecursionError under the
gevent event loop during LLM proxy outbound requests.

See: Issue #1900 - safe_request recursion in gevent environment
"""

import sys

# ============================================================================
# Monkey-patch gevent BEFORE any other imports
# ============================================================================
# Must be the first thing before any urllib3 or related imports
try:
    import gevent.monkey
    gevent.monkey.patch_all()
except ImportError:
    pass

# Now import gunicorn and run it
import re
import os

from gunicorn.app.wsgiapp import run

if __name__ == "__main__":
    # Pass remaining arguments to gunicorn
    sys.argv[0] = re.sub(r'(-script/.*\.py|-\$)', 'gunicorn', sys.argv[0])
    run()
