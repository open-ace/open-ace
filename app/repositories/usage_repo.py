"""
Open ACE - Usage Repository

Repository for usage data access operations.
"""

import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, cast

from app.repositories.database import Database, escape_like, is_postgresql
from app.utils.hostname_validator import get_hostname_filter_sql, is_valid_hostname
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)