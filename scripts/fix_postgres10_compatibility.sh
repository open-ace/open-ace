#!/bin/bash
# 修复 PostgreSQL 10 兼容性问题的脚本
# 问题: baseline SQL 使用了 PostgreSQL 12+ 的参数 default_table_access_method
# 解决: 注释掉这个参数

set -e

echo "=== PostgreSQL 10 兼容性修复脚本 ==="

# 检查是否在正确的目录
if [ ! -f "schema/baselines/baseline_2026_06_23-postgres.sql" ]; then
    echo "错误: 请在 Open ACE 根目录下运行此脚本"
    exit 1
fi

# 备份原文件
backup_file="schema/baselines/baseline_2026_06_23-postgres.sql.backup_$(date +%Y%m%d_%H%M%S)"
cp schema/baselines/baseline_2026_06_23-postgres.sql "$backup_file"
echo "✓ 已备份原文件到: $backup_file"

# 注释掉不兼容的参数
sed -i 's/^SET default_table_access_method = heap;$/-- SET default_table_access_method = heap;  -- PostgreSQL 12+ only, commented for compatibility with PostgreSQL 10/' \
    schema/baselines/baseline_2026_06_23-postgres.sql

echo "✓ 已修改 SQL 文件，注释掉 default_table_access_method 参数"
echo ""
echo "现在可以重新运行安装脚本了！"