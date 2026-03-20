#!/usr/bin/env python3
"""Initialize database on remote machine."""

import sys
sys.path.insert(0, '/opt/open-ace/scripts/shared')

from db import init_database

if __name__ == '__main__':
    init_database()
