"""
Open ACE - Constants Module

Application-wide constants and configuration values.
"""

from app.constants.role_groups import ROLE_GROUPS, normalize_role_to_group

__all__ = ["ROLE_GROUPS", "normalize_role_to_group"]