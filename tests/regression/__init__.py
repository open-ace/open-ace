"""回归测试模块

测试文件命名规范：
    test_模式_一级菜单_二级菜单.py
    例如：test_manage_governance_audit.py

测试覆盖：
    - 登录功能 (test_login.py)
    - 导航功能 (test_navigation.py)

    Manage 模式：
    - Overview: Dashboard
    - Analysis: Trend, Anomaly, ROI, Conversation History, Messages
    - Governance: Audit, Quota, Compliance, Security
    - Users: Management, Tenants
    - Settings: SSO

    Work 模式：
    - Workspace, Sessions, Prompts
"""

# 登录和导航
from .test_login import run_all_tests as run_login_tests
from .test_manage_analysis_anomaly import run_all_tests as run_manage_analysis_anomaly_tests
from .test_manage_analysis_conversation_history import (
    run_all_tests as run_manage_analysis_conversation_history_tests,
)
from .test_manage_analysis_messages import run_all_tests as run_manage_analysis_messages_tests
from .test_manage_analysis_roi import run_all_tests as run_manage_analysis_roi_tests

# Manage 模式 - Analysis
from .test_manage_analysis_trend import run_all_tests as run_manage_analysis_trend_tests

# Manage 模式 - Governance
from .test_manage_governance_audit import run_all_tests as run_manage_governance_audit_tests
from .test_manage_governance_compliance import (
    run_all_tests as run_manage_governance_compliance_tests,
)
from .test_manage_governance_quota import run_all_tests as run_manage_governance_quota_tests
from .test_manage_governance_security import run_all_tests as run_manage_governance_security_tests

# Manage 模式 - Overview
from .test_manage_overview_dashboard import run_all_tests as run_manage_overview_dashboard_tests

# Manage 模式 - Settings
from .test_manage_settings_sso import run_all_tests as run_manage_settings_sso_tests

# Manage 模式 - Users
from .test_manage_users_management import run_all_tests as run_manage_users_management_tests
from .test_manage_users_tenants import run_all_tests as run_manage_users_tenants_tests
from .test_navigation import run_all_tests as run_navigation_tests
from .test_work_prompts import run_all_tests as run_work_prompts_tests
from .test_work_sessions import run_all_tests as run_work_sessions_tests

# Work 模式
from .test_work_workspace import run_all_tests as run_work_workspace_tests
