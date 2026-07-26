"""Pytest config for Issue #1828 tests.

Inserts this directory onto ``sys.path`` at collection time so the sibling
``_helpers`` module is importable from every test file without per-file
``sys.path`` hacks (and without an ``E402`` import-order lint).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
