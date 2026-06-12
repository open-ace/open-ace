"""
检查配额数据脚本
查找异常配额值（超过上限或异常数值）
"""

import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, "/Users/rhuang/workspace/open-ace")

from app import create_app
from app.repositories.user_repo import UserRepository
from app.schemas.quota import MAX_TOKEN_QUOTA, MAX_REQUEST_QUOTA

app = create_app()
with app.app_context():
    user_repo = UserRepository()
    users = user_repo.get_all_users()

    print("=" * 80)
    print("配额数据检查报告")
    print("=" * 80)

    # 统计数据
    total_users = len(users)
    abnormal_users = []

    for user in users:
        user_id = user["id"]
        username = user["username"]

        # 检查各个配额字段
        quotas = {
            "daily_token_quota": user.get("daily_token_quota"),
            "monthly_token_quota": user.get("monthly_token_quota"),
            "daily_request_quota": user.get("daily_request_quota"),
            "monthly_request_quota": user.get("monthly_request_quota"),
        }

        issues = []

        # 检查 token quotas
        for field in ["daily_token_quota", "monthly_token_quota"]:
            value = quotas[field]
            if value is not None:
                if value > MAX_TOKEN_QUOTA:
                    issues.append(f"{field}: {value}M (超过上限 {MAX_TOKEN_QUOTA}M)")
                elif value < 0:
                    issues.append(f"{field}: {value}M (负值)")

        # 检查 request quotas
        for field in ["daily_request_quota", "monthly_request_quota"]:
            value = quotas[field]
            if value is not None:
                if value > MAX_REQUEST_QUOTA:
                    issues.append(f"{field}: {value} (超过上限 {MAX_REQUEST_QUOTA})")
                elif value < 0:
                    issues.append(f"{field}: {value} (负值)")

        if issues:
            abnormal_users.append(
                {"id": user_id, "username": username, "email": user.get("email"), "issues": issues}
            )

    # 输出报告
    print(f"\n总用户数: {total_users}")
    print(f"异常用户数: {len(abnormal_users)}")

    if abnormal_users:
        print("\n" + "=" * 80)
        print("异常用户详情:")
        print("=" * 80)
        for user in abnormal_users:
            print(f"\n用户 ID: {user['id']}")
            print(f"用户名: {user['username']}")
            print(f"邮箱: {user['email']}")
            print("问题:")
            for issue in user["issues"]:
                print(f"  - {issue}")
    else:
        print("\n✅ 所有配额数据都在正常范围内")

    # 配额分布统计
    print("\n" + "=" * 80)
    print("配额分布统计:")
    print("=" * 80)

    # Token quota 分布
    daily_token_values = [
        u.get("daily_token_quota") for u in users if u.get("daily_token_quota") is not None
    ]
    monthly_token_values = [
        u.get("monthly_token_quota") for u in users if u.get("monthly_token_quota") is not None
    ]

    print(f"\n设置 daily_token_quota 的用户数: {len(daily_token_values)}")
    if daily_token_values:
        print(f"  最小值: {min(daily_token_values)}M")
        print(f"  最大值: {max(daily_token_values)}M")
        print(f"  平均值: {sum(daily_token_values)/len(daily_token_values):.2f}M")

    print(f"\n设置 monthly_token_quota 的用户数: {len(monthly_token_values)}")
    if monthly_token_values:
        print(f"  最小值: {min(monthly_token_values)}M")
        print(f"  最大值: {max(monthly_token_values)}M")
        print(f"  平均值: {sum(monthly_token_values)/len(monthly_token_values):.2f}M")

    # Request quota 分布
    daily_request_values = [
        u.get("daily_request_quota") for u in users if u.get("daily_request_quota") is not None
    ]
    monthly_request_values = [
        u.get("monthly_request_quota") for u in users if u.get("monthly_request_quota") is not None
    ]

    print(f"\n设置 daily_request_quota 的用户数: {len(daily_request_values)}")
    if daily_request_values:
        print(f"  最小值: {min(daily_request_values)}")
        print(f"  最大值: {max(daily_request_values)}")
        print(f"  平均值: {sum(daily_request_values)/len(daily_request_values):.0f}")

    print(f"\n设置 monthly_request_quota 的用户数: {len(monthly_request_values)}")
    if monthly_request_values:
        print(f"  最小值: {min(monthly_request_values)}")
        print(f"  最大值: {max(monthly_request_values)}")
        print(f"  平均值: {sum(monthly_request_values)/len(monthly_request_values):.0f}")

    print("\n" + "=" * 80)
