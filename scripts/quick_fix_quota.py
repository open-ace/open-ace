"""Quick script to fix the detected abnormal quota."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.repositories.user_repo import UserRepository

user_repo = UserRepository()

# Fix 黄迎春's abnormal monthly_token_quota
# Set to max value (2147M)
user_repo.update_user_quota(1, monthly_token_quota=2147)

print("Fixed user quota successfully")
